#!/usr/bin/env python3
"""Incremental daily updater for the A-share research dataset.

Unlike the initial builders (which skip any existing per-code file), this module
extends history from each file's last date to today, refreshes a short lookback
window for adjustable series, then rebuilds derived layers and the research
warehouse year partitions.
"""
from __future__ import annotations

import datetime as dt
import logging
import re
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Set

import pandas as pd
import requests

try:
    from mootdx.quotes import Quotes
except ImportError:  # pragma: no cover
    Quotes = None

try:
    import baostock as bs
except ImportError:  # pragma: no cover
    bs = None

from build_dataset import (
    PARQUET,
    ROOT,
    STATE,
    Throttle,
    UA,
    atomic_json,
    atomic_parquet,
    baostock_frame,
    bar_output_path,
    build_bj_daily_indicators,
    build_limit_prices,
    build_qfq_from_corporate_actions,
    build_shares_daily,
    build_snapshot,
    fetch_akshare_history,
    fetch_tdx_history,
    fetch_tencent_history,
    load_config,
    retry,
    setup_logging,
    validate_dataset,
)


def _listed_universe(codes: Optional[Sequence[str]] = None) -> pd.DataFrame:
    master = pd.read_parquet(PARQUET / "security_master" / "security_master.parquet")
    universe = master[master["status"].eq("listed")].copy()
    if codes:
        universe = universe[universe["code"].isin(set(codes))]
    return universe.sort_values(["exchange", "code"]).reset_index(drop=True)


def _merge_by_date(existing: pd.DataFrame, new: pd.DataFrame) -> pd.DataFrame:
    if existing.empty:
        return new.copy()
    if new.empty:
        return existing.copy()
    columns = existing.columns.union(new.columns, sort=False)
    frame = pd.concat(
        [
            existing.dropna(axis="columns", how="all"),
            new.dropna(axis="columns", how="all"),
        ],
        ignore_index=True,
    ).reindex(columns=columns)
    frame["date"] = pd.to_datetime(frame["date"]).dt.normalize()
    return (
        frame.drop_duplicates("date", keep="last")
        .sort_values("date")
        .reset_index(drop=True)
    )


def _last_date(path: Path) -> Optional[pd.Timestamp]:
    if not path.exists():
        return None
    dates = pd.to_datetime(pd.read_parquet(path, columns=["date"])["date"], errors="coerce")
    if dates.dropna().empty:
        return None
    return pd.Timestamp(dates.max()).normalize()


def _target_asof(config: Dict) -> pd.Timestamp:
    """Latest trading day on/before configured end (default: today).

    Weekend/holiday re-runs must not treat calendar today as a missing bar day.
    """
    today = pd.Timestamp(config.get("end_date") or dt.date.today()).normalize()
    cal_path = PARQUET / "reference" / "trade_calendar.parquet"
    if cal_path.exists():
        cal = pd.read_parquet(cal_path, columns=["calendar_date", "is_trading_day"])
        days = pd.to_datetime(
            cal.loc[cal["is_trading_day"].astype(int).eq(1), "calendar_date"],
            errors="coerce",
        ).dropna()
        days = days[days <= today]
        if len(days):
            return pd.Timestamp(days.max()).normalize()
    return today


def _bars_already_at(
    asof: pd.Timestamp,
    sample_codes: Sequence[str] = ("000001", "600519", "300750"),
) -> bool:
    """Fast probe: liquid names already have bars through ``asof``."""
    for code in sample_codes:
        last = _last_date(bar_output_path(str(code), "qfq"))
        if last is None or last < asof:
            return False
    return True


def _indicators_already_at(
    asof: pd.Timestamp,
) -> bool:
    """Return whether every listed SH/SZ symbol has indicators through ``asof``."""
    universe = _listed_universe()
    codes = universe.loc[
        universe["exchange"].isin(["SH", "SZ"]), "code"
    ].astype(str)
    if codes.empty:
        return False
    for code in codes:
        last = _last_date(PARQUET / "daily_indicators" / f"{code}.parquet")
        if last is None or last < asof:
            return False
    return True


