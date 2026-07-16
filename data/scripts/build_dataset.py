#!/usr/bin/env python3
"""Build a resumable, point-in-time-aware A-share research dataset.

Primary data sources follow the a-stock-data skill:
  * mootdx/TongDaXin: SH/SZ daily bars
  * Tencent: current quote/fundamental snapshot
  * Sina: historical financial statements
  * CNInfo: security identifiers and financial-report announcement dates

AkShare is used only as a thin wrapper around exchange/Eastmoney endpoints for
the current and delisted security universe, plus a rate-limited fallback for
symbols unavailable from TongDaXin (notably Beijing and delisted securities).
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import logging
import random
import re
import sys
import threading
import time
import urllib.request
from bisect import bisect_right
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from io import StringIO
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Optional, Sequence, Tuple

import pandas as pd
import requests

try:
    import akshare as ak
except ImportError:
    ak = None

try:
    from mootdx import consts
    from mootdx.quotes import Quotes
except ImportError:
    Quotes = None
    consts = None

try:
    import baostock as bs
except ImportError:
    bs = None


ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "config" / "dataset.json"
PARQUET = ROOT / "parquet"
RAW = ROOT / "raw"
STATE = ROOT / "state"
LOGS = ROOT / "logs"
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
CNINFO_STOCK_URL = "http://www.cninfo.com.cn/new/data/szse_stock.json"
CNINFO_QUERY_URL = "https://www.cninfo.com.cn/new/hisAnnouncement/query"
SINA_FINANCIAL_URL = (
    "https://quotes.sina.cn/cn/api/openapi.php/"
    "CompanyFinanceService.getFinanceReport2022"
)


def setup_logging() -> None:
    LOGS.mkdir(parents=True, exist_ok=True)
    log_path = LOGS / f"build_{dt.date.today().isoformat()}.log"
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler(log_path, encoding="utf-8"),
        ],
    )


def load_config() -> Dict:
    return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))


def atomic_parquet(df: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    df.to_parquet(temp, index=False)
    temp.replace(path)


def atomic_json(data: Dict, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(
        json.dumps(data, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    temp.replace(path)


def retry(
    fn: Callable,
    label: str,
    attempts: int = 3,
    base_sleep: float = 1.0,
):
    last_error = None
    for attempt in range(1, attempts + 1):
        try:
            return fn()
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            if attempt < attempts:
                wait = base_sleep * (2 ** (attempt - 1)) + random.uniform(0, 0.4)
                logging.warning("%s failed (%s/%s): %s", label, attempt, attempts, exc)
                time.sleep(wait)
    raise RuntimeError(f"{label} failed after {attempts} attempts: {last_error}")


class Throttle:
    def __init__(self, min_interval: float):
        self.min_interval = min_interval
        self.last_call = 0.0
        self._lock = threading.Lock()

    def wait(self) -> None:
        with self._lock:
            remaining = self.min_interval - (time.time() - self.last_call)
            if remaining > 0:
                time.sleep(remaining + random.uniform(0.05, 0.2))
            self.last_call = time.time()


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
        ("000", "001", "002", "003", "300", "301", "600", "601", "603",
         "605", "688", "689", "4", "8", "920")
    )


def fetch_cninfo_map(session: requests.Session) -> pd.DataFrame:
    response = session.get(CNINFO_STOCK_URL, timeout=20)
    response.raise_for_status()
    rows = response.json().get("stockList", [])
    result = pd.DataFrame(rows)
    if result.empty:
        return pd.DataFrame(columns=["code", "org_id", "cninfo_name"])
    result = result[result["category"].eq("A股")].copy()
    return result.rename(
        columns={"orgId": "org_id", "zwjc": "cninfo_name"}
    )[["code", "org_id", "cninfo_name"]]


def fetch_tdx_current() -> pd.DataFrame:
    if Quotes is None:
        return pd.DataFrame(columns=["code", "name"])
    client = Quotes.factory(market="std")
    frames = []
    for market in (consts.MARKET_SH, consts.MARKET_SZ):
        frame = client.stocks(market=market)[["code", "name"]].copy()
        frame["exchange"] = "SH" if market == consts.MARKET_SH else "SZ"
        frames.append(frame)
    result = pd.concat(frames, ignore_index=True)
    result["code"] = result["code"].astype(str).str.zfill(6)
    return result[result["code"].map(is_a_share)].drop_duplicates("code")


def fetch_current_listing_dates() -> pd.DataFrame:
    """Fetch current A-share listing dates from exchange bulk lists."""
    if ak is None:
        return pd.DataFrame(columns=["code", "list_date"])
    frames = []
    for board in ("主板A股", "科创板"):
        sh = ak.stock_info_sh_name_code(symbol=board).rename(
            columns={"证券代码": "code", "上市日期": "list_date"}
        )
        frames.append(sh[["code", "list_date"]])
    sz = ak.stock_info_sz_name_code(symbol="A股列表").rename(
        columns={"A股代码": "code", "A股上市日期": "list_date"}
    )
    frames.append(sz[["code", "list_date"]])
    bj = ak.stock_info_bj_name_code().rename(
        columns={"证券代码": "code", "上市日期": "list_date"}
    )
    frames.append(bj[["code", "list_date"]])
    result = pd.concat(frames, ignore_index=True)
    result["code"] = result["code"].astype(str).str.zfill(6)
    result["list_date"] = pd.to_datetime(result["list_date"], errors="coerce")
    return (
        result[result["code"].map(is_a_share)]
        .dropna(subset=["list_date"])
        .drop_duplicates("code", keep="last")
    )


def fetch_current_universe() -> pd.DataFrame:
    if ak is None:
        return fetch_tdx_current()
    frame = ak.stock_info_a_code_name().rename(columns={"name": "current_name"})
    frame["code"] = frame["code"].astype(str).str.zfill(6)
    frame = frame[frame["code"].map(is_a_share)].copy()
    frame["exchange"] = frame["code"].map(exchange_for)
    frames = [frame[["code", "current_name", "exchange"]]]
    try:
        bj = ak.stock_info_bj_name_code().rename(
            columns={
                "证券代码": "code",
                "证券简称": "current_name",
            }
        )
        bj["code"] = bj["code"].astype(str).str.zfill(6)
        bj["exchange"] = "BJ"
        frames.append(bj[["code", "current_name", "exchange"]])
    except Exception as exc:  # noqa: BLE001
        logging.warning("Unable to load Beijing listed universe: %s", exc)
    result = pd.concat(frames, ignore_index=True).drop_duplicates("code")
    try:
        result = result.merge(fetch_current_listing_dates(), on="code", how="left")
    except Exception as exc:  # noqa: BLE001
        logging.warning("Unable to load current listing dates: %s", exc)
        result["list_date"] = pd.NaT
    return result


def fetch_delisted_universe() -> pd.DataFrame:
    if ak is None:
        logging.warning("AkShare unavailable; delisted universe cannot be loaded")
        return pd.DataFrame()
    frames = []
    try:
        sh = ak.stock_info_sh_delist().rename(
            columns={
                "公司代码": "code",
                "公司简称": "name",
                "上市日期": "list_date",
                "暂停上市日期": "delist_date",
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

    delisted = fetch_delisted_universe() if config["include_delisted"] else pd.DataFrame()
    if not delisted.empty:
        delisted["status"] = "delisted"

    combined = pd.concat([current, delisted], ignore_index=True)
    combined = combined.sort_values(
        ["code", "status"], key=lambda s: s.map({"listed": 0, "delisted": 1}).fillna(2)
        if s.name == "status" else s
    ).drop_duplicates("code", keep="first")

    try:
        cninfo = retry(lambda: fetch_cninfo_map(session), "CNInfo identifier map")
        combined = combined.merge(cninfo, on="code", how="left")
        combined["name"] = combined["name"].fillna(combined["cninfo_name"])
    except Exception as exc:  # noqa: BLE001
        logging.warning("CNInfo map unavailable: %s", exc)
        combined["org_id"] = None
        combined["cninfo_name"] = None

    combined["exchange"] = combined["code"].map(exchange_for)
    combined["list_date"] = pd.to_datetime(combined["list_date"], errors="coerce")
    combined["delist_date"] = pd.to_datetime(combined["delist_date"], errors="coerce")
    combined["source"] = "exchange_lists+akshare+cninfo+mootdx"

    # Keep manually purged no-bar securities out of the research universe.
    purge_path = STATE / "purged_no_bars_securities.json"
    if purge_path.exists():
        try:
            purged = set(json.loads(purge_path.read_text(encoding="utf-8")).get("codes", []))
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
    ]
    master["list_date"] = pd.to_datetime(master["list_date"], errors="coerce")
    master = master.drop(columns=["exchange_list_date", "bj_list_date"], errors="ignore")
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


def normalize_tdx_bars(frame: pd.DataFrame, code: str, adjustment: str) -> pd.DataFrame:
    if frame.empty:
        return frame
    result = frame.rename(columns={"vol": "volume"}).copy()
    result["date"] = pd.to_datetime(result["datetime"]).dt.normalize()
    keep = ["date", "open", "high", "low", "close", "volume", "amount"]
    result = result.loc[:, ~result.columns.duplicated()][keep]
    for col in keep[1:]:
        result[col] = pd.to_numeric(result[col], errors="coerce")
    result["code"] = code
    result["exchange"] = exchange_for(code)
    result["adjustment"] = adjustment
    result["source"] = "mootdx"
    return result.sort_values("date").drop_duplicates(["date", "code"])


def fetch_tdx_history(
    client,
    code: str,
    start_date: pd.Timestamp,
    end_date: pd.Timestamp,
    adjustment: str,
    page_size: int,
) -> pd.DataFrame:
    pages = []
    start_offset = 0
    adjust_arg = None if adjustment == "raw" else adjustment
    while True:
        kwargs = {
            "symbol": code,
            "frequency": 9,
            "start": start_offset,
            "offset": page_size,
        }
        if adjust_arg:
            kwargs["adjust"] = adjust_arg
        page = client.bars(**kwargs)
        if page is None or page.empty:
            break
        normalized = normalize_tdx_bars(page, code, adjustment)
        valid_count = int(normalized["date"].notna().sum())
        normalized = normalized.dropna(subset=["date"])
        if normalized.empty:
            break
        pages.append(normalized)
        oldest = normalized["date"].min()
        if oldest <= start_date or valid_count < page_size:
            break
        # mootdx qfq pages include synthetic rows with NaT dates around
        # adjustment-factor boundaries. Counting those rows in the next offset
        # skips valid trading days at every page boundary.
        start_offset += valid_count
        if start_offset > 20000:
            raise RuntimeError(f"pagination guard reached for {code}")
    if not pages:
        return pd.DataFrame()
    result = pd.concat(pages, ignore_index=True)
    return result[
        result["date"].between(start_date, end_date)
    ].sort_values("date").drop_duplicates(["date", "code"])


def fetch_akshare_history(
    code: str,
    start_date: pd.Timestamp,
    end_date: pd.Timestamp,
    adjustment: str,
    throttle: Throttle,
) -> pd.DataFrame:
    if ak is None:
        return pd.DataFrame()
    throttle.wait()
    adjust = "" if adjustment == "raw" else adjustment
    frame = ak.stock_zh_a_hist(
        symbol=code,
        period="daily",
        start_date=start_date.strftime("%Y%m%d"),
        end_date=end_date.strftime("%Y%m%d"),
        adjust=adjust,
    )
    if frame.empty:
        return frame
    rename = {
        "日期": "date", "开盘": "open", "收盘": "close", "最高": "high",
        "最低": "low", "成交量": "volume", "成交额": "amount",
    }
    result = frame.rename(columns=rename)
    result["date"] = pd.to_datetime(result["date"])
    result["code"] = code
    result["exchange"] = exchange_for(code)
    result["adjustment"] = adjustment
    result["source"] = "eastmoney_via_akshare"
    columns = [
        "date", "open", "high", "low", "close", "volume", "amount",
        "code", "exchange", "adjustment", "source",
    ]
    return result[columns].sort_values("date").drop_duplicates(["date", "code"])


def fetch_tencent_history(
    session: requests.Session,
    code: str,
    start_date: pd.Timestamp,
    end_date: pd.Timestamp,
    adjustment: str,
    throttle: Throttle,
) -> pd.DataFrame:
    """Fetch daily bars by calendar year from Tencent, including BJ/delisted."""
    prefix = {"SH": "sh", "SZ": "sz", "BJ": "bj"}[exchange_for(code)]
    symbol = prefix + code
    rows = []
    for year in range(start_date.year, end_date.year + 1):
        throttle.wait()
        adjust = "" if adjustment == "raw" else adjustment
        params = {
            "_var": f"kline_day{adjust}{year}",
            "param": (
                f"{symbol},day,{year}-01-01,{year + 1}-12-31,640,{adjust}"
            ),
            "r": "0.8205512681390605",
        }
        response = session.get(
            "https://proxy.finance.qq.com/ifzqgtimg/appstock/app/newfqkline/get",
            params=params,
            timeout=20,
        )
        response.raise_for_status()
        text = response.text
        if "=" not in text:
            continue
        data = json.loads(text.split("=", 1)[1]).get("data", {}).get(symbol, {})
        values = data.get("day") or data.get("qfqday") or data.get("hfqday") or []
        for item in values:
            if len(item) < 6:
                continue
            amount = None
            if len(item) > 8:
                try:
                    amount = float(item[8]) * 10000
                except (TypeError, ValueError):
                    amount = None
            rows.append({
                "date": item[0],
                "open": item[1],
                "close": item[2],
                "high": item[3],
                "low": item[4],
                "volume": item[5],
                "amount": amount,
            })
    if not rows:
        return pd.DataFrame()
    result = pd.DataFrame(rows)
    result["date"] = pd.to_datetime(result["date"], errors="coerce")
    for column in ("open", "close", "high", "low", "volume", "amount"):
        result[column] = pd.to_numeric(result[column], errors="coerce")
    result["code"] = code
    result["exchange"] = exchange_for(code)
    result["adjustment"] = adjustment
    result["source"] = "tencent"
    return (
        result[result["date"].between(start_date, end_date)]
        .sort_values("date")
        .drop_duplicates(["date", "code"])
    )


def bar_output_path(code: str, adjustment: str) -> Path:
    return PARQUET / "daily_bars" / f"adjustment={adjustment}" / f"{code}.parquet"


def build_bars(
    config: Dict,
    limit: Optional[int] = None,
    codes: Optional[Sequence[str]] = None,
    force: bool = False,
) -> None:
    universe_path = PARQUET / "security_master" / "security_master.parquet"
    universe = pd.read_parquet(universe_path) if universe_path.exists() else build_universe(config)
    if codes:
        wanted = set(codes)
        universe = universe[universe["code"].isin(wanted)]
    # Stable TongDaXin coverage first. Beijing and delisted symbols depend on
    # slower, less reliable fallback endpoints and must not block the main set.
    preferred = universe[
        universe["status"].eq("listed") & universe["exchange"].isin(["SH", "SZ"])
    ]
    fallback = universe.drop(preferred.index)
    universe = pd.concat([preferred, fallback], ignore_index=True)
    if limit:
        universe = universe.head(limit)

    start_date = pd.Timestamp(config["start_date"])
    end_date = pd.Timestamp(config["end_date"] or dt.date.today())
    adjustments = config["bar_adjustments"]
    client = Quotes.factory(market="std") if Quotes is not None else None
    tencent_session = requests.Session()
    tencent_session.headers.update({"User-Agent": UA})
    tencent_throttle = Throttle(0.12)
    eastmoney_throttle = Throttle(config["eastmoney_min_interval_seconds"])
    failures = []
    total_tasks = len(universe) * len(adjustments)
    task_number = 0

    for row in universe.itertuples(index=False):
        code = str(row.code)
        symbol_start = start_date
        symbol_end = end_date
        if pd.notna(row.list_date):
            symbol_start = max(symbol_start, pd.Timestamp(row.list_date))
        if pd.notna(row.delist_date):
            symbol_end = min(symbol_end, pd.Timestamp(row.delist_date))
        if symbol_start > symbol_end:
            continue
        for adjustment in adjustments:
            task_number += 1
            output = bar_output_path(code, adjustment)
            if output.exists() and not force:
                continue
            logging.info("[%s/%s] bars %s %s", task_number, total_tasks, code, adjustment)
            frame = pd.DataFrame()
            try:
                frame = retry(
                    lambda: fetch_tencent_history(
                        tencent_session, code, symbol_start, symbol_end,
                        adjustment, tencent_throttle,
                    ),
                    f"Tencent bars {code} {adjustment}",
                    attempts=config["retries"],
                    base_sleep=config["retry_base_seconds"],
                )
            except Exception as exc:  # noqa: BLE001
                logging.warning("Tencent unavailable for %s: %s", code, exc)
            if frame.empty and client is not None and row.exchange in ("SH", "SZ"):
                try:
                    frame = retry(
                        lambda: fetch_tdx_history(
                            client, code, symbol_start, symbol_end, adjustment,
                            config["bar_page_size"],
                        ),
                        f"mootdx bars {code} {adjustment}",
                        attempts=config["retries"],
                        base_sleep=config["retry_base_seconds"],
                    )
                except Exception as exc:  # noqa: BLE001
                    logging.warning("mootdx unavailable for %s: %s", code, exc)
            if frame.empty:
                try:
                    frame = retry(
                        lambda: fetch_akshare_history(
                            code, symbol_start, symbol_end, adjustment, eastmoney_throttle
                        ),
                        f"fallback bars {code} {adjustment}",
                        attempts=config["retries"],
                        base_sleep=config["retry_base_seconds"],
                    )
                except Exception as exc:  # noqa: BLE001
                    failures.append({"code": code, "adjustment": adjustment, "error": str(exc)})
                    continue
            if frame.empty:
                failures.append({"code": code, "adjustment": adjustment, "error": "empty"})
                continue
            atomic_parquet(frame, output)

    atomic_json({"failures": failures, "updated_at": dt.datetime.now()}, STATE / "bar_failures.json")
    logging.info("Bar download finished with %s failed tasks", len(failures))


def repair_qfq_bars(
    config: Dict,
    limit: Optional[int] = None,
    codes: Optional[Sequence[str]] = None,
) -> None:
    """Rebuild only qfq files whose trading dates differ from raw bars."""
    if Quotes is None:
        raise RuntimeError("mootdx is required; install requirements.txt")
    master = pd.read_parquet(
        PARQUET / "security_master" / "security_master.parquet"
    ).set_index("code")
    wanted = set(codes or [])
    raw_dir = PARQUET / "daily_bars" / "adjustment=raw"
    qfq_dir = PARQUET / "daily_bars" / "adjustment=qfq"
    candidates = []
    for raw_path in sorted(raw_dir.glob("*.parquet")):
        code = raw_path.stem
        if wanted and code not in wanted:
            continue
        qfq_path = qfq_dir / raw_path.name
        if not qfq_path.exists() or code not in master.index:
            continue
        row = master.loc[code]
        if row.exchange not in ("SH", "SZ") or row.status != "listed":
            continue
        raw_dates = pd.read_parquet(raw_path, columns=["date"])["date"]
        qfq_dates = pd.read_parquet(qfq_path, columns=["date"])["date"]
        if not pd.Index(raw_dates).equals(pd.Index(qfq_dates)):
            candidates.append((code, raw_path, qfq_path))
    if limit:
        candidates = candidates[:limit]

    client = Quotes.factory(market="std")
    failures = []
    repaired = 0
    for index, (code, raw_path, qfq_path) in enumerate(candidates, start=1):
        logging.info("[%s/%s] repair qfq %s", index, len(candidates), code)
        raw_dates = pd.read_parquet(raw_path, columns=["date"])["date"]
        start_date = pd.Timestamp(raw_dates.min())
        end_date = pd.Timestamp(raw_dates.max())
        try:
            frame = retry(
                lambda: fetch_tdx_history(
                    client, code, start_date, end_date, "qfq",
                    config["bar_page_size"],
                ),
                f"repair qfq {code}",
                attempts=config["retries"],
                base_sleep=config["retry_base_seconds"],
            )
            if frame.empty:
                raise RuntimeError("empty")
            repaired_dates = pd.Index(frame["date"])
            expected_dates = pd.Index(raw_dates)
            if not repaired_dates.equals(expected_dates):
                raise RuntimeError(
                    "date mismatch remains: "
                    f"raw={len(expected_dates)} qfq={len(repaired_dates)} "
                    f"raw_only={len(expected_dates.difference(repaired_dates))} "
                    f"qfq_only={len(repaired_dates.difference(expected_dates))}"
                )
            atomic_parquet(frame, qfq_path)
            repaired += 1
        except Exception as exc:  # noqa: BLE001
            failures.append({"code": code, "error": str(exc)})
    atomic_json(
        {
            "candidates": len(candidates),
            "repaired": repaired,
            "failures": failures,
            "updated_at": dt.datetime.now(),
        },
        STATE / "qfq_repair.json",
    )
    logging.info(
        "QFQ repair finished: %s/%s repaired, %s failed",
        repaired, len(candidates), len(failures),
    )


def tencent_quotes(codes: Sequence[str]) -> pd.DataFrame:
    prefixed = []
    for code in codes:
        prefix = {"SH": "sh", "SZ": "sz", "BJ": "bj"}[exchange_for(code)]
        prefixed.append(prefix + code)
    url = "https://qt.gtimg.cn/q=" + ",".join(prefixed)
    request = urllib.request.Request(url, headers={"User-Agent": UA})
    text = urllib.request.urlopen(request, timeout=20).read().decode("gbk")
    rows = []
    for line in text.split(";"):
        if '="' not in line:
            continue
        key, payload = line.split('="', 1)
        values = payload.rstrip('"\n').split("~")
        if len(values) < 53:
            continue
        code = key.split("_")[-1][2:]
        def number(index: int):
            try:
                return float(values[index]) if values[index] else None
            except (ValueError, IndexError):
                return None
        rows.append({
            "snapshot_time": dt.datetime.now(),
            "code": code,
            "name": values[1],
            "price": number(3),
            "last_close": number(4),
            "open": number(5),
            "change_pct": number(32),
            "high": number(33),
            "low": number(34),
            "amount_wan": number(37),
            "turnover_pct": number(38),
            "pe_ttm": number(39),
            "amplitude_pct": number(43),
            "market_cap_yi": number(44),
            "float_market_cap_yi": number(45),
            "pb": number(46),
            "limit_up": number(47),
            "limit_down": number(48),
            "volume_ratio": number(49),
            "pe_static": number(52),
            "source": "tencent",
        })
    return pd.DataFrame(rows)


def build_snapshot(config: Dict, limit: Optional[int] = None) -> None:
    universe = pd.read_parquet(PARQUET / "security_master" / "security_master.parquet")
    codes = universe.loc[universe["status"].eq("listed"), "code"].tolist()
    if limit:
        codes = codes[:limit]
    frames = []
    for offset in range(0, len(codes), 50):
        batch = codes[offset:offset + 50]
        frames.append(retry(lambda b=batch: tencent_quotes(b), f"Tencent batch {offset}"))
    result = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    date = dt.date.today().isoformat()
    atomic_parquet(result, PARQUET / "market_snapshots" / f"{date}.parquet")
    logging.info("Tencent snapshot saved: %s rows", len(result))


def fetch_sina_statement(
    session: requests.Session,
    code: str,
    report_type: str,
    periods: int,
    throttle: Throttle,
) -> pd.DataFrame:
    prefix = "sh" if exchange_for(code) == "SH" else "sz"
    throttle.wait()
    response = session.get(
        SINA_FINANCIAL_URL,
        params={
            "paperCode": prefix + code,
            "source": report_type,
            "type": "0",
            "page": "1",
            "num": str(periods),
        },
        timeout=20,
    )
    response.raise_for_status()
    payload = response.json()
    result = payload.get("result") or {}
    data = result.get("data") or {}
    report_list = data.get("report_list") or {}
    def numeric(value):
        if value in (None, "", "--", "-"):
            return None
        try:
            return float(str(value).replace(",", "").replace("%", ""))
        except (TypeError, ValueError):
            return None

    rows = []
    for period, obj in report_list.items():
        for position, item in enumerate(obj.get("data", []) or []):
            title = item.get("item_title")
            if not title:
                continue
            value = numeric(item.get("item_value"))
            year_on_year = numeric(item.get("item_tongbi"))
            item_code = str(item.get("item_field") or "").strip()
            if not item_code and value is None and year_on_year is None:
                # Presentation-only category/header rows are not financial facts.
                continue
            if not item_code:
                item_code = (
                    f"__unmapped__:{report_type}:{position}:"
                    f"{str(title).strip()}"
                )
            rows.append({
                "code": code,
                "report_date": pd.to_datetime(period, format="%Y%m%d", errors="coerce"),
                "statement_type": report_type,
                "item_code": item_code,
                "item": title,
                "item_display": item.get("item_display"),
                "item_group_no": numeric(item.get("item_group_no")),
                "value": value,
                "value_raw": str(item.get("item_value") or ""),
                "year_on_year": year_on_year,
                "source": "sina",
            })
    return pd.DataFrame(rows)


def deduplicate_financial_frame(frame: pd.DataFrame) -> Tuple[pd.DataFrame, int]:
    """Enforce the logical statement key, preferring populated source rows."""
    if frame.empty:
        return frame, 0
    if "item_code" not in frame:
        frame = frame.copy()
        frame["item_code"] = frame["item"].astype(str)
    keys = ["code", "report_date", "statement_type", "item_code"]
    conflict_count = 0
    duplicate_groups = frame[frame.duplicated(keys, keep=False)].groupby(
        keys, dropna=False
    )
    for _, group in duplicate_groups:
        non_null_values = group["value"].dropna().unique()
        if len(non_null_values) > 1:
            conflict_count += 1
    ranked = frame.copy()
    ranked["_has_value"] = ranked["value"].notna().astype("int8")
    ranked["_has_yoy"] = ranked["year_on_year"].notna().astype("int8")
    ranked["_raw_length"] = ranked["value_raw"].fillna("").astype(str).str.len()
    ranked = ranked.sort_values(
        keys + ["_has_value", "_has_yoy", "_raw_length"]
    )
    result = ranked.drop_duplicates(keys, keep="last").drop(
        columns=["_has_value", "_has_yoy", "_raw_length"]
    )
    return result.sort_values(
        ["report_date", "statement_type", "item"],
        ascending=[False, True, True],
    ), conflict_count


def build_financials(
    config: Dict,
    limit: Optional[int] = None,
    codes: Optional[Sequence[str]] = None,
    force: bool = False,
) -> None:
    universe = pd.read_parquet(PARQUET / "security_master" / "security_master.parquet")
    if codes:
        universe = universe[universe["code"].isin(set(codes))]
    if limit:
        universe = universe.head(limit)
    session = requests.Session()
    session.headers.update({"User-Agent": UA})
    throttle = Throttle(config["sina_min_interval_seconds"])
    failures = []
    for index, row in enumerate(universe.itertuples(index=False), start=1):
        code = str(row.code)
        output = PARQUET / "financials" / f"{code}.parquet"
        if output.exists() and not force:
            continue
        logging.info("[%s/%s] financials %s", index, len(universe), code)
        frames = []
        for report_type in ("fzb", "lrb", "llb"):
            try:
                frame = retry(
                    lambda rt=report_type: fetch_sina_statement(
                        session, code, rt, config["financial_periods"], throttle
                    ),
                    f"Sina {report_type} {code}",
                    attempts=config["retries"],
                )
                if not frame.empty:
                    frames.append(frame)
            except Exception as exc:  # noqa: BLE001
                failures.append({"code": code, "type": report_type, "error": str(exc)})
        if frames:
            cleaned, conflicts = deduplicate_financial_frame(
                pd.concat(frames, ignore_index=True)
            )
            if conflicts:
                failures.append({
                    "code": code,
                    "type": "duplicate_value_conflicts",
                    "error": str(conflicts),
                })
            atomic_parquet(cleaned, output)
        else:
            failures.append({"code": code, "type": "all", "error": "empty"})
    atomic_json(
        {"failures": failures, "updated_at": dt.datetime.now()},
        STATE / "financial_failures.json",
    )


def repair_financial_duplicates(config: Dict) -> None:
    """Deduplicate existing financial files without refetching source data."""
    del config
    files = sorted((PARQUET / "financials").glob("*.parquet"))
    changed_files = 0
    removed_rows = 0
    conflicts = []
    for index, path in enumerate(files, start=1):
        frame = pd.read_parquet(path)
        cleaned, conflict_count = deduplicate_financial_frame(frame)
        removed = len(frame) - len(cleaned)
        if removed:
            atomic_parquet(cleaned, path)
            changed_files += 1
            removed_rows += removed
        if conflict_count:
            conflicts.append({"code": path.stem, "conflicting_keys": conflict_count})
        if index % 500 == 0:
            logging.info(
                "Financial dedup progress: %s/%s files, %s rows removed",
                index, len(files), removed_rows,
            )
    atomic_json(
        {
            "files": len(files),
            "changed_files": changed_files,
            "removed_rows": removed_rows,
            "conflicts": conflicts,
            "updated_at": dt.datetime.now(),
        },
        STATE / "financial_deduplication.json",
    )
    logging.info(
        "Financial dedup finished: %s files changed, %s rows removed, "
        "%s files with value conflicts",
        changed_files, removed_rows, len(conflicts),
    )


def cninfo_fallback_org_id(code: str) -> str:
    if exchange_for(code) == "SH":
        return f"gssh0{code}"
    if exchange_for(code) == "BJ":
        return f"gsbj0{code}"
    return f"gssz0{code}"


def resolve_cninfo_org_id(
    session: requests.Session,
    code: str,
    throttle: Throttle,
) -> Optional[str]:
    """Resolve real CNInfo orgId; synthetic IDs silently lose many companies."""
    throttle.wait()
    response = session.post(
        "http://www.cninfo.com.cn/new/information/topSearch/query",
        params={"keyWord": code, "maxNum": 10},
        timeout=20,
    )
    response.raise_for_status()
    for item in response.json() or []:
        if str(item.get("code", "")).zfill(6) == code:
            return item.get("orgId")
    return None


def build_financial_announcements(
    config: Dict,
    limit: Optional[int] = None,
    codes: Optional[Sequence[str]] = None,
    force: bool = False,
    q1_only: bool = False,
) -> None:
    universe = pd.read_parquet(PARQUET / "security_master" / "security_master.parquet")
    universe["code"] = universe["code"].astype(str)
    if codes:
        universe = universe[universe["code"].isin({str(c).zfill(6) for c in codes})]
    if limit:
        universe = universe.head(limit)
    session = requests.Session()
    session.headers.update({
        "User-Agent": UA,
        "Content-Type": "application/x-www-form-urlencoded",
        "Referer": "https://www.cninfo.com.cn/new/disclosure",
        "Origin": "https://www.cninfo.com.cn",
    })
    throttle = Throttle(0.8)
    failures = []
    for index, row in enumerate(universe.itertuples(index=False), start=1):
        code = str(row.code)
        output = PARQUET / "announcements" / f"{code}.parquet"
        if output.exists() and not force:
            if not q1_only:
                continue
            existing = pd.read_parquet(output, columns=["title"])
            if existing["title"].astype(str).str.contains(
                r"(?:第一季度|一季度)报告", regex=True
            ).any():
                continue
        logging.info("[%s/%s] financial announcements %s", index, len(universe), code)
        try:
            org_id = retry(
                lambda: resolve_cninfo_org_id(session, code, throttle),
                f"CNInfo orgId {code}",
                attempts=config["retries"],
            )
        except Exception as exc:  # noqa: BLE001
            failures.append({"code": code, "error": f"orgId: {exc}"})
            continue
        if not org_id:
            # Persist empty marker so coverage stays aligned with master; new
            # listings often lack a CNInfo mapping for a short window.
            empty = pd.DataFrame(
                columns=[
                    "code",
                    "announcement_id",
                    "announcement_date",
                    "title",
                    "category",
                    "adjunct_url",
                    "source",
                ]
            )
            atomic_parquet(empty, output)
            failures.append({"code": code, "error": "orgId: empty"})
            continue
        column = "sse" if row.exchange == "SH" else ("third" if row.exchange == "BJ" else "szse")
        plate = "sh" if row.exchange == "SH" else ("bj" if row.exchange == "BJ" else "sz")
        payload = {
            "stock": f"{code},{org_id}",
            "tabName": "fulltext",
            "pageSize": 30,
            "pageNum": 1,
            "column": column,
            "category": (
                "category_yjdbg_szsh"
                if q1_only
                else (
                    "category_ndbg_szsh;category_bndbg_szsh;"
                    "category_yjdbg_szsh;category_sjdbg_szsh"
                )
            ),
            "plate": plate,
            "seDate": f"{config['start_date']}~{config['end_date'] or dt.date.today()}",
            "searchkey": "",
            "secid": "",
            "sortName": "",
            "sortType": "",
            "isHLtitle": "true",
        }
        try:
            rows = []
            page_number = 1
            while True:
                payload["pageNum"] = page_number
                throttle.wait()
                response = retry(
                    lambda: session.post(CNINFO_QUERY_URL, data=payload, timeout=25),
                    f"CNInfo announcements {code} page {page_number}",
                    attempts=config["retries"],
                )
                response.raise_for_status()
                data = response.json()
                items = data.get("announcements", []) or []
                for item in items:
                    timestamp = item.get("announcementTime")
                    announcement_date = pd.to_datetime(
                        timestamp, unit="ms", errors="coerce", utc=True
                    )
                    if pd.notna(announcement_date):
                        announcement_date = (
                            announcement_date.tz_convert("Asia/Shanghai")
                            .normalize()
                            .tz_localize(None)
                        )
                    rows.append({
                        "code": code,
                        "announcement_id": item.get("announcementId"),
                        "announcement_date": announcement_date,
                        "title": re.sub(r"<[^>]+>", "", item.get("announcementTitle", "")),
                        "category": item.get("announcementTypeName", ""),
                        "adjunct_url": item.get("adjunctUrl", ""),
                        "source": "cninfo",
                    })
                # CNInfo's totalpages is occasionally wrong (for example,
                # totalRecordNum=40 but totalpages=1). hasMore is reliable.
                if not items or not data.get("hasMore", False):
                    break
                page_number += 1
                if page_number > 100:
                    raise RuntimeError(f"pagination guard reached for {code}")
            if rows:
                frame = pd.DataFrame(rows).drop_duplicates("announcement_id")
                if q1_only and output.exists():
                    frame = pd.concat(
                        [pd.read_parquet(output), frame], ignore_index=True
                    ).drop_duplicates("announcement_id", keep="last")
                atomic_parquet(frame, output)
            else:
                # Valid for brand-new listings before the first periodic report.
                empty = pd.DataFrame(
                    columns=[
                        "code",
                        "announcement_id",
                        "announcement_date",
                        "title",
                        "category",
                        "adjunct_url",
                        "source",
                    ]
                )
                if not (q1_only and output.exists()):
                    atomic_parquet(empty, output)
                failures.append({"code": code, "error": "empty"})
        except Exception as exc:  # noqa: BLE001
            failures.append({"code": code, "error": str(exc)})
    atomic_json(
        {"failures": failures, "updated_at": dt.datetime.now()},
        STATE / (
            "announcement_q1_failures.json"
            if q1_only else "announcement_failures.json"
        ),
    )


def report_date_from_announcement_title(title: str) -> Optional[pd.Timestamp]:
    """Parse a periodic-report end date from a CNInfo announcement title."""
    text = re.sub(r"\s+", "", str(title))
    year_match = re.search(r"((?:19|20)\d{2})年", text)
    if not year_match:
        return None
    year = int(year_match.group(1))
    # Prefer more specific quarterly labels before bare "年报".
    if re.search(r"(?:半年度报告|半年报|中期报告|中报)", text):
        return pd.Timestamp(year=year, month=6, day=30)
    if re.search(r"(?:第一季度|一季度|第1季度|一季)报告|一季报", text):
        return pd.Timestamp(year=year, month=3, day=31)
    if re.search(r"(?:第三季度|三季度|第3季度|三季)报告|三季报", text):
        return pd.Timestamp(year=year, month=9, day=30)
    if re.search(r"(?:第四季度|四季度|第4季度)报告|四季报", text):
        return pd.Timestamp(year=year, month=12, day=31)
    if re.search(r"(?:年度报告|年报)", text) and "半年度" not in text and "中期" not in text:
        return pd.Timestamp(year=year, month=12, day=31)
    return None


def build_financial_availability(config: Dict) -> None:
    """Map financial report periods to conservative announcement availability."""
    from research_layer import rebuild_financial_availability

    rebuild_financial_availability(config)


def fetch_corporate_actions(
    session: requests.Session,
    code: str,
    throttle: Throttle,
) -> pd.DataFrame:
    """Fetch dividend, bonus-share and transfer events from Eastmoney."""
    output_columns = [
        "code", "name", "report_date", "plan_notice_date", "publish_date",
        "notice_date", "record_date", "ex_date", "cash_dividend_per_10",
        "bonus_share_per_10", "transfer_share_per_10",
        "bonus_transfer_per_10", "total_shares", "progress",
        "plan_description", "source",
    ]
    rows = []
    page_number = 1
    while True:
        throttle.wait()
        response = session.get(
            "https://datacenter-web.eastmoney.com/api/data/v1/get",
            params={
                "reportName": "RPT_SHAREBONUS_DET",
                "columns": "ALL",
                "filter": f'(SECURITY_CODE="{code}")',
                "pageNumber": str(page_number),
                "pageSize": "100",
                "sortColumns": "EX_DIVIDEND_DATE",
                "sortTypes": "-1",
                "source": "WEB",
                "client": "WEB",
            },
            timeout=20,
        )
        response.raise_for_status()
        payload = response.json()
        if not payload.get("success"):
            message = payload.get("message") or ""
            if message == "返回数据为空":
                return pd.DataFrame(columns=output_columns)
            raise RuntimeError(message or "Eastmoney returned failure")
        result = payload.get("result") or {}
        items = result.get("data") or []
        rows.extend(items)
        total_pages = int(result.get("pages") or 1)
        if not items or page_number >= total_pages:
            break
        page_number += 1
    if not rows:
        return pd.DataFrame(columns=output_columns)

    frame = pd.DataFrame(rows)
    rename = {
        "SECURITY_CODE": "code",
        "SECURITY_NAME_ABBR": "name",
        "REPORT_DATE": "report_date",
        "PLAN_NOTICE_DATE": "plan_notice_date",
        "PUBLISH_DATE": "publish_date",
        "NOTICE_DATE": "notice_date",
        "EQUITY_RECORD_DATE": "record_date",
        "EX_DIVIDEND_DATE": "ex_date",
        "PRETAX_BONUS_RMB": "cash_dividend_per_10",
        "BONUS_RATIO": "bonus_share_per_10",
        "IT_RATIO": "transfer_share_per_10",
        "BONUS_IT_RATIO": "bonus_transfer_per_10",
        "TOTAL_SHARES": "total_shares",
        "ASSIGN_PROGRESS": "progress",
        "IMPL_PLAN_PROFILE": "plan_description",
    }
    frame = frame.rename(columns=rename)
    columns = [
        "code", "name", "report_date", "plan_notice_date", "publish_date",
        "notice_date", "record_date", "ex_date", "cash_dividend_per_10",
        "bonus_share_per_10", "transfer_share_per_10",
        "bonus_transfer_per_10", "total_shares", "progress",
        "plan_description",
    ]
    for column in columns:
        if column not in frame:
            frame[column] = None
    for column in (
        "report_date", "plan_notice_date", "publish_date", "notice_date",
        "record_date", "ex_date",
    ):
        frame[column] = pd.to_datetime(frame[column], errors="coerce")
    for column in (
        "cash_dividend_per_10", "bonus_share_per_10",
        "transfer_share_per_10", "bonus_transfer_per_10", "total_shares",
    ):
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    frame["source"] = "eastmoney"
    return (
        frame[columns + ["source"]]
        .sort_values(["ex_date", "publish_date"], na_position="last")
        .drop_duplicates(
            ["code", "report_date", "ex_date", "plan_description"], keep="last"
        )
    )


def build_corporate_actions(
    config: Dict,
    limit: Optional[int] = None,
    codes: Optional[Sequence[str]] = None,
    force: bool = False,
) -> None:
    universe = pd.read_parquet(PARQUET / "security_master" / "security_master.parquet")
    if codes:
        universe = universe[universe["code"].isin(set(codes))]
    if limit:
        universe = universe.head(limit)
    session = requests.Session()
    session.headers.update({
        "User-Agent": UA,
        "Referer": "https://data.eastmoney.com/",
    })
    throttle = Throttle(config["eastmoney_min_interval_seconds"])
    failures = []
    empty_codes = []
    for index, row in enumerate(universe.itertuples(index=False), start=1):
        code = str(row.code)
        output = PARQUET / "corporate_actions" / f"{code}.parquet"
        if output.exists() and not force:
            continue
        logging.info("[%s/%s] corporate actions %s", index, len(universe), code)
        try:
            frame = retry(
                lambda: fetch_corporate_actions(session, code, throttle),
                f"corporate actions {code}",
                attempts=config["retries"],
                base_sleep=config["retry_base_seconds"],
            )
            if frame.empty:
                empty_codes.append(code)
                # Empty is a valid result for companies without dividend or
                # bonus-share events. Persist it so resumptions do not query
                # the same code indefinitely.
                atomic_parquet(frame, output)
                continue
            atomic_parquet(frame, output)
        except Exception as exc:  # noqa: BLE001
            failures.append({"code": code, "error": str(exc)})
    atomic_json(
        {
            "failures": failures,
            "empty_codes": empty_codes,
            "updated_at": dt.datetime.now(),
        },
        STATE / "corporate_action_failures.json",
    )


def fetch_equity_structure(
    session: requests.Session,
    code: str,
    throttle: Throttle,
) -> pd.DataFrame:
    """Fetch historical total/float share changes from Eastmoney F10 equity."""
    output_columns = [
        "code", "name", "end_date", "notice_date", "change_reason",
        "total_shares", "float_shares", "unlimited_shares", "limited_shares",
        "listed_a_shares", "free_shares", "source",
    ]
    rows = []
    page_number = 1
    while True:
        throttle.wait()
        response = session.get(
            "https://datacenter-web.eastmoney.com/api/data/v1/get",
            params={
                "reportName": "RPT_F10_EH_EQUITY",
                "columns": "ALL",
                "filter": f'(SECURITY_CODE="{code}")',
                "pageNumber": str(page_number),
                "pageSize": "100",
                "sortColumns": "END_DATE",
                "sortTypes": "-1",
                "source": "WEB",
                "client": "WEB",
            },
            timeout=20,
        )
        response.raise_for_status()
        payload = response.json()
        if not payload.get("success"):
            message = payload.get("message") or ""
            if message == "返回数据为空":
                return pd.DataFrame(columns=output_columns)
            raise RuntimeError(message or "Eastmoney returned failure")
        result = payload.get("result") or {}
        items = result.get("data") or []
        rows.extend(items)
        total_pages = int(result.get("pages") or 1)
        if not items or page_number >= total_pages:
            break
        page_number += 1
    if not rows:
        return pd.DataFrame(columns=output_columns)

    frame = pd.DataFrame(rows)
    rename = {
        "SECURITY_CODE": "code",
        "SECURITY_NAME_ABBR": "name",
        "END_DATE": "end_date",
        "NOTICE_DATE": "notice_date",
        "CHANGE_REASON": "change_reason",
        "TOTAL_SHARES": "total_shares",
        "UNLIMITED_SHARES": "unlimited_shares",
        "LIMITED_SHARES": "limited_shares",
        "LISTED_A_SHARES": "listed_a_shares",
        "FREE_SHARES": "free_shares",
    }
    frame = frame.rename(columns=rename)
    for column in (
        "code", "name", "end_date", "notice_date", "change_reason",
        "total_shares", "unlimited_shares", "limited_shares",
        "listed_a_shares", "free_shares",
    ):
        if column not in frame:
            frame[column] = None
    frame["end_date"] = pd.to_datetime(frame["end_date"], errors="coerce")
    frame["notice_date"] = pd.to_datetime(frame["notice_date"], errors="coerce")
    for column in (
        "total_shares", "unlimited_shares", "limited_shares",
        "listed_a_shares", "free_shares",
    ):
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    # Prefer 流通A股; fall back to unlimited shares. Avoid FREE_SHARES alone —
    # for some names it equals total capital rather than tradable A float.
    frame["float_shares"] = frame["listed_a_shares"].where(
        frame["listed_a_shares"].notna() & (frame["listed_a_shares"] > 0),
        frame["unlimited_shares"],
    )
    frame["float_shares"] = frame["float_shares"].where(
        frame["float_shares"].notna() & (frame["float_shares"] > 0),
        pd.NA,
    )
    frame["source"] = "eastmoney_rpt_f10_eh_equity"
    return (
        frame[output_columns]
        .dropna(subset=["end_date"])
        .sort_values("end_date")
        .drop_duplicates(["code", "end_date"], keep="last")
        .reset_index(drop=True)
    )


def build_equity_structure(
    config: Dict,
    limit: Optional[int] = None,
    codes: Optional[Sequence[str]] = None,
    force: bool = False,
) -> None:
    """Download historical capital-structure events (total + float shares)."""
    universe = pd.read_parquet(PARQUET / "security_master" / "security_master.parquet")
    if codes:
        universe = universe[universe["code"].isin(set(codes))]
    if limit:
        universe = universe.head(limit)
    session = requests.Session()
    session.headers.update({
        "User-Agent": UA,
        "Referer": "https://data.eastmoney.com/",
    })
    throttle = Throttle(config["eastmoney_min_interval_seconds"])
    output_dir = PARQUET / "equity_structure"
    output_dir.mkdir(parents=True, exist_ok=True)
    failures = []
    empty_codes = []
    built = 0
    for index, row in enumerate(universe.itertuples(index=False), start=1):
        code = str(row.code)
        output = output_dir / f"{code}.parquet"
        if output.exists() and not force:
            continue
        logging.info("[%s/%s] equity structure %s", index, len(universe), code)
        try:
            frame = retry(
                lambda c=code: fetch_equity_structure(session, c, throttle),
                f"equity structure {code}",
                attempts=config["retries"],
                base_sleep=config["retry_base_seconds"],
            )
            if frame.empty:
                empty_codes.append(code)
                atomic_parquet(frame, output)
                continue
            atomic_parquet(frame, output)
            built += 1
        except Exception as exc:  # noqa: BLE001
            failures.append({"code": code, "error": str(exc)})
        if index % 200 == 0:
            logging.info(
                "equity structure progress: %s/%s built=%s empty=%s failures=%s",
                index, len(universe), built, len(empty_codes), len(failures),
            )
    atomic_json(
        {
            "built": built,
            "empty_codes": empty_codes,
            "failures": failures,
            "updated_at": dt.datetime.now(),
        },
        STATE / "equity_structure_coverage.json",
    )
    logging.info(
        "equity structure finished: built=%s empty=%s failures=%s",
        built, len(empty_codes), len(failures),
    )


def _post_event_total_shares(row: pd.Series) -> Optional[float]:
    """Derive outstanding shares after a bonus/transfer event."""
    total = row.get("total_shares")
    if pd.isna(total):
        return None
    total = float(total)
    bonus = float(row["bonus_share_per_10"]) if pd.notna(row.get("bonus_share_per_10")) else 0.0
    transfer = (
        float(row["transfer_share_per_10"])
        if pd.notna(row.get("transfer_share_per_10"))
        else 0.0
    )
    factor = 1.0 + (bonus + transfer) / 10.0
    return total * factor


def _trading_dates_for_symbol(
    code: str,
    master: pd.DataFrame,
    trading_days: pd.Series,
    start_date: pd.Timestamp,
    end_date: pd.Timestamp,
) -> pd.Index:
    symbol_start = start_date
    symbol_end = end_date
    if code in master.index:
        row = master.loc[code]
        if pd.notna(row.list_date):
            symbol_start = max(symbol_start, pd.Timestamp(row.list_date))
        if pd.notna(row.delist_date):
            symbol_end = min(symbol_end, pd.Timestamp(row.delist_date))
    dates = trading_days[(trading_days >= symbol_start) & (trading_days <= symbol_end)]
    bar_path = PARQUET / "daily_bars" / "adjustment=raw" / f"{code}.parquet"
    if bar_path.exists():
        bar_dates = pd.to_datetime(
            pd.read_parquet(bar_path, columns=["date"])["date"]
        ).dt.normalize()
        return pd.Index(sorted(set(dates).intersection(set(bar_dates))))
    return pd.Index(dates)


def _shares_points_from_equity(equity: pd.DataFrame) -> Optional[pd.DataFrame]:
    frame = equity.dropna(subset=["end_date", "total_shares"]).copy()
    if frame.empty:
        return None
    frame = frame.sort_values("end_date")
    first = frame.iloc[0]
    points = [
        {
            "effective_date": pd.Timestamp("1900-01-01"),
            "total_shares": float(first["total_shares"]),
            "float_shares": (
                float(first["float_shares"])
                if pd.notna(first.get("float_shares"))
                else pd.NA
            ),
        }
    ]
    for row in frame.itertuples(index=False):
        points.append(
            {
                "effective_date": pd.Timestamp(row.end_date),
                "total_shares": float(row.total_shares),
                "float_shares": (
                    float(row.float_shares)
                    if pd.notna(row.float_shares)
                    else pd.NA
                ),
            }
        )
    return (
        pd.DataFrame(points)
        .drop_duplicates("effective_date", keep="last")
        .sort_values("effective_date")
    )


def _shares_points_from_corporate_actions(actions: pd.DataFrame) -> Optional[pd.DataFrame]:
    if actions.empty or "total_shares" not in actions.columns:
        return None
    frame = actions.copy()
    frame["ex_date"] = pd.to_datetime(frame["ex_date"], errors="coerce")
    frame["notice_date"] = pd.to_datetime(frame["notice_date"], errors="coerce")
    frame["effective_date"] = frame["ex_date"].fillna(frame["notice_date"])
    frame = frame.dropna(subset=["effective_date", "total_shares"])
    if "progress" in frame.columns:
        implemented = frame[
            frame["progress"].astype(str).str.contains("实施", na=False)
        ]
        if not implemented.empty:
            frame = implemented
    if frame.empty:
        return None
    frame = frame.sort_values("effective_date")
    frame["post_total_shares"] = frame.apply(_post_event_total_shares, axis=1)
    frame = frame.dropna(subset=["post_total_shares"])
    if frame.empty:
        return None
    first = frame.iloc[0]
    points = [
        {
            "effective_date": pd.Timestamp("1900-01-01"),
            "total_shares": float(first["total_shares"]),
            "float_shares": pd.NA,
        }
    ]
    for row in frame.itertuples(index=False):
        points.append(
            {
                "effective_date": pd.Timestamp(row.effective_date),
                "total_shares": float(row.post_total_shares),
                "float_shares": pd.NA,
            }
        )
    return (
        pd.DataFrame(points)
        .drop_duplicates("effective_date", keep="last")
        .sort_values("effective_date")
    )


def build_shares_daily(
    config: Dict,
    limit: Optional[int] = None,
    codes: Optional[Sequence[str]] = None,
    force: bool = False,
) -> None:
    """Build PIT total/float shares and market caps.

    Prefer Eastmoney equity-structure history for total + float shares; fall
    back to corporate-action total shares when equity history is missing.
    """
    calendar = pd.read_parquet(PARQUET / "reference" / "trade_calendar.parquet")
    trading_days = pd.to_datetime(
        calendar.loc[calendar["is_trading_day"].eq(1), "calendar_date"]
    ).dt.normalize().dropna().sort_values()
    start_date = pd.Timestamp(config["start_date"])
    end_date = pd.Timestamp(config["end_date"] or dt.date.today())
    trading_days = trading_days[
        (trading_days >= start_date) & (trading_days <= end_date)
    ]

    master = pd.read_parquet(
        PARQUET / "security_master" / "security_master.parquet"
    ).set_index("code")
    equity_dir = PARQUET / "equity_structure"
    action_dir = PARQUET / "corporate_actions"
    equity_codes = {path.stem for path in equity_dir.glob("*.parquet")} if equity_dir.exists() else set()
    action_codes = {path.stem for path in action_dir.glob("*.parquet")} if action_dir.exists() else set()
    target_codes = sorted(equity_codes | action_codes)
    if codes:
        wanted = set(codes)
        target_codes = [code for code in target_codes if code in wanted]
    if limit:
        target_codes = target_codes[:limit]

    output_dir = PARQUET / "shares_daily"
    output_dir.mkdir(parents=True, exist_ok=True)
    built = 0
    skipped_empty = 0
    with_float = 0
    failures = []
    for index, code in enumerate(target_codes, start=1):
        output = output_dir / f"{code}.parquet"
        if output.exists() and not force:
            continue
        try:
            points_frame = None
            source_tag = None
            equity_path = equity_dir / f"{code}.parquet"
            if equity_path.exists():
                equity = pd.read_parquet(equity_path)
                if not equity.empty:
                    points_frame = _shares_points_from_equity(equity)
                    source_tag = "equity_structure+raw_bars"
            if points_frame is None:
                action_path = action_dir / f"{code}.parquet"
                if action_path.exists():
                    actions = pd.read_parquet(action_path)
                    points_frame = _shares_points_from_corporate_actions(actions)
                    source_tag = "corporate_actions+raw_bars"
            if points_frame is None or points_frame.empty:
                skipped_empty += 1
                continue

            dates = _trading_dates_for_symbol(
                code, master, trading_days, start_date, end_date
            )
            if len(dates) == 0:
                skipped_empty += 1
                continue

            shares = pd.DataFrame({"date": dates})
            shares = pd.merge_asof(
                shares.sort_values("date"),
                points_frame.rename(columns={"effective_date": "date"}),
                on="date",
                direction="backward",
            )
            shares = shares.dropna(subset=["total_shares"])
            if shares.empty:
                skipped_empty += 1
                continue

            shares["code"] = code
            shares["total_market_cap"] = pd.NA
            shares["float_market_cap"] = pd.NA
            bar_path = PARQUET / "daily_bars" / "adjustment=raw" / f"{code}.parquet"
            if bar_path.exists():
                bars = pd.read_parquet(bar_path, columns=["date", "close"])
                bars["date"] = pd.to_datetime(bars["date"]).dt.normalize()
                shares = shares.merge(bars, on="date", how="left")
                shares["total_market_cap"] = shares["total_shares"] * shares["close"]
                shares["float_market_cap"] = shares["float_shares"] * shares["close"]
                shares = shares.drop(columns=["close"])
            shares["total_shares"] = shares["total_shares"].round().astype("Int64")
            shares["float_shares"] = pd.to_numeric(
                shares["float_shares"], errors="coerce"
            ).round().astype("Int64")
            shares["source"] = source_tag
            if shares["float_shares"].notna().any():
                with_float += 1
            atomic_parquet(
                shares[
                    [
                        "code", "date", "total_shares", "float_shares",
                        "total_market_cap", "float_market_cap", "source",
                    ]
                ].sort_values("date"),
                output,
            )
            built += 1
            if index % 500 == 0:
                logging.info(
                    "shares_daily progress: %s/%s built=%s with_float=%s",
                    index, len(target_codes), built, with_float,
                )
        except Exception as exc:  # noqa: BLE001
            failures.append({"code": code, "error": str(exc)})
    atomic_json(
        {
            "built": built,
            "with_float": with_float,
            "skipped_empty": skipped_empty,
            "failures": failures,
            "updated_at": dt.datetime.now(),
        },
        STATE / "shares_daily_coverage.json",
    )
    logging.info(
        "shares_daily finished: built=%s with_float=%s skipped_empty=%s failures=%s",
        built, with_float, skipped_empty, len(failures),
    )


def _limit_pct_for_row(
    code: str,
    is_st: bool,
    trade_date: pd.Timestamp,
    *,
    list_date: Optional[pd.Timestamp] = None,
    listing_session: Optional[int] = None,
) -> Optional[float]:
    """A-share daily limit ratio by board / ST / registration-system rules."""
    if code.startswith(("8", "4", "92")):
        return 0.30
    is_star = code.startswith(("688", "689"))
    is_chinext = code.startswith(("300", "301"))
    registration_ipo = is_star or (
        is_chinext
        and list_date is not None
        and list_date >= pd.Timestamp("2020-08-24")
    ) or (
        not is_star
        and not is_chinext
        and list_date is not None
        and list_date >= pd.Timestamp("2023-04-10")
    )
    if registration_ipo and listing_session is not None and listing_session <= 5:
        return None
    if is_star:
        return 0.20
    # ChiNext registration system: 20% from 2020-08-24 onward.
    if is_chinext and trade_date >= pd.Timestamp("2020-08-24"):
        return 0.20
    if is_st:
        # Main-board risk-warning shares moved from 5% to 10% on 2026-07-06.
        return 0.10 if trade_date >= pd.Timestamp("2026-07-06") else 0.05
    return 0.10


def build_limit_prices(
    config: Dict,
    limit: Optional[int] = None,
    codes: Optional[Sequence[str]] = None,
    force: bool = False,
) -> None:
    """Derive daily limit-up/down prices from raw close and board/ST rules."""
    del config
    bar_dir = PARQUET / "daily_bars" / "adjustment=raw"
    indicator_dir = PARQUET / "daily_indicators"
    output_dir = PARQUET / "limit_prices"
    output_dir.mkdir(parents=True, exist_ok=True)
    master_path = PARQUET / "security_master" / "security_master.parquet"
    list_dates: Dict[str, pd.Timestamp] = {}
    if master_path.exists():
        master = pd.read_parquet(master_path, columns=["code", "list_date"])
        list_dates = {
            str(row.code).zfill(6): pd.Timestamp(row.list_date).normalize()
            for row in master.itertuples(index=False)
            if pd.notna(row.list_date)
        }
    calendar_path = PARQUET / "reference" / "trade_calendar.parquet"
    market_dates = pd.DatetimeIndex([])
    if calendar_path.exists():
        calendar = pd.read_parquet(calendar_path)
        market_dates = pd.DatetimeIndex(
            pd.to_datetime(
                calendar.loc[calendar["is_trading_day"].astype(bool), "calendar_date"]
            ).dt.normalize()
        ).sort_values()
    bar_files = sorted(bar_dir.glob("*.parquet"))
    if codes:
        wanted = set(codes)
        bar_files = [path for path in bar_files if path.stem in wanted]
    if limit:
        bar_files = bar_files[:limit]

    built = 0
    failures = []
    for index, path in enumerate(bar_files, start=1):
        code = path.stem
        output = output_dir / f"{code}.parquet"
        if output.exists() and not force:
            continue
        try:
            bars = pd.read_parquet(path, columns=["date", "close"]).copy()
            bars["date"] = pd.to_datetime(bars["date"]).dt.normalize()
            bars = bars.sort_values("date").drop_duplicates("date", keep="last")
            bars["prev_close"] = bars["close"].shift(1)
            is_st = pd.Series(False, index=bars.index)
            indicator_path = indicator_dir / f"{code}.parquet"
            if indicator_path.exists():
                indicators = pd.read_parquet(
                    indicator_path, columns=["date", "isST"]
                ).copy()
                indicators["date"] = pd.to_datetime(indicators["date"]).dt.normalize()
                bars = bars.merge(indicators, on="date", how="left")
                is_st = bars["isST"].fillna(0).astype(float).ne(0)
                bars = bars.drop(columns=["isST"])
            list_date = list_dates.get(code)
            if list_date is not None and len(market_dates):
                list_index = int(market_dates.searchsorted(list_date, side="left"))
                listing_sessions = [
                    int(market_dates.searchsorted(date, side="left")) - list_index + 1
                    for date in bars["date"]
                ]
            else:
                listing_sessions = [None] * len(bars)
            limit_pct = [
                _limit_pct_for_row(
                    code,
                    bool(st),
                    date,
                    list_date=list_date,
                    listing_session=listing_session,
                )
                for st, date, listing_session in zip(
                    is_st.tolist(), bars["date"].tolist(), listing_sessions
                )
            ]
            bars["limit_pct"] = pd.to_numeric(
                pd.Series(limit_pct, index=bars.index), errors="coerce"
            )
            bars["limit_up"] = (
                bars["prev_close"] * (1.0 + bars["limit_pct"]) + 1e-8
            ).round(2)
            bars["limit_down"] = (
                bars["prev_close"] * (1.0 - bars["limit_pct"]) + 1e-8
            ).round(2)
            # First day/missing previous close and registration IPO sessions 1-5
            # have no standard daily limit.
            mask = bars["prev_close"].isna() | bars["limit_pct"].isna()
            bars.loc[mask, ["limit_up", "limit_down", "limit_pct"]] = pd.NA
            # Hit flags use 1-cent tolerance for exchange rounding differences.
            bars["is_limit_up"] = (
                bars["close"].notna()
                & bars["limit_up"].notna()
                & ((bars["close"] + 1e-8) >= (bars["limit_up"] - 0.01))
            )
            bars["is_limit_down"] = (
                bars["close"].notna()
                & bars["limit_down"].notna()
                & ((bars["close"] - 1e-8) <= (bars["limit_down"] + 0.01))
            )
            bars.loc[mask, ["is_limit_up", "is_limit_down"]] = False
            bars["code"] = code
            bars["source"] = "rule_derived+raw_bars"
            atomic_parquet(
                bars[
                    [
                        "code", "date", "prev_close", "close", "limit_up",
                        "limit_down", "limit_pct", "is_limit_up", "is_limit_down",
                        "source",
                    ]
                ],
                output,
            )
            built += 1
            if index % 500 == 0:
                logging.info(
                    "limit_prices progress: %s/%s built=%s",
                    index, len(bar_files), built,
                )
        except Exception as exc:  # noqa: BLE001
            failures.append({"code": code, "error": str(exc)})
    atomic_json(
        {
            "built": built,
            "failures": failures,
            "updated_at": dt.datetime.now(),
        },
        STATE / "limit_prices_coverage.json",
    )
    logging.info(
        "limit_prices finished: built=%s failures=%s", built, len(failures),
    )


def build_bj_daily_indicators(
    config: Dict,
    limit: Optional[int] = None,
    codes: Optional[Sequence[str]] = None,
    force: bool = False,
) -> None:
    """Derive BJ daily turn/pctChg from raw bars + float shares.

    BaoStock does not cover Beijing Exchange. Valuation fields (pe/pb/ps/pcf)
    remain null; turn uses amount / float_market_cap when available.
    """
    del config
    universe = pd.read_parquet(PARQUET / "security_master" / "security_master.parquet")
    universe = universe[universe["exchange"].eq("BJ")].copy()
    if codes:
        universe = universe[universe["code"].isin(set(codes))]
    if limit:
        universe = universe.head(limit)

    output_dir = PARQUET / "daily_indicators"
    output_dir.mkdir(parents=True, exist_ok=True)
    built = 0
    skipped = 0
    failures = []
    for index, row in enumerate(universe.itertuples(index=False), start=1):
        code = str(row.code)
        output = output_dir / f"{code}.parquet"
        if output.exists() and not force:
            continue
        bar_path = PARQUET / "daily_bars" / "adjustment=raw" / f"{code}.parquet"
        if not bar_path.exists():
            skipped += 1
            continue
        try:
            bars = pd.read_parquet(
                bar_path, columns=["date", "close", "amount"]
            ).copy()
            bars["date"] = pd.to_datetime(bars["date"]).dt.normalize()
            bars = bars.sort_values("date").drop_duplicates("date", keep="last")
            bars["pctChg"] = bars["close"].pct_change() * 100.0
            shares_path = PARQUET / "shares_daily" / f"{code}.parquet"
            if shares_path.exists():
                shares = pd.read_parquet(
                    shares_path,
                    columns=["date", "float_market_cap", "float_shares"],
                ).copy()
                shares["date"] = pd.to_datetime(shares["date"]).dt.normalize()
                bars = bars.merge(shares, on="date", how="left")
                bars["turn"] = (
                    bars["amount"] / bars["float_market_cap"] * 100.0
                ).where(bars["float_market_cap"].gt(0))
            else:
                bars["turn"] = pd.NA
            frame = pd.DataFrame(
                {
                    "date": bars["date"],
                    "code": code,
                    "turn": bars["turn"],
                    "tradestatus": 1,
                    "pctChg": bars["pctChg"],
                    "peTTM": pd.NA,
                    "pbMRQ": pd.NA,
                    "psTTM": pd.NA,
                    "pcfNcfTTM": pd.NA,
                    "isST": 0,
                    "source": "derived_bars_shares_bj",
                }
            )
            atomic_parquet(frame, output)
            built += 1
            if index % 100 == 0:
                logging.info(
                    "bj indicators progress: %s/%s built=%s",
                    index, len(universe), built,
                )
        except Exception as exc:  # noqa: BLE001
            failures.append({"code": code, "error": str(exc)})
    atomic_json(
        {
            "built": built,
            "skipped": skipped,
            "failures": failures,
            "updated_at": dt.datetime.now(),
        },
        STATE / "bj_indicators_coverage.json",
    )
    logging.info(
        "bj indicators finished: built=%s skipped=%s failures=%s",
        built, skipped, len(failures),
    )


def baostock_frame(result) -> pd.DataFrame:
    rows = []
    while result.error_code == "0" and result.next():
        rows.append(result.get_row_data())
    if result.error_code != "0":
        raise RuntimeError(f"BaoStock {result.error_code}: {result.error_msg}")
    return pd.DataFrame(rows, columns=result.fields)


def build_daily_indicators(
    config: Dict,
    limit: Optional[int] = None,
    codes: Optional[Sequence[str]] = None,
    force: bool = False,
) -> None:
    """Build daily tradability, ST and valuation fields for SH/SZ securities."""
    if bs is None:
        raise RuntimeError("baostock is required; install requirements.txt")
    universe = pd.read_parquet(PARQUET / "security_master" / "security_master.parquet")
    universe = universe[universe["exchange"].isin(["SH", "SZ"])].copy()
    if codes:
        universe = universe[universe["code"].isin(set(codes))]
    if limit:
        universe = universe.head(limit)

    login = bs.login()
    if login.error_code != "0":
        raise RuntimeError(f"BaoStock login failed: {login.error_msg}")
    failures = []
    fields = (
        "date,code,turn,tradestatus,pctChg,peTTM,pbMRQ,"
        "psTTM,pcfNcfTTM,isST"
    )
    start_date = pd.Timestamp(config["start_date"])
    end_date = pd.Timestamp(config["end_date"] or dt.date.today())
    try:
        for index, row in enumerate(universe.itertuples(index=False), start=1):
            code = str(row.code)
            output = PARQUET / "daily_indicators" / f"{code}.parquet"
            if output.exists() and not force:
                continue
            symbol_start = start_date
            symbol_end = end_date
            if pd.notna(row.list_date):
                symbol_start = max(symbol_start, pd.Timestamp(row.list_date))
            if pd.notna(row.delist_date):
                symbol_end = min(symbol_end, pd.Timestamp(row.delist_date))
            if symbol_start > symbol_end:
                continue
            logging.info("[%s/%s] daily indicators %s", index, len(universe), code)
            prefix = "sh" if row.exchange == "SH" else "sz"
            try:
                def query():
                    return bs.query_history_k_data_plus(
                        f"{prefix}.{code}",
                        fields,
                        start_date=symbol_start.strftime("%Y-%m-%d"),
                        end_date=symbol_end.strftime("%Y-%m-%d"),
                        frequency="d",
                        adjustflag="3",
                    )

                result = query()
                if result.error_code == "10001001":
                    login = bs.login()
                    if login.error_code != "0":
                        raise RuntimeError(
                            f"BaoStock re-login failed: {login.error_msg}"
                        )
                    result = query()
                frame = baostock_frame(result)
                if frame.empty:
                    failures.append({"code": code, "error": "empty"})
                    continue
                frame["date"] = pd.to_datetime(frame["date"], errors="coerce")
                frame["code"] = code
                for column in (
                    "turn", "pctChg", "peTTM", "pbMRQ", "psTTM", "pcfNcfTTM"
                ):
                    frame[column] = pd.to_numeric(frame[column], errors="coerce")
                for column in ("tradestatus", "isST"):
                    frame[column] = pd.to_numeric(
                        frame[column], errors="coerce"
                    ).astype("Int8")
                frame["source"] = "baostock"
                atomic_parquet(frame, output)
            except Exception as exc:  # noqa: BLE001
                failures.append({"code": code, "error": str(exc)})
    finally:
        bs.logout()
    atomic_json(
        {"failures": failures, "updated_at": dt.datetime.now()},
        STATE / "indicator_failures.json",
    )


def import_simtradedata_indicators(
    source_root: Path,
    limit: Optional[int] = None,
    codes: Optional[Sequence[str]] = None,
    force: bool = False,
) -> None:
    """Import the public SimTradeData valuation/status release into our schema."""
    valuation_dir = source_root / "valuation"
    status_path = source_root / "metadata" / "stock_status.parquet"
    if not valuation_dir.exists() or not status_path.exists():
        raise FileNotFoundError(
            "SimTradeData extract must contain valuation/ and "
            f"metadata/stock_status.parquet under {source_root}"
        )
    manifest_path = source_root / "manifest.json"
    release_version = "unknown"
    if manifest_path.exists():
        release_version = str(
            json.loads(manifest_path.read_text(encoding="utf-8")).get(
                "version", "unknown"
            )
        )
    source_name = f"simtradedata_release_{release_version}"

    status = pd.read_parquet(status_path)
    st_dates: Dict[str, set[pd.Timestamp]] = defaultdict(set)
    halt_dates: Dict[str, set[pd.Timestamp]] = defaultdict(set)
    for row in status.itertuples(index=False):
        day = pd.Timestamp(str(row.date)).normalize()
        target = st_dates if str(row.status_type).upper() == "ST" else halt_dates
        for symbol in row.symbols:
            target[str(symbol).split(".")[0]].add(day)

    files = sorted(valuation_dir.glob("*.parquet"))
    if codes:
        requested = {str(code).zfill(6) for code in codes}
        files = [path for path in files if path.stem.split(".")[0] in requested]
    if limit:
        files = files[:limit]

    output_dir = PARQUET / "daily_indicators"
    output_dir.mkdir(parents=True, exist_ok=True)
    built = skipped = 0
    for index, path in enumerate(files, start=1):
        code = path.stem.split(".")[0]
        output = output_dir / f"{code}.parquet"
        if output.exists() and not force:
            skipped += 1
            continue
        valuation = pd.read_parquet(path)
        dates = pd.to_datetime(valuation["date"]).dt.normalize()
        frame = pd.DataFrame(
            {
                "date": dates,
                "code": code,
                "turn": pd.to_numeric(valuation["turnover_rate"], errors="coerce"),
                "tradestatus": (~dates.isin(halt_dates.get(code, ()))).astype("int8"),
                "pctChg": float("nan"),
                "peTTM": pd.to_numeric(valuation["pe_ttm"], errors="coerce"),
                "pbMRQ": pd.to_numeric(valuation["pb"], errors="coerce"),
                "psTTM": pd.to_numeric(valuation["ps_ttm"], errors="coerce"),
                "pcfNcfTTM": pd.to_numeric(valuation["pcf"], errors="coerce"),
                "isST": dates.isin(st_dates.get(code, ())).astype("int8"),
                "source": source_name,
            }
        )
        atomic_parquet(frame, output)
        built += 1
        if index % 500 == 0 or index == len(files):
            logging.info(
                "SimTradeData indicators progress: %s/%s built=%s skipped=%s",
                index,
                len(files),
                built,
                skipped,
            )
    logging.info(
        "SimTradeData indicators finished: built=%s skipped=%s",
        built,
        skipped,
    )


def build_reference_data(config: Dict) -> None:
    """Build trading calendar, benchmark bars, industry and index snapshots."""
    if bs is None:
        raise RuntimeError("baostock is required; install requirements.txt")
    start_date = pd.Timestamp(config["start_date"])
    end_date = pd.Timestamp(config["end_date"] or dt.date.today())
    login = bs.login()
    if login.error_code != "0":
        raise RuntimeError(f"BaoStock login failed: {login.error_msg}")
    try:
        calendar = baostock_frame(
            bs.query_trade_dates(
                start_date=start_date.strftime("%Y-%m-%d"),
                end_date=end_date.strftime("%Y-%m-%d"),
            )
        )
        calendar["calendar_date"] = pd.to_datetime(calendar["calendar_date"])
        calendar["is_trading_day"] = pd.to_numeric(
            calendar["is_trading_day"], errors="coerce"
        ).astype("Int8")
        atomic_parquet(calendar, PARQUET / "reference" / "trade_calendar.parquet")

        index_fields = (
            "date,code,open,high,low,close,preclose,volume,amount,pctChg"
        )
        for code in ("000300", "000905", "000852"):
            frame = baostock_frame(
                bs.query_history_k_data_plus(
                    f"sh.{code}",
                    index_fields,
                    start_date=start_date.strftime("%Y-%m-%d"),
                    end_date=end_date.strftime("%Y-%m-%d"),
                    frequency="d",
                    adjustflag="3",
                )
            )
            if frame.empty:
                continue
            frame["date"] = pd.to_datetime(frame["date"], errors="coerce")
            for column in (
                "open", "high", "low", "close", "preclose",
                "volume", "amount", "pctChg",
            ):
                frame[column] = pd.to_numeric(frame[column], errors="coerce")
            frame["source"] = "baostock"
            atomic_parquet(
                frame, PARQUET / "reference" / "index_bars" / f"{code}.parquet"
            )

        snapshot_dates = [
            f"{year}-12-31" for year in range(start_date.year, end_date.year)
        ] + [end_date.strftime("%Y-%m-%d")]
        industry_frames = []
        constituent_frames = []
        for snapshot_date in snapshot_dates:
            industry = baostock_frame(bs.query_stock_industry(date=snapshot_date))
            if not industry.empty:
                industry["snapshot_date"] = pd.Timestamp(snapshot_date)
                industry_frames.append(industry)
            for index_name, query in (
                ("hs300", bs.query_hs300_stocks),
                ("zz500", bs.query_zz500_stocks),
            ):
                members = baostock_frame(query(snapshot_date))
                if not members.empty:
                    members["snapshot_date"] = pd.Timestamp(snapshot_date)
                    members["index_name"] = index_name
                    constituent_frames.append(members)
        if industry_frames:
            atomic_parquet(
                pd.concat(industry_frames, ignore_index=True),
                PARQUET / "reference" / "industry_membership.parquet",
            )
        if constituent_frames:
            atomic_parquet(
                pd.concat(constituent_frames, ignore_index=True),
                PARQUET / "reference" / "index_constituents.parquet",
            )
    finally:
        bs.logout()


def _merge_adjacent_intervals(
    rows: List[Dict],
    identity_columns: Sequence[str],
) -> pd.DataFrame:
    """Merge consecutive snapshot-derived intervals with identical identity."""
    if not rows:
        return pd.DataFrame()
    ordered = sorted(
        rows,
        key=lambda row: (
            str(row.get("code", "")),
            *(str(row.get(column, "")) for column in identity_columns),
            pd.Timestamp(row["valid_from"]),
        ),
    )
    merged: List[Dict] = []
    for row in ordered:
        if merged:
            previous = merged[-1]
            same_identity = (
                previous["code"] == row["code"]
                and all(
                    previous[column] == row[column]
                    for column in identity_columns
                )
            )
            adjacent = (
                pd.notna(previous["valid_to"])
                and pd.Timestamp(previous["valid_to"])
                == pd.Timestamp(row["valid_from"])
            )
            if same_identity and adjacent:
                previous["valid_to"] = row["valid_to"]
                previous["is_current_snapshot"] = row.get(
                    "is_current_snapshot", False
                )
                continue
        merged.append(dict(row))
    return pd.DataFrame(merged)


def build_reference_intervals(config: Dict) -> None:
    """Convert raw annual snapshots into explicit, quality-tagged intervals."""
    del config
    industry_path = PARQUET / "reference" / "industry_membership.parquet"
    constituent_path = PARQUET / "reference" / "index_constituents.parquet"
    if not industry_path.exists() or not constituent_path.exists():
        raise RuntimeError("reference snapshots are missing; run reference first")

    industry = pd.read_parquet(industry_path)
    industry["snapshot_date"] = pd.to_datetime(
        industry["snapshot_date"], errors="coerce"
    ).dt.normalize()
    industry_dates = sorted(industry["snapshot_date"].dropna().unique())
    industry_next = {
        pd.Timestamp(date): (
            pd.Timestamp(industry_dates[index + 1])
            if index + 1 < len(industry_dates) else pd.NaT
        )
        for index, date in enumerate(industry_dates)
    }
    industry_rows: List[Dict] = []
    for row in industry.itertuples(index=False):
        snapshot_date = pd.Timestamp(row.snapshot_date)
        name = str(row.industry or "").strip()
        classification = str(row.industryClassification or "证监会行业分类").strip()
        code = str(row.code).split(".")[-1].zfill(6)
        industry_rows.append({
            "code": code,
            "classification_system": "CSRC",
            "industry_level": max(1, len([part for part in name.split("-") if part])),
            "industry_code": f"CSRC:{classification}:{name}",
            "industry_name": name,
            "valid_from": snapshot_date,
            "valid_to": industry_next[snapshot_date],
            "snapshot_date": snapshot_date,
            "is_current_snapshot": snapshot_date == pd.Timestamp(industry_dates[-1]),
            "interval_quality": "snapshot_derived",
            "source_update_date": pd.to_datetime(
                row.updateDate, errors="coerce"
            ),
            "source": "baostock",
        })
    industry_intervals = _merge_adjacent_intervals(
        industry_rows,
        ["classification_system", "industry_level", "industry_code"],
    )

    sw_path = (
        PARQUET / "reference" / "industry_indices"
        / "current_stock_membership.parquet"
    )
    if sw_path.exists():
        sw = pd.read_parquet(sw_path)
        sw_rows = pd.DataFrame({
            "code": sw["security_code"].astype(str).str.zfill(6),
            "classification_system": "SW",
            "industry_level": 1,
            "industry_code": sw["sw_level1_index_code"].astype(str),
            "industry_name": sw["sw_level1_name"].astype(str),
            # Current-only rows start at the observed snapshot, not the
            # historical included_since date, to avoid survivorship leakage.
            "valid_from": pd.to_datetime(sw["snapshot_date"]).dt.normalize(),
            "valid_to": pd.NaT,
            "snapshot_date": pd.to_datetime(sw["snapshot_date"]).dt.normalize(),
            "is_current_snapshot": True,
            "interval_quality": "current_only",
            "source_update_date": pd.to_datetime(
                sw["included_since"], errors="coerce"
            ),
            "source": sw["source"].astype(str),
        })
        industry_intervals = pd.concat(
            [industry_intervals, sw_rows], ignore_index=True
        )
    industry_intervals = industry_intervals.sort_values(
        ["classification_system", "code", "valid_from", "industry_code"]
    )
    atomic_parquet(
        industry_intervals,
        PARQUET / "reference" / "industry_membership_intervals.parquet",
    )

    constituents = pd.read_parquet(constituent_path)
    constituents["snapshot_date"] = pd.to_datetime(
        constituents["snapshot_date"], errors="coerce"
    ).dt.normalize()
    index_code_map = {"hs300": "000300", "zz500": "000905"}
    constituent_rows: List[Dict] = []
    for index_name, group in constituents.groupby("index_name"):
        snapshot_dates = sorted(group["snapshot_date"].dropna().unique())
        next_dates = {
            pd.Timestamp(date): (
                pd.Timestamp(snapshot_dates[index + 1])
                if index + 1 < len(snapshot_dates) else pd.NaT
            )
            for index, date in enumerate(snapshot_dates)
        }
        for row in group.itertuples(index=False):
            snapshot_date = pd.Timestamp(row.snapshot_date)
            constituent_rows.append({
                "index_code": index_code_map.get(str(index_name), str(index_name)),
                "code": str(row.code).split(".")[-1].zfill(6),
                "valid_from": snapshot_date,
                "valid_to": next_dates[snapshot_date],
                "snapshot_date": snapshot_date,
                "interval_quality": "snapshot_derived",
                "source_update_date": pd.to_datetime(
                    row.updateDate, errors="coerce"
                ),
                "source": "baostock",
            })
    constituent_intervals = _merge_adjacent_intervals(
        constituent_rows, ["index_code"]
    )
    constituent_intervals = constituent_intervals.sort_values(
        ["index_code", "code", "valid_from"]
    )
    atomic_parquet(
        constituent_intervals,
        PARQUET / "reference" / "index_constituent_intervals.parquet",
    )
    atomic_json(
        {
            "industry_interval_rows": len(industry_intervals),
            "index_constituent_interval_rows": len(constituent_intervals),
            "updated_at": dt.datetime.now(),
        },
        STATE / "reference_interval_coverage.json",
    )
    logging.info(
        "Reference intervals saved: %s industry, %s index constituent rows",
        len(industry_intervals), len(constituent_intervals),
    )


def build_industry_indices(config: Dict, force: bool = False) -> None:
    """Build Shenwan level-1 industry index master and daily bars."""
    if ak is None:
        raise RuntimeError("akshare is required; install requirements.txt")
    start_date = pd.Timestamp(config["start_date"])
    end_date = pd.Timestamp(config["end_date"] or dt.date.today())
    master = retry(
        ak.sw_index_first_info,
        "Shenwan level-1 index master",
        attempts=config["retries"],
        base_sleep=config["retry_base_seconds"],
    ).rename(
        columns={
            "行业代码": "index_code",
            "行业名称": "industry_name",
            "成份个数": "constituent_count",
            "静态市盈率": "pe_static",
            "TTM(滚动)市盈率": "pe_ttm",
            "市净率": "pb",
            "静态股息率": "dividend_yield",
        }
    )
    master["index_code"] = master["index_code"].astype(str)
    master["snapshot_date"] = pd.Timestamp(dt.date.today())
    master["source"] = "sw_via_akshare"
    atomic_parquet(
        master,
        PARQUET / "reference" / "industry_indices" / "sw_level1_master.parquet",
    )

    failures = []
    for index, row in enumerate(master.itertuples(index=False), start=1):
        full_code = str(row.index_code)
        code = full_code.split(".", 1)[0]
        output = (
            PARQUET / "reference" / "industry_indices" / "daily_bars"
            / f"{code}.parquet"
        )
        if output.exists() and not force:
            continue
        logging.info(
            "[%s/%s] Shenwan industry index %s %s",
            index, len(master), code, row.industry_name,
        )
        try:
            frame = retry(
                lambda c=code: ak.index_hist_sw(symbol=c, period="day"),
                f"Shenwan index bars {code}",
                attempts=config["retries"],
                base_sleep=config["retry_base_seconds"],
            ).rename(
                columns={
                    "代码": "index_code",
                    "日期": "date",
                    "开盘": "open",
                    "收盘": "close",
                    "最高": "high",
                    "最低": "low",
                    "成交量": "volume",
                    "成交额": "amount",
                }
            )
            if frame.empty:
                failures.append({"index_code": code, "error": "empty"})
                continue
            frame["date"] = pd.to_datetime(frame["date"], errors="coerce")
            frame = frame[frame["date"].between(start_date, end_date)].copy()
            for column in ("open", "high", "low", "close", "volume", "amount"):
                frame[column] = pd.to_numeric(frame[column], errors="coerce")
            frame["industry_name"] = row.industry_name
            frame["source"] = "sw_via_akshare"
            atomic_parquet(
                frame[
                    [
                        "date", "index_code", "industry_name", "open", "high",
                        "low", "close", "volume", "amount", "source",
                    ]
                ].sort_values("date"),
                output,
            )
        except Exception as exc:  # noqa: BLE001
            failures.append({"index_code": code, "error": str(exc)})
    atomic_json(
        {"failures": failures, "updated_at": dt.datetime.now()},
        STATE / "industry_index_failures.json",
    )


def build_sw_current_membership(config: Dict) -> None:
    """Build current stock-to-Shenwan-level-1 membership.

    This is a current snapshot, not a survivorship-safe historical membership
    table. The existing CSRC annual snapshots remain the historical fallback.
    """
    master_path = (
        PARQUET / "reference" / "industry_indices" / "sw_level1_master.parquet"
    )
    if not master_path.exists():
        build_industry_indices(config)
    master = pd.read_parquet(master_path)
    output_path = (
        PARQUET / "reference" / "industry_indices"
        / "current_stock_membership.parquet"
    )
    existing = pd.read_parquet(output_path) if output_path.exists() else pd.DataFrame()
    completed_codes = (
        set(existing["sw_level1_index_code"].dropna().astype(str))
        if not existing.empty else set()
    )
    session = requests.Session()
    session.headers.update({"User-Agent": UA})
    throttle = Throttle(2.0)
    frames = [existing] if not existing.empty else []
    failures = []
    for index, row in enumerate(master.itertuples(index=False), start=1):
        full_code = str(row.index_code)
        if full_code in completed_codes:
            continue
        logging.info(
            "[%s/%s] current Shenwan members %s %s",
            index, len(master), full_code, row.industry_name,
        )
        try:
            def fetch_page() -> str:
                throttle.wait()
                response = session.get(
                    "https://legulegu.com/stockdata/index-composition",
                    params={"industryCode": full_code},
                    timeout=35,
                )
                response.raise_for_status()
                return response.text

            tables = pd.read_html(StringIO(retry(fetch_page, config)))
            if not tables or tables[0].empty:
                failures.append({"index_code": full_code, "error": "empty"})
                continue
            # The page currently injects JSON-LD into later table headers.
            # The first six columns remain stable and contain all membership data.
            frame = tables[0].iloc[:, :6].copy()
            frame.columns = [
                "sequence", "security_code", "security_name", "included_since",
                "page_sw_level1", "sub_concept",
            ]
            frame["security_code"] = (
                frame["security_code"].astype(str).str.extract(r"(\d{6})", expand=False)
            )
            frame["included_since"] = pd.to_datetime(
                frame["included_since"], errors="coerce"
            )
            frame["sw_level1_index_code"] = full_code
            frame["sw_level1_name"] = row.industry_name
            frame["snapshot_date"] = pd.Timestamp(dt.date.today())
            frame["is_current_snapshot"] = True
            frame["source"] = "legulegu_sw"
            frames.append(
                frame[
                    [
                        "security_code", "security_name", "sw_level1_index_code",
                        "sw_level1_name", "included_since", "sub_concept",
                        "snapshot_date", "is_current_snapshot", "source",
                    ]
                ]
            )
        except Exception as exc:  # noqa: BLE001
            failures.append({"index_code": full_code, "error": str(exc)})
    if frames:
        result = pd.concat(frames, ignore_index=True)
        result = result.dropna(subset=["security_code"])
        result = result.drop_duplicates(
            ["security_code", "sw_level1_index_code"], keep="last"
        ).sort_values(["sw_level1_index_code", "security_code"])
        atomic_parquet(result, output_path)
    atomic_json(
        {"failures": failures, "updated_at": dt.datetime.now()},
        STATE / "sw_membership_failures.json",
    )


def build_manifest(config: Dict) -> None:
    master_path = PARQUET / "security_master" / "security_master.parquet"
    master = pd.read_parquet(master_path) if master_path.exists() else pd.DataFrame()
    files = list(PARQUET.rglob("*.parquet"))
    bars = [path for path in files if "daily_bars" in path.parts]
    financials = [path for path in files if "financials" in path.parts]
    announcements = [path for path in files if "announcements" in path.parts]
    corporate_actions = [path for path in files if "corporate_actions" in path.parts]
    manifest = {
        "generated_at": dt.datetime.now(),
        "configured_start_date": config["start_date"],
        "configured_end_date": config["end_date"] or str(dt.date.today()),
        "security_count": len(master),
        "listed_count": int(master["status"].eq("listed").sum()) if not master.empty else 0,
        "delisted_count": int(master["status"].eq("delisted").sum()) if not master.empty else 0,
        "bar_files": len(bars),
        "financial_files": len(financials),
        "announcement_files": len(announcements),
        "corporate_action_files": len(corporate_actions),
        "snapshot_files": len([p for p in files if "market_snapshots" in p.parts]),
        "limitations": [
            "Historical universe is best-effort; free exchange lists may omit renamed or migrated securities.",
            "Forward-adjusted prices are snapshots and can change after future corporate actions.",
            "Tencent valuation fields are current snapshots and must not be used in historical backtests.",
            "Sina statements are aligned to CNInfo announcement dates only approximately; revisions are not versioned.",
            "Beijing and delisted bars may use the rate-limited Eastmoney fallback.",
        ],
    }
    atomic_json(manifest, ROOT / "manifest.json")


def validate_dataset(config: Dict) -> None:
    del config
    master = pd.read_parquet(PARQUET / "security_master" / "security_master.parquet")
    issues = []
    if master["code"].duplicated().any():
        issues.append("duplicate security codes in master")
    listed_missing_dates = int(
        (master["status"].eq("listed") & master["list_date"].isna()).sum()
    )
    if listed_missing_dates:
        issues.append(f"{listed_missing_dates} listed securities missing list_date")

    master_codes = set(master["code"].astype(str))
    raw_files = sorted(
        (PARQUET / "daily_bars" / "adjustment=raw").glob("*.parquet")
    )
    qfq_files = sorted(
        (PARQUET / "daily_bars" / "adjustment=qfq").glob("*.parquet")
    )
    raw_codes = {path.stem for path in raw_files}
    qfq_codes = {path.stem for path in qfq_files}
    raw_rows = 0
    qfq_rows = 0
    min_date = None
    max_date = None
    invalid_ohlc = 0
    duplicate_keys = 0
    qfq_date_mismatch_codes = []
    for path in raw_files:
        frame = pd.read_parquet(path)
        raw_rows += len(frame)
        if frame.empty:
            continue
        current_min = pd.to_datetime(frame["date"]).min()
        current_max = pd.to_datetime(frame["date"]).max()
        min_date = current_min if min_date is None else min(min_date, current_min)
        max_date = current_max if max_date is None else max(max_date, current_max)
        invalid_ohlc += int(
            ((frame["high"] < frame[["open", "close", "low"]].max(axis=1))
             | (frame["low"] > frame[["open", "close", "high"]].min(axis=1))).sum()
        )
        duplicate_keys += int(frame.duplicated(["code", "date"]).sum())

        qfq_path = PARQUET / "daily_bars" / "adjustment=qfq" / path.name
        if qfq_path.exists():
            qfq_dates = pd.read_parquet(qfq_path, columns=["date"])["date"]
            qfq_rows += len(qfq_dates)
            if not pd.Index(frame["date"]).equals(pd.Index(qfq_dates)):
                qfq_date_mismatch_codes.append(path.stem)
    if qfq_date_mismatch_codes:
        issues.append(
            f"{len(qfq_date_mismatch_codes)} qfq files do not align with raw dates"
        )

    financial_duplicate_rows = 0
    financial_conflicting_keys = 0
    financial_files = sorted((PARQUET / "financials").glob("*.parquet"))
    financial_keys = [
        "code", "report_date", "statement_type", "item_code"
    ]
    for path in financial_files:
        frame = pd.read_parquet(
            path,
            columns=financial_keys + ["value"],
        )
        duplicate_mask = frame.duplicated(financial_keys, keep=False)
        financial_duplicate_rows += int(duplicate_mask.sum())
        if duplicate_mask.any():
            for _, group in frame.loc[duplicate_mask].groupby(
                financial_keys, dropna=False
            ):
                if len(group["value"].dropna().unique()) > 1:
                    financial_conflicting_keys += 1
    if financial_duplicate_rows:
        issues.append(
            f"{financial_duplicate_rows} duplicate financial statement rows"
        )

    announcement_files = sorted((PARQUET / "announcements").glob("*.parquet"))
    duplicate_announcements = 0
    for path in announcement_files:
        frame = pd.read_parquet(path, columns=["announcement_id"])
        duplicate_announcements += int(frame["announcement_id"].duplicated().sum())
    if duplicate_announcements:
        issues.append(f"{duplicate_announcements} duplicate announcements")

    availability_path = (
        PARQUET / "reference" / "financial_availability.parquet"
    )
    availability_quality = {}
    if availability_path.exists():
        availability = pd.read_parquet(availability_path)
        availability_quality = (
            availability["availability_quality"].value_counts(
                dropna=False
            ).to_dict()
        )

    interval_checks = {}
    for name, path, keys, groups in (
        (
            "industry",
            PARQUET / "reference" / "industry_membership_intervals.parquet",
            [
                "code", "classification_system", "industry_level",
                "industry_code", "valid_from",
            ],
            ["code", "classification_system"],
        ),
        (
            "index_constituent",
            PARQUET / "reference" / "index_constituent_intervals.parquet",
            ["index_code", "code", "valid_from"],
            ["index_code", "code"],
        ),
    ):
        if not path.exists():
            continue
        frame = pd.read_parquet(path)
        duplicate_interval_keys = int(frame.duplicated(keys).sum())
        invalid_ranges = int(
            (
                frame["valid_to"].notna()
                & (frame["valid_to"] <= frame["valid_from"])
            ).sum()
        )
        overlaps = 0
        for _, group in frame.sort_values("valid_from").groupby(groups):
            previous_end = None
            for row in group.itertuples(index=False):
                if (
                    previous_end is not None
                    and pd.notna(previous_end)
                    and row.valid_from < previous_end
                ):
                    overlaps += 1
                previous_end = row.valid_to
        interval_checks[name] = {
            "rows": len(frame),
            "duplicate_keys": duplicate_interval_keys,
            "invalid_ranges": invalid_ranges,
            "overlaps": overlaps,
        }
        if duplicate_interval_keys or invalid_ranges or overlaps:
            issues.append(f"invalid {name} validity intervals")

    indicator_files = list((PARQUET / "daily_indicators").glob("*.parquet"))
    corporate_action_files = list(
        (PARQUET / "corporate_actions").glob("*.parquet")
    )
    shares_daily_files = list((PARQUET / "shares_daily").glob("*.parquet"))
    equity_structure_files = list((PARQUET / "equity_structure").glob("*.parquet"))
    limit_price_files = list((PARQUET / "limit_prices").glob("*.parquet"))
    float_filled = 0
    for path in shares_daily_files:
        sample = pd.read_parquet(path, columns=["float_shares"])
        if sample["float_shares"].notna().any():
            float_filled += 1
    report = {
        "generated_at": dt.datetime.now(),
        "security_count": len(master),
        "listed_missing_list_date": listed_missing_dates,
        "coverage": {
            "raw_bars": {
                "files": len(raw_files),
                "missing_master_codes": len(master_codes - raw_codes),
            },
            "qfq_bars": {
                "files": len(qfq_files),
                "missing_master_codes": len(master_codes - qfq_codes),
            },
            "daily_indicators": {"files": len(indicator_files)},
            "financials": {"files": len(financial_files)},
            "announcements": {"files": len(announcement_files)},
            "corporate_actions": {"files": len(corporate_action_files)},
            "equity_structure": {"files": len(equity_structure_files)},
            "shares_daily": {
                "files": len(shares_daily_files),
                "with_float_shares": float_filled,
            },
            "limit_prices": {"files": len(limit_price_files)},
        },
        "raw_bar_row_count": raw_rows,
        "qfq_bar_row_count": qfq_rows,
        "bar_min_date": min_date,
        "bar_max_date": max_date,
        "invalid_ohlc_rows": invalid_ohlc,
        "duplicate_bar_keys": duplicate_keys,
        "qfq_date_mismatch_count": len(qfq_date_mismatch_codes),
        "qfq_date_mismatch_sample": qfq_date_mismatch_codes[:100],
        "financial_duplicate_rows": financial_duplicate_rows,
        "financial_conflicting_keys": financial_conflicting_keys,
        "duplicate_announcements": duplicate_announcements,
        "financial_availability_quality": availability_quality,
        "interval_checks": interval_checks,
        "issues": issues,
        "status": "pass" if not issues and invalid_ohlc == 0 and duplicate_keys == 0 else "review",
    }
    atomic_json(report, ROOT / "quality_report.json")
    logging.info("Quality report: %s", report)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "command",
        choices=["universe", "listing-dates", "bars", "repair-qfq", "snapshot",
                 "financials", "repair-financials",
                 "announcements", "announcements-q1", "financial-availability",
                 "corporate-actions", "equity-structure", "shares-daily",
                 "limit-prices", "indicators", "bj-indicators", "bj-valuation",
                 "import-simtradedata-indicators",
                 "warehouse", "catalog", "update-daily",
                 "reference",
                 "reference-intervals", "industry-indices", "sw-membership",
                 "validate", "manifest",
                 "all"],
    )
    parser.add_argument("--limit", type=int, help="Limit securities for a pilot run")
    parser.add_argument("--codes", nargs="*", help="Only process specific six-digit codes")
    parser.add_argument("--force", action="store_true", help="Overwrite completed symbol files / redo update-daily even if already current")
    parser.add_argument(
        "--source-root",
        type=Path,
        default=RAW / "simtradelab" / "cn",
        help="Extracted SimTradeData CN release root",
    )
    parser.add_argument(
        "--skip-slow",
        action="store_true",
        help="For update-daily: skip corporate/equity/financials/announcements",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=4,
        help="Parallel worker threads for update-daily bars/financials/announcements (default: 4)",
    )
    return parser.parse_args()


def main() -> None:
    setup_logging()
    config = load_config()
    args = parse_args()
    for directory in (PARQUET, RAW, STATE, LOGS):
        directory.mkdir(parents=True, exist_ok=True)

    if args.command in ("universe", "all"):
        build_universe(config)
    if args.command in ("listing-dates", "all"):
        build_listing_dates(config)
    if args.command in ("bars", "all"):
        build_bars(config, args.limit, args.codes, args.force)
    if args.command in ("repair-qfq", "all"):
        repair_qfq_bars(config, args.limit, args.codes)
    if args.command in ("snapshot", "all"):
        build_snapshot(config, args.limit)
    if args.command in ("financials", "all"):
        build_financials(config, args.limit, args.codes, args.force)
    if args.command in ("repair-financials", "all"):
        repair_financial_duplicates(config)
    if args.command in ("announcements", "all"):
        build_financial_announcements(config, args.limit, args.codes, args.force)
    if args.command == "announcements-q1":
        build_financial_announcements(
            config, args.limit, args.codes, args.force, q1_only=True
        )
    if args.command in ("financial-availability", "all"):
        build_financial_availability(config)
    if args.command in ("corporate-actions", "all"):
        build_corporate_actions(config, args.limit, args.codes, args.force)
    if args.command in ("equity-structure", "all"):
        build_equity_structure(config, args.limit, args.codes, args.force)
    if args.command in ("shares-daily", "all"):
        build_shares_daily(config, args.limit, args.codes, args.force)
    if args.command in ("limit-prices", "all"):
        build_limit_prices(config, args.limit, args.codes, args.force)
    if args.command in ("indicators", "all"):
        build_daily_indicators(config, args.limit, args.codes, args.force)
    if args.command == "import-simtradedata-indicators":
        import_simtradedata_indicators(
            args.source_root,
            args.limit,
            args.codes,
            args.force,
        )
    if args.command in ("bj-indicators", "all"):
        build_bj_daily_indicators(config, args.limit, args.codes, args.force)
    if args.command in ("bj-valuation", "all"):
        from research_layer import enrich_bj_indicators_with_valuation

        enrich_bj_indicators_with_valuation(config, args.limit, args.codes)
    if args.command in ("warehouse", "all"):
        from research_layer import build_warehouse

        build_warehouse(config)
    if args.command in ("catalog", "all"):
        from research_layer import build_catalog

        build_catalog(config)
    if args.command == "update-daily":
        from update import run_daily_update

        run_daily_update(
            config, codes=args.codes, skip_slow=args.skip_slow,
            force=args.force, max_workers=args.workers,
        )
    if args.command in ("reference", "all"):
        build_reference_data(config)
    if args.command in ("reference-intervals", "all"):
        build_reference_intervals(config)
    if args.command in ("industry-indices", "all"):
        build_industry_indices(config, args.force)
    if args.command in ("sw-membership", "all"):
        build_sw_current_membership(config)
    if args.command in ("validate", "all"):
        validate_dataset(config)
    if args.command in ("manifest", "all"):
        build_manifest(config)


if __name__ == "__main__":
    main()
