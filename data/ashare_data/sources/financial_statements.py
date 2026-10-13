"""sources — financial statements: data acquisition and normalization."""
from __future__ import annotations
import datetime as dt
import logging
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Dict, Optional, Sequence, Tuple, Set
import pandas as pd
import requests
from data.ashare_data.paths import PARQUET_ROOT as PARQUET, STATE_ROOT as STATE
from data.ashare_data.runtime import UA

from data.ashare_data.runtime import Throttle
from data.ashare_data.incremental_files import _listed_universe
from data.ashare_data.storage import atomic_json
from data.ashare_data.storage import atomic_parquet
from data.ashare_data.sources.securities import exchange_for
from data.ashare_data.runtime import retry
SINA_FINANCIAL_URL = (
    "https://quotes.sina.cn/cn/api/openapi.php/"
    "CompanyFinanceService.getFinanceReport2022"
)


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


def merge_statement_revisions(existing: pd.DataFrame, new: pd.DataFrame) -> pd.DataFrame:
    """The latest successful source observation wins, retaining unreturned history.

    This maintains the latest statement snapshot, not a vintage/PIT revision archive.
    """
    keys = ['code', 'report_date', 'statement_type', 'item_code']
    if existing.empty:
        result = new.copy()
    else:
        updated_keys = pd.MultiIndex.from_frame(new[keys])
        keep = ~pd.MultiIndex.from_frame(existing[keys]).isin(updated_keys)
        result = pd.concat([existing.loc[keep], new], ignore_index=True)
    return result.sort_values(['report_date','statement_type','item'], ascending=[False,True,True]).reset_index(drop=True)


def update_financials_incremental(
    config: Dict,
    codes: Optional[Sequence[str]] = None,
    periods: int = 12,
    max_workers: int = 8,
    on_code_done=None,
) -> Set[str]:
    """Fetch recent statement periods and merge into existing financial files."""
    from .fetch_windows import financial_period_count

    universe = _listed_universe(codes)
    throttle = config.get('_sina_budget') or Throttle(config["sina_min_interval_seconds"])
    _local = threading.local()

    def _session():
        if not hasattr(_local, "session"):
            _local.session = requests.Session()
            _local.session.headers.update({"User-Agent": UA})
        return _local.session

    def _process_stock(row) -> tuple[str, bool, list]:
        code = str(row.code)
        output = PARQUET / "financials" / f"{code}.parquet"
        logging.info("update financials %s", code)
        checked = config.get('_source_checked_through', {}).get(code)
        fetch_periods = max(periods, financial_period_count(config, checked))
        frames = []
        stock_failures: list = []
        for report_type in ("fzb", "lrb", "llb"):
            try:
                frame = retry(
                    lambda rt=report_type: fetch_sina_statement(
                        _session(), code, rt, fetch_periods, throttle
                    ),
                    f"Sina {report_type} {code}",
                    attempts=config["retries"],
                )
                if not frame.empty:
                    frames.append(frame)
                else:
                    stock_failures.append({'code': code, 'type': report_type, 'error': 'empty statement response'})
            except Exception as exc:  # noqa: BLE001
                stock_failures.append({"code": code, "type": report_type, "error": str(exc)})
        if not frames:
            stock_failures.append({"code": code, "type": "all", "error": "empty"})
            return code, False, stock_failures
        if stock_failures:
            # A partial HTTP success must not overwrite the previous complete asset.
            return code, False, stock_failures
        new, conflicts = deduplicate_financial_frame(pd.concat(frames, ignore_index=True))
        if conflicts:
            return code, False, [{'code':code, 'type':'source_conflicts', 'error':str(conflicts)}]
        existing = pd.read_parquet(output) if output.exists() else pd.DataFrame()
        merged = merge_statement_revisions(existing, new)
        atomic_parquet(merged, output)
        if on_code_done is not None:
            on_code_done(code)
        return code, True, []

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
                logging.info("financials progress: %s/%s", completed, total)

    atomic_json(
        {
            "updated_codes": sorted(updated),
            "failures": failures,
            "updated_at": dt.datetime.now(),
        },
        STATE / "daily_financial_update.json",
    )
    logging.info(
        "Financial incremental update: %s codes, %s failures",
        len(updated), len(failures),
    )
    return updated