def _stock_daily_asof() -> Optional[pd.Timestamp]:
    """Max date in the catalog's stable stock_daily view."""
    try:
        import duckdb
        from research_layer import CATALOG
    except Exception:  # noqa: BLE001
        return None
    if not CATALOG.exists():
        return None
    try:
        con = duckdb.connect(str(CATALOG), read_only=True)
        try:
            row = con.execute("SELECT max(CAST(date AS DATE)) FROM stock_daily").fetchone()
        finally:
            con.close()
    except Exception:  # noqa: BLE001
        return None
    if not row or row[0] is None:
        return None
    return pd.Timestamp(row[0]).normalize()


def _sync_research_layer_for_asof(config: Dict, end_date: pd.Timestamp) -> None:
    """Refresh warehouse + catalog when bars are current but stock_daily lags.

    Share counts may be carried forward, while dated market caps are derived
    from current amount/turnover and exchange limits require exact-date rows.
    """
    from research_layer import build_catalog

    print(
        f"Research sync → {end_date.date()}: warehouse (from parquet) + catalog",
        flush=True,
    )
    cfg = dict(config)
    cfg["end_date"] = str(end_date.date())
    update_warehouse_current_year(cfg)
    build_catalog(cfg)
    print(f"Research sync done; stock_daily asof → {_stock_daily_asof()}", flush=True)


def _warehouse_limits_cover_year(asof: pd.Timestamp) -> bool:
    """Return whether every warehouse raw-bar key has an exact limit row."""
    try:
        import duckdb
        from research_layer import WAREHOUSE, assert_warehouse_limit_coverage

        connection = duckdb.connect()
        try:
            raw_root = WAREHOUSE / "daily_bars_raw"
            raw_asof = connection.execute(
                f"""
                SELECT max(CAST(date AS DATE))
                FROM read_parquet(
                    '{raw_root.as_posix()}/**/*.parquet',
                    hive_partitioning=true,
                    union_by_name=true
                )
                WHERE year(CAST(date AS DATE)) = {asof.year}
                """
            ).fetchone()[0]
            if raw_asof is None or pd.Timestamp(raw_asof) < asof:
                raise RuntimeError(
                    f"warehouse raw bars stop at {raw_asof}, before {asof.date()}"
                )
            assert_warehouse_limit_coverage(
                connection,
                WAREHOUSE,
                year=asof.year,
            )
        finally:
            connection.close()
    except Exception as exc:  # noqa: BLE001
        logging.info(
            "Warehouse limit-price coverage is incomplete for year=%s: %s",
            asof.year,
            exc,
        )
        return False
    return True


