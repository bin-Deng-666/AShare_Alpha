"""sources — prices: data acquisition and normalization."""

from __future__ import annotations
import datetime as dt
import json
import logging
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Dict, Optional, Sequence, Set
import pandas as pd
import requests
from ashare_alpha.data.paths import PARQUET_ROOT as PARQUET, STATE_ROOT as STATE
from ashare_alpha.data.runtime import UA

try:
    import akshare as ak
except ImportError:
    ak = None
try:
    from mootdx import consts
    from mootdx.quotes import Quotes
except ImportError:
    consts = Quotes = None

from ashare_alpha.data.runtime import Throttle
from ashare_alpha.data.incremental_files import _last_date
from ashare_alpha.data.incremental_files import _listed_universe
from ashare_alpha.data.incremental_files import _merge_by_date
from ashare_alpha.data.calendar import _target_asof
from ashare_alpha.data.storage import atomic_json
from ashare_alpha.data.storage import atomic_parquet
from ashare_alpha.data.sources.securities import build_universe
from ashare_alpha.data.sources.securities import exchange_for
from ashare_alpha.data.runtime import retry


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
    return (
        result[result["date"].between(start_date, end_date)]
        .sort_values("date")
        .drop_duplicates(["date", "code"])
    )


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
        "日期": "date",
        "开盘": "open",
        "收盘": "close",
        "最高": "high",
        "最低": "low",
        "成交量": "volume",
        "成交额": "amount",
    }
    result = frame.rename(columns=rename)
    result["date"] = pd.to_datetime(result["date"])
    result["code"] = code
    result["exchange"] = exchange_for(code)
    result["adjustment"] = adjustment
    result["source"] = "eastmoney_via_akshare"
    columns = [
        "date",
        "open",
        "high",
        "low",
        "close",
        "volume",
        "amount",
        "code",
        "exchange",
        "adjustment",
        "source",
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
            "param": (f"{symbol},day,{year}-01-01,{year + 1}-12-31,640,{adjust}"),
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
            rows.append(
                {
                    "date": item[0],
                    "open": item[1],
                    "close": item[2],
                    "high": item[3],
                    "low": item[4],
                    "volume": item[5],
                    "amount": amount,
                }
            )
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
    universe = (
        pd.read_parquet(universe_path)
        if universe_path.exists()
        else build_universe(config)
    )
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
            logging.info(
                "[%s/%s] bars %s %s", task_number, total_tasks, code, adjustment
            )
            frame = pd.DataFrame()
            try:
                frame = retry(
                    lambda: fetch_tencent_history(
                        tencent_session,
                        code,
                        symbol_start,
                        symbol_end,
                        adjustment,
                        tencent_throttle,
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
                            client,
                            code,
                            symbol_start,
                            symbol_end,
                            adjustment,
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
                            code,
                            symbol_start,
                            symbol_end,
                            adjustment,
                            eastmoney_throttle,
                        ),
                        f"fallback bars {code} {adjustment}",
                        attempts=config["retries"],
                        base_sleep=config["retry_base_seconds"],
                    )
                except Exception as exc:  # noqa: BLE001
                    failures.append(
                        {"code": code, "adjustment": adjustment, "error": str(exc)}
                    )
                    continue
            if frame.empty:
                failures.append(
                    {"code": code, "adjustment": adjustment, "error": "empty"}
                )
                continue
            atomic_parquet(frame, output)

    atomic_json(
        {"failures": failures, "updated_at": dt.datetime.now()},
        STATE / "bar_failures.json",
    )
    logging.info("Bar download finished with %s failed tasks", len(failures))


def repair_qfq_bars(
    config: Dict,
    limit: Optional[int] = None,
    codes: Optional[Sequence[str]] = None,
) -> None:
    """Rebuild only qfq files whose trading dates differ from raw bars."""
    if Quotes is None:
        raise RuntimeError(
            "MootDX repair requires a dedicated environment: pip install -r scripts/legacy/requirements-tdx.txt"
        )
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
                    client,
                    code,
                    start_date,
                    end_date,
                    "qfq",
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
        repaired,
        len(candidates),
        len(failures),
    )


