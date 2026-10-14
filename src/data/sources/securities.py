"""sources — securities: data acquisition and normalization."""

from __future__ import annotations
import datetime as dt
import json
import logging
import re
from concurrent.futures import ThreadPoolExecutor
from typing import Dict
import pandas as pd
import requests
from ashare_alpha.data.paths import PARQUET_ROOT as PARQUET, STATE_ROOT as STATE
from ashare_alpha.data.runtime import UA

try:
    import akshare as ak
except ImportError:
    ak = None
from ashare_alpha.data.storage import atomic_json
from ashare_alpha.data.storage import atomic_parquet
from ashare_alpha.data.runtime import retry

CNINFO_STOCK_URL = "https://www.cninfo.com.cn/new/data/szse_stock.json"


def exchange_for(code: str) -> str:
    if code.startswith(("4", "8", "92")):
        return "BJ"
    if code.startswith(("6", "9")):
        return "SH"
    return "SZ"


def is_a_share(code: str) -> bool:
    if not re.fullmatch(r"\d{6}", code):
        return False
    return code.startswith(
        (
            "000",
            "001",
            "002",
            "003",
            "300",
            "301",
            "302",
            "600",
            "601",
            "603",
            "605",
            "688",
            "689",
            "4",
            "8",
            "920",
        )
    )


def fetch_cninfo_map(session: requests.Session) -> pd.DataFrame:
    response = session.get(CNINFO_STOCK_URL, timeout=20)
    response.raise_for_status()
    rows = response.json().get("stockList", [])
    result = pd.DataFrame(rows)
    if result.empty:
        return pd.DataFrame(columns=["code", "org_id", "cninfo_name"])
    result = result[result["category"].eq("A股")].copy()
    return result.rename(columns={"orgId": "org_id", "zwjc": "cninfo_name"})[
        ["code", "org_id", "cninfo_name"]
    ]


def fetch_beijing_list() -> pd.DataFrame:
    """Read every official BSE page with explicit request deadlines."""
    rows = []
    expected_pages = None
    with requests.Session() as session:
        session.headers.update({"User-Agent": UA})
        page = 0
        while True:
            response = session.post(
                "https://www.bse.cn/nqxxController/nqxxCnzq.do",
                data={
                    "page": str(page),
                    "typejb": "T",
                    "xxfcbj[]": "2",
                    "xxzqdm": "",
                    "sortfield": "xxzqdm",
                    "sorttype": "asc",
                },
                timeout=(5, 20),
            )
            response.raise_for_status()
            text = response.text
            data = json.loads(text[text.find("[") : text.rfind("]") + 1])[0]
            pages = int(data["totalPages"])
            if pages < 1 or (expected_pages is not None and pages != expected_pages):
                raise RuntimeError("BSE listing pagination changed during fetch")
            expected_pages = pages
            items = data["content"]
            if not items:
                raise RuntimeError(f"BSE listing page {page} is empty")
            rows.extend(items)
            page += 1
            if page >= expected_pages:
                break
    frame = pd.DataFrame(rows).rename(
        columns={"xxzqdm": "code", "xxzqjc": "current_name", "fxssrq": "list_date"}
    )
    frame["code"] = frame["code"].astype(str).str.zfill(6)
    frame["list_date"] = pd.to_datetime(frame["list_date"], errors="coerce")
    return frame[["code", "current_name", "list_date"]]


def fetch_market_list() -> pd.DataFrame:
    """Sina's current A-share list includes suspended names and all exchanges."""
    base = "https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/Market_Center."
    with requests.Session() as session:
        session.headers.update({"User-Agent": UA})
        response = session.get(
            base + "getHQNodeStockCount", params={"node": "hs_a"}, timeout=(5, 20)
        )
        response.raise_for_status()
        expected = int(response.json())
        if expected < 1000:
            raise RuntimeError(f"Incomplete A-share count: {expected}")
        pages = (expected + 99) // 100

        def fetch(page):
            r = requests.get(
                base + "getHQNodeData",
                params={
                    "page": page,
                    "num": 100,
                    "sort": "symbol",
                    "asc": 1,
                    "node": "hs_a",
                    "symbol": "",
                },
                headers={"User-Agent": UA},
                timeout=(5, 20),
            )
            r.raise_for_status()
            data = r.json()
            if not isinstance(data, list) or not data:
                raise RuntimeError(f"A-share list page {page} is invalid")
            return data

        with ThreadPoolExecutor(max_workers=4) as pool:
            rows = [
                row for batch in pool.map(fetch, range(1, pages + 1)) for row in batch
            ]
    frame = pd.DataFrame(rows)
    if len(frame) != expected or frame["code"].duplicated().any():
        raise RuntimeError("A-share listing count/keys changed during pagination")
    frame["code"] = frame["code"].astype(str).str.zfill(6)
    frame = frame[frame["code"].map(is_a_share)]
    return frame.rename(columns={"name": "current_name"})[["code", "current_name"]]