def _finish_if_daily_layers_current(
    config: Dict,
    end_date: pd.Timestamp,
    *,
    after_calendar_refresh: bool,
) -> Optional[dict]:
    """Skip remaining work when bars, indicators, and research view are current."""
    bars_current = _bars_already_at(end_date)
    indicators_current = _indicators_already_at(end_date)
    limits_current = (
        _warehouse_limits_cover_year(end_date)
        if bars_current and indicators_current
        else False
    )
    if not (bars_current and indicators_current and limits_current):
        if bars_current and not indicators_current:
            position = "after calendar refresh" if after_calendar_refresh else ""
            logging.info(
                "Bars current %s but daily indicators lag; continue update",
                position,
            )
        elif bars_current and indicators_current and not limits_current:
            logging.info(
                "Bars and indicators are current but exact limit-price coverage "
                "is incomplete; continue update"
            )
        return None

    warehouse_date = _stock_daily_asof()
    synced = warehouse_date is None or warehouse_date < end_date
    if synced:
        logging.info(
            "Daily layers current but stock_daily asof=%s; sync research layer to %s",
            warehouse_date.date() if warehouse_date is not None else None,
            end_date.date(),
        )
        _sync_research_layer_for_asof(config, end_date)
        warehouse_date = _stock_daily_asof()

    if after_calendar_refresh:
        reason = "bars_already_current_after_calendar"
    elif synced:
        reason = "bars_current_research_synced"
    else:
        reason = "bars_and_stock_daily_current"
    payload = {
        "end_date": str(end_date.date()),
        "skipped": True,
        "reason": reason,
        "stock_daily_asof": (
            str(warehouse_date.date()) if warehouse_date is not None else None
        ),
        "updated_at": dt.datetime.now(),
    }
    atomic_json(payload, STATE / "daily_update.json")
    logging.info(
        "Skip remaining update-daily: bars + indicators + stock_daily ready through %s",
        end_date.date(),
    )
    return payload


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
                tencent_session, code, start, end, adjustment, tencent_throttle,
            ),
            f"Tencent bars {code} {adjustment}",
            attempts=config["retries"],
            base_sleep=config["retry_base_seconds"],
        )
    except Exception as exc:  # noqa: BLE001
        logging.warning("Tencent unavailable for %s: %s", code, exc)
    if frame.empty and client is not None and exchange in ("SH", "SZ"):
        try:
            frame = retry(
                lambda: fetch_tdx_history(
                    client, code, start, end, adjustment, config["bar_page_size"],
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
                    code, start, end, adjustment, eastmoney_throttle,
                ),
                f"fallback bars {code} {adjustment}",
                attempts=config["retries"],
                base_sleep=config["retry_base_seconds"],
            )
        except Exception as exc:  # noqa: BLE001
            logging.warning("fallback unavailable for %s: %s", code, exc)
    return frame


def update_bars_incremental(
    config: Dict,
    codes: Optional[Sequence[str]] = None,
    lookback_days: int = 5,
    qfq_refresh_days: int = 60,
    max_workers: int = 4,
) -> Set[str]:
    """Append new raw bars; refresh a qfq lookback window for factor changes."""
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
        stock_updated = False
        stock_failures: list = []
        stock_skipped = 0
        for adjustment in config["bar_adjustments"]:
            output = bar_output_path(code, adjustment)
            last = _last_date(output)
            if last is not None and last >= end_date:
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
            if pd.notna(row.list_date):
                start = max(start, pd.Timestamp(row.list_date))
            if start > end_date:
                continue
            logging.info("update bars %s %s from %s", code, adjustment, start.date())
            try:
                new = _fetch_bars_range(
                    config, code, str(row.exchange), start, end_date, adjustment,
                    _client(), _session(), tencent_throttle, eastmoney_throttle,
                )
            except Exception as exc:  # noqa: BLE001
                stock_failures.append({"code": code, "adjustment": adjustment, "error": str(exc)})
                continue
            if new.empty:
                stock_failures.append({"code": code, "adjustment": adjustment, "error": "empty"})
                continue
            existing = pd.read_parquet(output) if output.exists() else pd.DataFrame()
            merged = _merge_by_date(existing, new)
            if existing.empty or len(merged) != len(existing) or (
                not existing.empty
                and pd.Timestamp(merged["date"].max()) != pd.Timestamp(existing["date"].max())
            ):
                stock_updated = True
            atomic_parquet(merged, output)
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
                logging.info("bars progress: %s/%s (%s failures so far)", completed, total, len(failures))

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