def _fetch_bars_range(
    config: Dict,
    code: str,
    exchange: str,
    start: pd.Timestamp,
    end: pd.Timestamp,
    adjustment: str,
    client,
    tencent_session: requests.Session,
    tencent_throttle: Throttle,
    eastmoney_throttle: Throttle,
) -> pd.DataFrame:
    frame = pd.DataFrame()
    try:
        frame = retry(
            lambda: fetch_tencent_history(
                tencent_session,
                code,
                start,
                end,
                adjustment,
                tencent_throttle,
            ),
            f"Tencent bars {code} {adjustment}",
            attempts=config["retries"],
            base_sleep=config["retry_base_seconds"],
        )
    except Exception as exc:  # noqa: BLE001
        logging.warning("Tencent unavailable for %s: %s", code, exc)
    if frame.empty and client is not None and exchange in ("SH", "SZ"):
        try:
            client = client() if callable(client) else client
            frame = retry(
                lambda: fetch_tdx_history(
                    client,
                    code,
                    start,
                    end,
                    adjustment,
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
                    code,
                    start,
                    end,
                    adjustment,
                    eastmoney_throttle,
                ),
                f"fallback bars {code} {adjustment}",
                attempts=config["retries"],
                base_sleep=config["retry_base_seconds"],
            )
        except Exception as exc:  # noqa: BLE001
            logging.warning("fallback unavailable for %s: %s", code, exc)
    return frame


def _qfq_history_changed(existing: pd.DataFrame, new: pd.DataFrame) -> bool:
    """An overlap revision can reprice all older vendor qfq bars, including BJ.

    Vendor adjustment can be affine, so never repair the older part by applying
    a single ratio. Re-fetch the history when overlapping OHLC changes.
    """
    if existing.empty or new.empty:
        return False
    fields = ["open", "high", "low", "close"]
    overlap = existing[["date", *fields]].merge(
        new[["date", *fields]], on="date", suffixes=("_old", "_new")
    )
    if overlap.empty:
        return True
    for field in fields:
        old = pd.to_numeric(overlap[field + "_old"], errors="coerce")
        fresh = pd.to_numeric(overlap[field + "_new"], errors="coerce")
        if ((old - fresh).abs().gt(1e-6) | (old.isna() != fresh.isna())).any():
            return True
    return False