def fetch_exchange_listings() -> pd.DataFrame:
    """Official listings retain names omitted from quote feeds during suspension."""
    frames = [fetch_beijing_list()]
    if ak is not None:
        try:
            sz = ak.stock_info_sz_name_code(symbol="A股列表").rename(
                columns={
                    "A股代码": "code",
                    "A股上市日期": "list_date",
                    "A股简称": "current_name",
                }
            )
            frames.append(sz[["code", "list_date", "current_name"]])
        except Exception as exc:
            logging.warning("SZSE listing dates unavailable: %s", exc)
    result = pd.concat(frames, ignore_index=True)
    result["code"] = result["code"].astype(str).str.zfill(6)
    result["list_date"] = pd.to_datetime(result["list_date"], errors="coerce")
    return result.dropna(subset=["list_date"]).drop_duplicates("code", keep="last")


def fetch_current_listing_dates() -> pd.DataFrame:
    return fetch_exchange_listings()[["code", "list_date"]]


def fetch_current_universe() -> pd.DataFrame:
    frame = fetch_market_list()
    dates = fetch_exchange_listings()
    frame = pd.concat(
        [frame, dates[["code", "current_name"]]], ignore_index=True
    ).drop_duplicates("code")
    frame = frame.merge(dates[["code", "list_date"]], on="code", how="left")
    prior_path = PARQUET / "security_master/security_master.parquet"
    prior = pd.read_parquet(prior_path) if prior_path.exists() else pd.DataFrame()
    if not prior.empty:
        # Quote feeds can omit listed CDRs from their A-share node. Verify each
        # historical CDR against a current quote before retaining it as listed.
        omitted = prior[
            prior.code.str.startswith("689")
            & ~prior.code.isin(frame.code)
            & ~prior.status.eq("delisted")
        ]
        supplements = []
        for row in omitted.itertuples(index=False):
            response = requests.get(
                "https://qt.gtimg.cn/q=sh" + row.code, timeout=(5, 15)
            )
            response.raise_for_status()
            fields = response.text.split("~")
            if (
                len(fields) < 38
                or fields[2] != row.code
                or "退" in fields[1]
                or not str(fields[30])[:8].isdigit()
            ):
                raise RuntimeError(f"Unable to verify current CDR listing: {row.code}")
            supplements.append(
                {
                    "code": row.code,
                    "current_name": fields[1],
                    "list_date": row.list_date,
                }
            )
        if supplements:
            frame = pd.concat([frame, pd.DataFrame(supplements)], ignore_index=True)
        prior_dates = prior.set_index("code")["list_date"]
        frame["list_date"] = frame["list_date"].fillna(frame["code"].map(prior_dates))
    frame["exchange"] = frame["code"].map(exchange_for)
    missing = frame[frame["list_date"].isna()]
    if not missing.empty:
        import baostock as bs
        from .indicators import baostock_frame

        login = bs.login()
        if login.error_code != "0":
            raise RuntimeError(login.error_msg)
        try:
            for code in missing["code"]:
                prefix = {"SH": "sh", "SZ": "sz", "BJ": "bj"}[exchange_for(code)]
                basic = baostock_frame(bs.query_stock_basic(code=f"{prefix}.{code}"))
                if not basic.empty:
                    frame.loc[frame.code.eq(code), "list_date"] = pd.to_datetime(
                        basic.iloc[0]["ipoDate"], errors="coerce"
                    )
        finally:
            bs.logout()
    if frame["list_date"].isna().any():
        raise RuntimeError(
            f"Unresolved listing dates: {frame.loc[frame.list_date.isna(), 'code'].tolist()[:20]}"
        )
    return frame


