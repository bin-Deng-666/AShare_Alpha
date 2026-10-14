"""sources — announcements: data acquisition and normalization."""

from __future__ import annotations
import datetime as dt
import logging
import re
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Dict, Optional, Sequence, Set
import pandas as pd
import requests
from ashare_alpha.data.paths import PARQUET_ROOT as PARQUET, STATE_ROOT as STATE
from ashare_alpha.data.runtime import UA

from ashare_alpha.data.runtime import Throttle
from ashare_alpha.data.incremental_files import _listed_universe
from ashare_alpha.data.storage import atomic_json
from ashare_alpha.data.storage import atomic_parquet
from ashare_alpha.data.runtime import retry

CNINFO_QUERY_URL = "https://www.cninfo.com.cn/new/hisAnnouncement/query"


def announcement_items(response) -> tuple[list, bool]:
    response.raise_for_status()
    data = response.json()
    if (
        not isinstance(data, dict)
        or "announcements" not in data
        or "hasMore" not in data
    ):
        raise RuntimeError("CNInfo returned an invalid announcement page")
    items = data["announcements"] or []
    more = bool(data["hasMore"])
    if more and not items:
        raise RuntimeError("CNInfo returned an empty page before pagination finished")
    return items, more


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
    session.headers.update(
        {
            "User-Agent": UA,
            "Content-Type": "application/x-www-form-urlencoded",
            "Referer": "https://www.cninfo.com.cn/new/disclosure",
            "Origin": "https://www.cninfo.com.cn",
        }
    )
    throttle = Throttle(config.get("cninfo_min_interval_seconds", 0.25))
    failures = []
    for index, row in enumerate(universe.itertuples(index=False), start=1):
        code = str(row.code)
        output = PARQUET / "announcements" / f"{code}.parquet"
        if output.exists() and not force:
            if not q1_only:
                continue
            existing = pd.read_parquet(output, columns=["title"])
            if (
                existing["title"]
                .astype(str)
                .str.contains(r"(?:第一季度|一季度)报告", regex=True)
                .any()
            ):
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
        column = (
            "sse"
            if row.exchange == "SH"
            else ("third" if row.exchange == "BJ" else "szse")
        )
        plate = (
            "sh" if row.exchange == "SH" else ("bj" if row.exchange == "BJ" else "sz")
        )
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
                items, more = announcement_items(response)
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
                    rows.append(
                        {
                            "code": code,
                            "announcement_id": item.get("announcementId"),
                            "announcement_date": announcement_date,
                            "title": re.sub(
                                r"<[^>]+>", "", item.get("announcementTitle", "")
                            ),
                            "category": item.get("announcementTypeName", ""),
                            "adjunct_url": item.get("adjunctUrl", ""),
                            "source": "cninfo",
                        }
                    )
                # CNInfo's totalpages is occasionally wrong (for example,
                # totalRecordNum=40 but totalpages=1). hasMore is reliable.
                if not more:
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
        STATE
        / (
            "announcement_q1_failures.json" if q1_only else "announcement_failures.json"
        ),
    )