def update_indicators_incremental(
    config: Dict,
    codes: Optional[Sequence[str]] = None,
    lookback_days: int = 5,
) -> Set[str]:
    """Extend BaoStock daily indicators for SH/SZ listed names."""
    if bs is None:
        raise RuntimeError("baostock is required; install requirements.txt")
    universe = _listed_universe(codes)
    universe = universe[universe["exchange"].isin(["SH", "SZ"])].copy()
    end_date = _target_asof(config)
    fields = (
        "date,code,turn,tradestatus,pctChg,peTTM,pbMRQ,"
        "psTTM,pcfNcfTTM,isST"
    )
    login = bs.login()
    if login.error_code != "0":
        raise RuntimeError(f"BaoStock login failed: {login.error_msg}")
    updated: Set[str] = set()
    failures = []
    skipped = 0
    try:
        for index, row in enumerate(universe.itertuples(index=False), start=1):
            if index > 1 and (index - 1) % 250 == 0:
                bs.logout()
                login = bs.login()
                if login.error_code != "0":
                    raise RuntimeError(f"BaoStock re-login failed: {login.error_msg}")
            code = str(row.code)
            output = PARQUET / "daily_indicators" / f"{code}.parquet"
            last = _last_date(output)
            if last is not None and last >= end_date:
                skipped += 1
                continue
            start = (
                (last - pd.Timedelta(days=lookback_days))
                if last is not None
                else pd.Timestamp(config["start_date"])
            )
            if pd.notna(row.list_date):
                start = max(start, pd.Timestamp(row.list_date))
            if start > end_date:
                continue
            logging.info("[%s/%s] update indicators %s", index, len(universe), code)
            prefix = "sh" if row.exchange == "SH" else "sz"

            def query():
                return bs.query_history_k_data_plus(
                    f"{prefix}.{code}",
                    fields,
                    start_date=start.strftime("%Y-%m-%d"),
                    end_date=end_date.strftime("%Y-%m-%d"),
                    frequency="d",
                    adjustflag="3",
                )

            try:
                result = query()
                if result.error_code == "10001001":
                    login = bs.login()
                    result = query()
                frame = baostock_frame(result)
                if frame.empty:
                    failures.append({"code": code, "error": "empty"})
                    continue
                frame["date"] = pd.to_datetime(frame["date"], errors="coerce")
                frame["code"] = code
                for column in ("turn", "pctChg", "peTTM", "pbMRQ", "psTTM", "pcfNcfTTM"):
                    frame[column] = pd.to_numeric(frame[column], errors="coerce")
                for column in ("tradestatus", "isST"):
                    frame[column] = pd.to_numeric(frame[column], errors="coerce").astype("Int8")
                frame["source"] = "baostock"
                existing = (
                    pd.read_parquet(output) if output.exists() else pd.DataFrame()
                )
                merged = _merge_by_date(existing, frame)
                atomic_parquet(merged, output)
                updated.add(code)
            except Exception as exc:  # noqa: BLE001
                failures.append({"code": code, "error": str(exc)})
    finally:
        bs.logout()
    atomic_json(
        {
            "updated_codes": sorted(updated),
            "failures": failures,
            "updated_at": dt.datetime.now(),
        },
        STATE / "daily_indicator_update.json",
    )
    logging.info(
        "Indicator incremental update: %s codes, %s skipped (already current), %s failures",
        len(updated),
        skipped,
        len(failures),
    )
    return updated


def refresh_trade_calendar(config: Dict) -> None:
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
    finally:
        bs.logout()
    logging.info("Trade calendar refreshed through %s", end_date.date())