def fetch_delisted_universe() -> pd.DataFrame:
    if ak is None:
        logging.warning("AkShare unavailable; delisted universe cannot be loaded")
        return pd.DataFrame()
    frames = []
    try:
        response = requests.get(
            "https://query.sse.com.cn/commonQuery.do",
            params={
                "sqlId": "COMMON_SSE_CP_GPJCTPZ_GPLB_GP_L",
                "isPagination": "true",
                "STOCK_TYPE": "1,2,8",
                "COMPANY_STATUS": "3",
                "type": "inParams",
                "pageHelp.pageSize": "10000",
                "pageHelp.pageNo": "1",
            },
            headers={"User-Agent": UA, "Referer": "https://www.sse.com.cn/"},
            timeout=(5, 15),
        )
        response.raise_for_status()
        sh = pd.DataFrame(response.json()["result"]).rename(
            columns={
                "COMPANY_CODE": "code",
                "COMPANY_ABBR": "name",
                "LIST_DATE": "list_date",
                "DELIST_DATE": "delist_date",
            }
        )
        sh["exchange"] = "SH"
        frames.append(sh[["code", "name", "list_date", "delist_date", "exchange"]])
    except Exception as exc:  # noqa: BLE001
        logging.warning("Unable to load SSE delisted list: %s", exc)
    try:
        sz = ak.stock_info_sz_delist(symbol="终止上市公司").rename(
            columns={
                "证券代码": "code",
                "证券简称": "name",
                "上市日期": "list_date",
                "终止上市日期": "delist_date",
            }
        )
        sz["exchange"] = "SZ"
        frames.append(sz[["code", "name", "list_date", "delist_date", "exchange"]])
    except Exception as exc:  # noqa: BLE001
        logging.warning("Unable to load SZSE delisted list: %s", exc)
    if not frames:
        return pd.DataFrame()
    result = pd.concat(frames, ignore_index=True)
    result["code"] = result["code"].astype(str).str.zfill(6)
    return result[result["code"].map(is_a_share)].drop_duplicates("code")