def update_bars_incremental(
    config: Dict,
    codes: Optional[Sequence[str]] = None,
    lookback_days: int = 5,
    qfq_refresh_days: int = 60,
    max_workers: int = 8,
) -> Set[str]:
    """Append raw bars; refetch vendor qfq history if its overlap was repriced."""
    universe = _listed_universe(codes)
    end_date = _target_asof(config)
    tencent_throttle = Throttle(0.12)
    eastmoney_throttle = Throttle(config["eastmoney_min_interval_seconds"])
    _local = threading.local()

    def _session():
        if not hasattr(_local, "session"):
            _local.session = requests.Session()
            _local.session.headers.update({"User-Agent": UA})
        return _local.session

    def _client():
        if not hasattr(_local, "client"):
            _local.client = Quotes.factory(market="std") if Quotes is not None else None
        return _local.client

    def _process_stock(row) -> tuple[str, bool, int, list]:
        code = str(row.code)
        stock_end = end_date
        delisted = getattr(row, "delist_date", None)
        if pd.notna(delisted):
            stock_end = min(stock_end, pd.Timestamp(delisted).normalize())
        repair_from = config.get("refresh_from")
        stock_updated = False
        stock_failures: list = []
        stock_skipped = 0
        for adjustment in config["bar_adjustments"]:
            if (
                config.get("derive_qfq")
                and adjustment == "qfq"
                and str(row.exchange) in ("SH", "SZ")
            ):
                continue  # qfq is rebuilt locally from raw + validated corporate events.
            output = bar_output_path(code, adjustment)
            last = _last_date(output)
            if (
                last is not None
                and last >= stock_end
                and not repair_from
                and not config.get("force_fetch")
            ):
                stock_skipped += 1
                continue
            if last is None:
                start = pd.Timestamp(config["start_date"])
                if pd.notna(row.list_date):
                    start = max(start, pd.Timestamp(row.list_date))
            elif adjustment == "qfq":
                start = last - pd.Timedelta(days=qfq_refresh_days)
            else:
                start = last - pd.Timedelta(days=lookback_days)
            if repair_from:
                start = min(start, pd.Timestamp(repair_from))
            start = max(start, pd.Timestamp(config["start_date"]))
            if pd.notna(row.list_date):
                start = max(start, pd.Timestamp(row.list_date))
            if start > stock_end:
                continue
            logging.info("update bars %s %s from %s", code, adjustment, start.date())
            try:
                new = _fetch_bars_range(
                    config,
                    code,
                    str(row.exchange),
                    start,
                    stock_end,
                    adjustment,
                    _client,
                    _session(),
                    tencent_throttle,
                    eastmoney_throttle,
                )
            except Exception as exc:  # noqa: BLE001
                stock_failures.append(
                    {"code": code, "adjustment": adjustment, "error": str(exc)}
                )
                continue
            if new.empty:
                stock_failures.append(
                    {"code": code, "adjustment": adjustment, "error": "empty"}
                )
                continue
            existing = pd.read_parquet(output) if output.exists() else pd.DataFrame()
            if adjustment == "qfq" and _qfq_history_changed(existing, new):
                history_start = pd.Timestamp(config["start_date"])
                if pd.notna(row.list_date):
                    history_start = max(history_start, pd.Timestamp(row.list_date))
                if history_start < start:
                    new = _fetch_bars_range(
                        config,
                        code,
                        str(row.exchange),
                        history_start,
                        stock_end,
                        adjustment,
                        _client,
                        _session(),
                        tencent_throttle,
                        eastmoney_throttle,
                    )
                    if new.empty:
                        stock_failures.append(
                            {
                                "code": code,
                                "adjustment": adjustment,
                                "error": "empty full qfq revision",
                            }
                        )
                        continue
                    expected_dates = pd.read_parquet(
                        bar_output_path(code, "raw"), columns=["date"]
                    )["date"]
                    if not pd.Index(new["date"]).equals(pd.Index(expected_dates)):
                        stock_failures.append(
                            {
                                "code": code,
                                "adjustment": adjustment,
                                "error": "full qfq revision does not cover raw dates",
                            }
                        )
                        continue
                    existing = pd.DataFrame()
            merged = _merge_by_date(existing, new)
            if atomic_parquet(merged, output):
                stock_updated = True
        return code, stock_updated, stock_skipped, stock_failures

    updated: Set[str] = set()
    failures: list = []
    skipped = 0
    total = len(universe)
    completed = 0

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {
            pool.submit(_process_stock, row): row
            for row in universe.itertuples(index=False)
        }
        for future in as_completed(futures):
            completed += 1
            code, was_updated, n_skipped, stock_failures = future.result()
            if was_updated:
                updated.add(code)
            skipped += n_skipped
            failures.extend(stock_failures)
            if completed % 200 == 0:
                logging.info(
                    "bars progress: %s/%s (%s failures so far)",
                    completed,
                    total,
                    len(failures),
                )

    atomic_json(
        {
            "updated_codes": sorted(updated),
            "failures": failures,
            "end_date": str(end_date.date()),
            "updated_at": dt.datetime.now(),
        },
        STATE / "daily_bar_update.json",
    )
    logging.info(
        "Bar incremental update: %s codes touched, %s skipped (already current), %s failures",
        len(updated),
        skipped,
        len(failures),
    )
    return updated