def update_warehouse_current_year(config: Dict) -> None:
    """Rewrite only the current-year warehouse partitions + stock_daily year."""
    try:
        import duckdb
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError("duckdb is required for warehouse update") from exc

    year = _target_asof(config).year
    con = duckdb.connect()
    try:
        from research_layer import (
            WAREHOUSE,
            assert_warehouse_limit_coverage,
            compact_warehouse_layer,
            warehouse_layer_specs,
            warehouse_shares_sql,
        )

        for spec in warehouse_layer_specs(PARQUET):
            logging.info("warehouse refreshing %s year=%s", spec.name, year)
            compact_warehouse_layer(con, spec, WAREHOUSE, year=year)

        assert_warehouse_limit_coverage(con, WAREHOUSE, year=year)

        panel = WAREHOUSE / "stock_daily"
        panel.mkdir(parents=True, exist_ok=True)
        for path in panel.glob(f"year={year}"):
            import shutil

            if path.is_dir():
                shutil.rmtree(path)
        logging.info("warehouse refreshing stock_daily year=%s", year)
        # Share counts may remain valid across dates, but dated market caps and
        # exchange limits must never be carried forward.
        shares_columns, shares_join = warehouse_shares_sql(
            WAREHOUSE, asof=True
        )

        con.execute(
            f"""
            COPY (
              SELECT
                b.code,
                CAST(b.date AS DATE) AS date,
                b.exchange,
                b.open, b.high, b.low, b.close, b.volume, b.amount,
                i.turn AS turnover_pct,
                CAST(i.tradestatus AS BOOLEAN) AS trade_status,
                i.pctChg AS pct_change,
                i.peTTM AS pe_ttm,
                i.pbMRQ AS pb_mrq,
                i.psTTM AS ps_ttm,
                i.pcfNcfTTM AS pcf_ncf_ttm,
                CAST(i.isST AS BOOLEAN) AS is_st,
                {shares_columns}
                l.limit_up,
                l.limit_down,
                l.limit_pct,
                l.is_limit_up,
                l.is_limit_down,
                year(CAST(b.date AS DATE)) AS year,
                b.exchange AS exchange_part
              FROM (
                SELECT *
                FROM read_parquet('{(WAREHOUSE / "daily_bars_qfq").as_posix()}/**/*.parquet', hive_partitioning=true, union_by_name=true)
                WHERE year(CAST(date AS DATE)) = {year}
              ) b
              LEFT JOIN read_parquet('{(WAREHOUSE / "daily_indicators").as_posix()}/**/*.parquet', hive_partitioning=true, union_by_name=true) i
                ON b.code = i.code AND CAST(b.date AS DATE) = CAST(i.date AS DATE)
              {shares_join}
              LEFT JOIN (
                SELECT code, CAST(date AS DATE) AS date,
                       limit_up, limit_down, limit_pct, is_limit_up, is_limit_down
                FROM read_parquet('{(WAREHOUSE / "limit_prices").as_posix()}/**/*.parquet', hive_partitioning=true, union_by_name=true)
              ) l
                ON b.code = l.code AND CAST(b.date AS DATE) = l.date
            )
            TO '{panel.as_posix()}'
            (FORMAT PARQUET, PARTITION_BY (year, exchange_part), OVERWRITE_OR_IGNORE)
            """
        )
    finally:
        con.close()
    logging.info("Warehouse current-year refresh done")


def update_financials_incremental(
    config: Dict,
    codes: Optional[Sequence[str]] = None,
    periods: int = 12,
    max_workers: int = 4,
) -> Set[str]:
    """Fetch recent statement periods and merge into existing financial files."""
    from build_dataset import deduplicate_financial_frame, fetch_sina_statement

    universe = _listed_universe(codes)
    throttle = Throttle(config["sina_min_interval_seconds"])
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
        frames = []
        stock_failures: list = []
        for report_type in ("fzb", "lrb", "llb"):
            try:
                frame = retry(
                    lambda rt=report_type: fetch_sina_statement(
                        _session(), code, rt, periods, throttle
                    ),
                    f"Sina {report_type} {code}",
                    attempts=config["retries"],
                )
                if not frame.empty:
                    frames.append(frame)
            except Exception as exc:  # noqa: BLE001
                stock_failures.append({"code": code, "type": report_type, "error": str(exc)})
        if not frames:
            stock_failures.append({"code": code, "type": "all", "error": "empty"})
            return code, False, stock_failures
        new = pd.concat(frames, ignore_index=True)
        existing = pd.read_parquet(output) if output.exists() else pd.DataFrame()
        merged, conflicts = deduplicate_financial_frame(
            pd.concat([existing, new], ignore_index=True)
            if not existing.empty
            else new
        )
        atomic_parquet(merged, output)
        if conflicts:
            stock_failures.append(
                {
                    "code": code,
                    "type": "duplicate_value_conflicts",
                    "error": str(conflicts),
                }
            )
        return code, True, stock_failures

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