def build_universe(config: Dict) -> pd.DataFrame:
    session = requests.Session()
    session.headers.update({"User-Agent": UA})
    current = retry(fetch_current_universe, "current A-share universe")
    current = current.rename(columns={"current_name": "name"})
    current["status"] = "listed"
    current["list_date"] = pd.to_datetime(current["list_date"], errors="coerce")
    current["delist_date"] = pd.NaT

    delisted = (
        fetch_delisted_universe() if config["include_delisted"] else pd.DataFrame()
    )
    if not delisted.empty:
        delisted["status"] = "delisted"

    combined = pd.concat([current, delisted], ignore_index=True)
    prior_path = PARQUET / "security_master/security_master.parquet"
    prior = pd.read_parquet(prior_path) if prior_path.exists() else pd.DataFrame()
    if not prior.empty:
        old_history = prior[~prior["status"].eq("listed")].drop(
            columns=["org_id", "cninfo_name", "source"], errors="ignore"
        )
        combined = pd.concat([combined, old_history], ignore_index=True)
        # A historical member cannot vanish just because a free list omits it.
        absent = prior[prior.status.eq("listed") & ~prior.code.isin(combined.code)]
        if not absent.empty:
            import baostock as bs
            from .indicators import baostock_frame

            login = bs.login()
            if login.error_code != "0":
                raise RuntimeError(login.error_msg)
            resolved = []
            try:
                for row in absent.itertuples(index=False):
                    prefix = {"SH": "sh", "SZ": "sz", "BJ": "bj"}[row.exchange]
                    basic = (
                        baostock_frame(
                            bs.query_stock_basic(code=f"{prefix}.{row.code}")
                        )
                        if row.exchange in ("SH", "SZ")
                        else pd.DataFrame()
                    )
                    item = row._asdict()
                    if not basic.empty and str(basic.iloc[0]["outDate"]).strip():
                        item["status"] = "delisted"
                        item["delist_date"] = pd.Timestamp(basic.iloc[0]["outDate"])
                    else:
                        # Preserve historical assets without inventing a delisting date.
                        item["status"] = "inactive"
                        item["delist_date"] = pd.NaT
                        logging.warning(
                            "Not in current listings, retained as inactive: %s",
                            row.code,
                        )
                    resolved.append(item)
            finally:
                bs.logout()
            combined = pd.concat(
                [
                    combined,
                    pd.DataFrame(resolved).drop(
                        columns=["org_id", "cninfo_name", "source"], errors="ignore"
                    ),
                ],
                ignore_index=True,
            )
    combined = combined.sort_values(
        ["code", "status"],
        key=lambda s: (
            s.map({"listed": 0, "delisted": 1}).fillna(2) if s.name == "status" else s
        ),
    ).drop_duplicates("code", keep="first")

    try:
        cninfo = retry(lambda: fetch_cninfo_map(session), "CNInfo identifier map")
        combined = combined.merge(cninfo, on="code", how="left")
        combined["name"] = combined["name"].fillna(combined["cninfo_name"])
    except Exception as exc:  # noqa: BLE001
        logging.warning("CNInfo map unavailable: %s", exc)
        combined["org_id"] = None
        combined["cninfo_name"] = None

    if not prior.empty:
        old = prior.set_index("code")
        for field in ("org_id", "cninfo_name"):
            combined[field] = combined[field].fillna(combined["code"].map(old[field]))
    combined["exchange"] = combined["code"].map(exchange_for)
    combined["list_date"] = pd.to_datetime(combined["list_date"], errors="coerce")
    combined["delist_date"] = pd.to_datetime(combined["delist_date"], errors="coerce")
    combined["source"] = "sina_current+bse+szse+baostock+cninfo+tencent_cdr"

    # Keep manually purged no-bar securities out of the research universe.
    purge_path = STATE / "purged_no_bars_securities.json"
    if purge_path.exists():
        try:
            purged = set(
                json.loads(purge_path.read_text(encoding="utf-8")).get("codes", [])
            )
        except Exception as exc:  # noqa: BLE001
            logging.warning("Unable to load purged securities list: %s", exc)
            purged = set()
        if purged:
            before = len(combined)
            combined = combined[~combined["code"].astype(str).isin(purged)].copy()
            logging.info(
                "Excluded %s purged no-bar securities from universe (%s -> %s)",
                before - len(combined),
                before,
                len(combined),
            )

    combined = combined.sort_values(["exchange", "code"]).reset_index(drop=True)
    path = PARQUET / "security_master" / "security_master.parquet"
    atomic_parquet(combined, path)
    logging.info(
        "Security master saved: %s securities (%s listed, %s delisted)",
        len(combined),
        int(combined["status"].eq("listed").sum()),
        int(combined["status"].eq("delisted").sum()),
    )
    return combined


def build_listing_dates(config: Dict) -> None:
    """Enrich the existing security master without rebuilding other fields."""
    del config
    path = PARQUET / "security_master" / "security_master.parquet"
    if not path.exists():
        raise RuntimeError("security master is missing; run universe first")
    master = pd.read_parquet(path)
    listing_dates = retry(
        fetch_current_listing_dates,
        "current exchange listing dates",
    ).rename(columns={"list_date": "exchange_list_date"})
    master = master.merge(listing_dates, on="code", how="left")
    listed_mask = master["status"].eq("listed")
    master.loc[listed_mask, "list_date"] = master.loc[
        listed_mask, "exchange_list_date"
    ].combine_first(master.loc[listed_mask, "list_date"])
    master["list_date"] = pd.to_datetime(master["list_date"], errors="coerce")
    master = master.drop(
        columns=["exchange_list_date", "bj_list_date"], errors="ignore"
    )
    atomic_parquet(master, path)
    missing = master[listed_mask & master["list_date"].isna()]
    atomic_json(
        {
            "listed_count": int(listed_mask.sum()),
            "resolved_count": int((listed_mask & master["list_date"].notna()).sum()),
            "missing_codes": missing["code"].astype(str).tolist(),
            "updated_at": dt.datetime.now(),
        },
        STATE / "listing_date_coverage.json",
    )
    logging.info(
        "Listing dates resolved: %s/%s listed securities",
        int((listed_mask & master["list_date"].notna()).sum()),
        int(listed_mask.sum()),
    )