def update_announcements_incremental(
    config: Dict,
    codes: Optional[Sequence[str]] = None,
    lookback_days: int = 180,
    max_workers: int = 8,
    on_code_done=None,
) -> Set[str]:
    """Refresh recent financial-report announcements and merge by announcement_id."""
    from .fetch_windows import fetch_start

    end = pd.Timestamp(config["end_date"] or dt.date.today()).normalize()
    universe = _listed_universe(codes)
    throttle = Throttle(config.get("cninfo_min_interval_seconds", 0.25))
    _local = threading.local()

    def _session():
        if not hasattr(_local, "session"):
            _local.session = requests.Session()
            _local.session.headers.update(
                {
                    "User-Agent": UA,
                    "Content-Type": "application/x-www-form-urlencoded",
                    "Referer": "https://www.cninfo.com.cn/new/disclosure",
                    "Origin": "https://www.cninfo.com.cn",
                }
            )
        return _local.session

    def _process_stock(row) -> tuple[str, bool, list]:
        code = str(row.code)
        output = PARQUET / "announcements" / f"{code}.parquet"
        checked = config.get("_source_checked_through", {}).get(code)
        start = fetch_start(config, checked, overlap_days=min(lookback_days, 7))
        logging.info("update announcements %s from %s", code, start)
        stock_failures: list = []
        try:
            cached_org = getattr(row, "org_id", None)
            org_id = (
                str(cached_org)
                if pd.notna(cached_org) and str(cached_org).strip()
                else retry(
                    lambda: resolve_cninfo_org_id(_session(), code, throttle),
                    f"CNInfo orgId {code}",
                    attempts=config["retries"],
                )
            )
        except Exception as exc:  # noqa: BLE001
            stock_failures.append({"code": code, "error": f"orgId: {exc}"})
            return code, False, stock_failures
        if not org_id:
            if not output.exists():
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
            stock_failures.append({"code": code, "error": "orgId: empty"})
            return code, False, stock_failures
        column = (
            "sse"
            if row.exchange == "SH"
            else ("third" if row.exchange == "BJ" else "szse")
        )
        plate = (
            "sh" if row.exchange == "SH" else ("bj" if row.exchange == "BJ" else "sz")
        )
        payload = {
            "stock": f"{code},{org_id}",
            "tabName": "fulltext",
            "pageSize": 30,
            "pageNum": 1,
            "column": column,
            "category": (
                "category_ndbg_szsh;category_bndbg_szsh;"
                "category_yjdbg_szsh;category_sjdbg_szsh"
            ),
            "plate": plate,
            "seDate": f"{start}~{end.date()}",
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
                    lambda: _session().post(CNINFO_QUERY_URL, data=payload, timeout=25),
                    f"CNInfo announcements {code} page {page_number}",
                    attempts=config["retries"],
                )
                items, more = announcement_items(response)
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
                    rows.append(
                        {
                            "code": code,
                            "announcement_id": item.get("announcementId"),
                            "announcement_date": announcement_date,
                            "title": re.sub(
                                r"<[^>]+>", "", item.get("announcementTitle", "")
                            ),
                            "category": item.get("announcementTypeName", ""),
                            "adjunct_url": item.get("adjunctUrl", ""),
                            "source": "cninfo",
                        }
                    )
                if not more:
                    break
                page_number += 1
                if page_number > 1000:
                    raise RuntimeError(
                        "CNInfo pagination did not terminate; incomplete response"
                    )
            if not rows:
                if not output.exists():
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
                if on_code_done is not None:
                    on_code_done(code)
                return code, False, stock_failures
            new = pd.DataFrame(rows)
            existing = pd.read_parquet(output) if output.exists() else pd.DataFrame()
            merged = pd.concat([existing, new], ignore_index=True)
            merged = merged.drop_duplicates("announcement_id", keep="last")
            merged = merged.sort_values("announcement_date").reset_index(drop=True)
            atomic_parquet(merged, output)
            if on_code_done is not None:
                on_code_done(code)
            return code, True, stock_failures
        except Exception as exc:  # noqa: BLE001
            stock_failures.append({"code": code, "error": str(exc)})
            return code, False, stock_failures

    updated: Set[str] = set()
    failures: list = []
    total = len(universe)
    completed = 0

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {
            pool.submit(_process_stock, row): row
            for row in universe.itertuples(index=False)
        }
        for future in as_completed(futures):
            completed += 1
            code, was_updated, stock_failures = future.result()
            if was_updated:
                updated.add(code)
            failures.extend(stock_failures)
            if completed % 200 == 0:
                logging.info("announcements progress: %s/%s", completed, total)

    atomic_json(
        {
            "updated_codes": sorted(updated),
            "failures": failures,
            "target": str(end.date()),
            "window_policy": "per-security successful check minus overlap; initial full history",
            "updated_at": dt.datetime.now(),
        },
        STATE / "daily_announcement_update.json",
    )
    logging.info(
        "Announcement incremental update: %s codes, %s failures",
        len(updated),
        len(failures),
    )
    return updated