def update_announcements_incremental(
    config: Dict,
    codes: Optional[Sequence[str]] = None,
    lookback_days: int = 180,
    max_workers: int = 4,
) -> Set[str]:
    """Refresh recent financial-report announcements and merge by announcement_id."""
    from build_dataset import CNINFO_QUERY_URL, resolve_cninfo_org_id

    end = pd.Timestamp(config["end_date"] or dt.date.today()).normalize()
    start = end - pd.Timedelta(days=lookback_days)
    universe = _listed_universe(codes)
    throttle = Throttle(0.8)
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
        logging.info("update announcements %s", code)
        stock_failures: list = []
        try:
            org_id = retry(
                lambda: resolve_cninfo_org_id(_session(), code, throttle),
                f"CNInfo orgId {code}",
                attempts=config["retries"],
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
            "seDate": f"{start.date()}~{end.date()}",
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
                if not items or not data.get("hasMore", False):
                    break
                page_number += 1
                if page_number > 20:
                    break
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
                return code, False, stock_failures
            new = pd.DataFrame(rows)
            existing = (
                pd.read_parquet(output) if output.exists() else pd.DataFrame()
            )
            merged = pd.concat([existing, new], ignore_index=True)
            merged = merged.drop_duplicates("announcement_id", keep="last")
            merged = merged.sort_values("announcement_date").reset_index(drop=True)
            atomic_parquet(merged, output)
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
            "window": f"{start.date()}~{end.date()}",
            "updated_at": dt.datetime.now(),
        },
        STATE / "daily_announcement_update.json",
    )
    logging.info(
        "Announcement incremental update: %s codes, %s failures",
        len(updated), len(failures),
    )
    return updated


def run_daily_update(
    config: Dict,
    codes: Optional[Sequence[str]] = None,
    skip_warehouse: bool = False,
    skip_slow: bool = False,
    force: bool = False,
    max_workers: int = 4,
) -> dict:
    """One-command refresh to keep the research DB current through today.

    Covers listed-universe changes, daily market data, corporate events,
    equity structure, recent financials/announcements, derived tables, and the
    DuckDB research layer. Slow Eastmoney/CNInfo/Sina layers can be skipped with
    skip_slow=True for a fast bars-only evening pass.

    If liquid bars already cover the latest trading day (and ``force`` is False),
    the whole update is skipped — re-running the same evening is a no-op.
    """
    from build_dataset import (
        build_corporate_action_events,
        build_corporate_actions,
        build_equity_structure,
        build_listing_dates,
        build_reference_data,
        build_reference_intervals,
        build_universe,
    )
    from research_layer import rebuild_financial_availability

    setup_logging()
    end_date = _target_asof(config)
    logging.info("Daily update starting; target asof=%s", end_date.date())

    if not force and codes is None:
        current_payload = _finish_if_daily_layers_current(
            config,
            end_date,
            after_calendar_refresh=False,
        )
        if current_payload is not None:
            return current_payload

    # 1) Universe / calendar
    logging.info("Step 1/10 universe + listing dates + calendar")
    build_universe(config)
    build_listing_dates(config)
    refresh_trade_calendar(config)
    # Calendar may extend; recompute asof after refresh.
    end_date = _target_asof(config)
    if not force and codes is None:
        current_payload = _finish_if_daily_layers_current(
            config,
            end_date,
            after_calendar_refresh=True,
        )
        if current_payload is not None:
            return current_payload

    # 2) Daily market facts
    logging.info("Step 2/10 bars + indicators")
    bar_codes = update_bars_incremental(config, codes=codes, max_workers=max_workers)
    ind_codes = update_indicators_incremental(config, codes=codes)

    listed = _listed_universe(codes)
    listed_codes = listed["code"].astype(str).tolist()

    if not skip_slow:
        # 3) Event / capital / statement layers (network, slower)
        logging.info("Step 3/10 corporate actions (force listed)")
        build_corporate_actions(config, codes=listed_codes, force=True)
        build_corporate_action_events(
            config,
            codes=listed_codes,
            force=True,
            data_cutoff=end_date,
            max_workers=max_workers,
        )
        logging.info("Step 4/10 equity structure (force listed)")
        build_equity_structure(config, codes=listed_codes, force=True)
        logging.info("Step 5/10 financials (recent periods merge)")
        update_financials_incremental(config, codes=listed_codes, periods=12, max_workers=max_workers)
        logging.info("Step 6/10 announcements (recent window merge)")
        update_announcements_incremental(config, codes=listed_codes, lookback_days=180, max_workers=max_workers)
        logging.info("Step 7/10 financial availability")
        rebuild_financial_availability(config)
    else:
        logging.warning(
            "skip_slow=True: corporate/equity/financials/announcements not refreshed"
        )
        build_corporate_action_events(
            config,
            codes=listed_codes,
            force=False,
            data_cutoff=end_date,
            max_workers=max_workers,
        )

    # 4) Derived daily tables
    logging.info("Step 8/10 derived indicators / limits / shares")
    bj_codes = listed.loc[listed.exchange.eq("BJ"), "code"].astype(str).tolist()
    if codes is not None:
        wanted = set(codes)
        bj_codes = [c for c in bj_codes if c in wanted]
    build_bj_daily_indicators(config, codes=bj_codes or None, force=True)
    try:
        from research_layer import enrich_bj_indicators_with_valuation

        enrich_bj_indicators_with_valuation(config, codes=bj_codes or None)
    except Exception as exc:  # noqa: BLE001
        logging.warning("BJ valuation refresh skipped: %s", exc)

    # After equity refresh, rebuild shares for all listed to catch unlocks.
    build_qfq_from_corporate_actions(
        config, codes=listed_codes, force=True
    )
    build_limit_prices(config, codes=listed_codes, force=True)
    build_shares_daily(config, codes=listed_codes, force=True)

    # 5) Reference snapshots (industry/index) + intervals
    logging.info("Step 9/10 reference snapshots")
    try:
        build_reference_data(config)
        build_reference_intervals(config)
    except Exception as exc:  # noqa: BLE001
        logging.warning("Reference refresh skipped: %s", exc)

    try:
        build_snapshot(config)
    except Exception as exc:  # noqa: BLE001
        logging.warning("Snapshot refresh skipped: %s", exc)

    # 6) Research layer
    logging.info("Step 10/10 warehouse + catalog + validate")
    if not skip_warehouse:
        update_warehouse_current_year(config)
        try:
            from research_layer import build_catalog

            build_catalog(config)
        except Exception as exc:  # noqa: BLE001
            logging.warning("Catalog refresh skipped: %s", exc)

    validate_dataset(config)
    payload = {
        "end_date": str(end_date.date()),
        "skipped": False,
        "bar_updated": len(bar_codes),
        "indicator_updated": len(ind_codes),
        "listed_codes": len(listed_codes),
        "skip_slow": skip_slow,
        "updated_at": dt.datetime.now(),
        "layers": [
            "universe",
            "listing_dates",
            "trade_calendar",
            "daily_bars",
            "daily_indicators",
            "corporate_actions" if not skip_slow else None,
            "equity_structure" if not skip_slow else None,
            "financials" if not skip_slow else None,
            "announcements" if not skip_slow else None,
            "financial_availability" if not skip_slow else None,
            "bj_indicators",
            "limit_prices",
            "shares_daily",
            "reference",
            "market_snapshots",
            "warehouse",
            "catalog",
        ],
    }
    atomic_json(payload, STATE / "daily_update.json")
    logging.info("Daily update finished for asof=%s", end_date.date())
    return payload
