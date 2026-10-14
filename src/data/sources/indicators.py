"""sources — indicators: data acquisition and normalization."""

from __future__ import annotations
import datetime as dt
import json
import logging
import threading
from collections import defaultdict
from pathlib import Path
from typing import Dict, Optional, Sequence, Set
import pandas as pd
from ashare_alpha.data.paths import PARQUET_ROOT as PARQUET, STATE_ROOT as STATE

try:
    import baostock as bs
except ImportError:
    bs = None

from ashare_alpha.data.incremental_files import _last_date
from ashare_alpha.data.incremental_files import _listed_universe
from ashare_alpha.data.incremental_files import _merge_by_date
from ashare_alpha.data.calendar import _target_asof
from ashare_alpha.data.storage import atomic_json, read_json
from ashare_alpha.data.storage import atomic_parquet


def build_bj_daily_indicators(
    config: Dict,
    limit: Optional[int] = None,
    codes: Optional[Sequence[str]] = None,
    force: bool = False,
    on_code_done=None,
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
            bars = pd.read_parquet(bar_path, columns=["date", "close", "amount"]).copy()
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
            if on_code_done is not None:
                on_code_done(code)
            built += 1
            if index % 100 == 0:
                logging.info(
                    "bj indicators progress: %s/%s built=%s",
                    index,
                    len(universe),
                    built,
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
        built,
        skipped,
        len(failures),
    )


def baostock_frame(result) -> pd.DataFrame:
    rows = []
    while result.error_code == "0" and result.next():
        rows.append(result.get_row_data())
    if result.error_code != "0":
        raise RuntimeError(f"BaoStock {result.error_code}: {result.error_msg}")
    return pd.DataFrame(rows, columns=result.fields)


def query_baostock_frame(query, *, attempts=3):
    """Recover expired sessions and broken sockets, discarding partial results."""
    for attempt in range(attempts):
        result = query()
        try:
            return baostock_frame(result)
        except RuntimeError:
            recoverable = {"10001001", "10002007"}
            if result.error_code not in recoverable or attempt + 1 >= attempts:
                raise
            # A receive error leaves the global socket unusable. Login creates
            # a new connection; close the old one without sending logout over it.
            if result.error_code == "10002007":
                import baostock.common.context as context

                connection = getattr(context, "default_socket", None)
                if connection is not None:
                    connection.close()
                    context.default_socket = None
            login = bs.login()
            if login.error_code != "0":
                raise RuntimeError(f"BaoStock re-login failed: {login.error_msg}")
    raise RuntimeError("BaoStock query has no attempts")


def build_daily_indicators(
    config: Dict,
    limit: Optional[int] = None,
    codes: Optional[Sequence[str]] = None,
    force: bool = False,
) -> None:
    """Build daily tradability, ST and valuation fields for SH/SZ securities."""
    if bs is None:
        raise RuntimeError(
            "baostock is required; install the data extra: pip install -e '.[data]'"
        )
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
        "date,code,turn,tradestatus,pctChg,peTTM,pbMRQ,psTTM,pcfNcfTTM,isST,preclose"
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
                    "turn",
                    "pctChg",
                    "peTTM",
                    "pbMRQ",
                    "psTTM",
                    "pcfNcfTTM",
                    "preclose",
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


def _process_indicator_stock(
    row,
    end_date: pd.Timestamp,
    config: Dict,
    lookback_days: int,
    fields: str,
    _lock: threading.Lock,
    _counter: list,
    _total: int,
) -> tuple[str, bool, int, dict | None]:
    """Process one stock using its worker process's BaoStock session."""
    import baostock as _bs

    code = str(row.code)
    output = PARQUET / "daily_indicators" / f"{code}.parquet"
    last = _last_date(output)
    delisted = getattr(row, "delist_date", None)
    if pd.notna(delisted):
        end_date = min(end_date, pd.Timestamp(delisted).normalize())
    repair_from = config.get("refresh_from")
    gap_from = config.get("_indicator_history_gaps", {}).get(code)
    if gap_from:
        repair_from = min(str(repair_from), str(gap_from)) if repair_from else gap_from
    if (
        last is not None
        and last >= end_date
        and not repair_from
        and not config.get("force_fetch")
    ):
        return code, False, 1, None  # skipped

    start = (
        (last - pd.Timedelta(days=lookback_days))
        if last is not None
        else pd.Timestamp(config["start_date"])
    )
    if repair_from:
        start = min(start, pd.Timestamp(repair_from))
    start = max(start, pd.Timestamp(config["start_date"]))
    if pd.notna(row.list_date):
        start = max(start, pd.Timestamp(row.list_date))
    if start > end_date:
        return code, False, 0, None

    prefix = "sh" if row.exchange == "SH" else "sz"

    # BaoStock owns a process-global socket. Each worker logs in once.
    try:
        frame = query_baostock_frame(
            lambda: _bs.query_history_k_data_plus(
                f"{prefix}.{code}",
                fields,
                start_date=start.strftime("%Y-%m-%d"),
                end_date=end_date.strftime("%Y-%m-%d"),
                frequency="d",
                adjustflag="3",
            ),
            attempts=config.get("retries", 3),
        )

        if frame.empty:
            return code, False, 0, {"code": code, "error": "empty"}

        frame["date"] = pd.to_datetime(frame["date"], errors="coerce")
        frame["code"] = code
        frame["preclose"] = pd.to_numeric(frame["preclose"], errors="coerce")
        for column in ("turn", "pctChg", "peTTM", "pbMRQ", "psTTM", "pcfNcfTTM"):
            frame[column] = pd.to_numeric(frame[column], errors="coerce")
        for column in ("tradestatus", "isST"):
            frame[column] = pd.to_numeric(frame[column], errors="coerce").astype("Int8")
        frame["source"] = "baostock"
        existing = pd.read_parquet(output) if output.exists() else pd.DataFrame()
        merged = _merge_by_date(existing, frame)
        atomic_parquet(merged, output)

        with _lock:
            _counter[0] += 1
            if _counter[0] % 200 == 0:
                logging.info("indicators progress: %s/%s", _counter[0], _total)

        return code, True, 0, None
    except Exception as exc:  # noqa: BLE001
        return code, False, 0, {"code": code, "error": str(exc)}


def indicator_history_gaps(universe, config, end_date, *, with_dates=False):
    """Find raw dates missing from indicator assets, including older omissions."""
    import duckdb

    raw_files = list((PARQUET / "daily_bars/adjustment=raw").glob("*.parquet"))
    indicator_files = list((PARQUET / "daily_indicators").glob("*.parquet"))
    if not raw_files or not indicator_files:
        return {}
    raw = str(PARQUET / "daily_bars/adjustment=raw/*.parquet").replace("'", "''")
    indicators = str(PARQUET / "daily_indicators/*.parquet").replace("'", "''")
    aggregate = "list(a.date ORDER BY a.date)" if with_dates else "min(a.date)"
    with duckdb.connect() as con:
        con.register("selected", universe[["code"]])
        rows = con.execute(
            f"""SELECT a.code,{aggregate} FROM read_parquet('{raw}',union_by_name=true) a
            JOIN selected s ON a.code=s.code
            ANTI JOIN read_parquet('{indicators}',union_by_name=true) b
            ON a.code=b.code AND a.date=b.date
            WHERE a.date >= ? AND a.date <= ? GROUP BY a.code""",
            [pd.Timestamp(config["start_date"]), end_date],
        ).fetchall()
    if with_dates:
        return {
            str(code): [str(pd.Timestamp(date).date()) for date in dates]
            for code, dates in rows
        }
    return {str(code): str(pd.Timestamp(date).date()) for code, date in rows}


def update_indicators_incremental(
    config: Dict,
    codes: Optional[Sequence[str]] = None,
    lookback_days: int = 5,
    max_workers: int = 8,
) -> Set[str]:
    """Extend BaoStock daily indicators for SH/SZ listed names (parallel)."""
    if bs is None:
        raise RuntimeError(
            "baostock is required; install the data extra: pip install -e '.[data]'"
        )
    universe = _listed_universe(codes)
    universe = universe[universe["exchange"].isin(["SH", "SZ"])].copy()
    end_date = _target_asof(config)
    config = {
        **config,
        "_indicator_history_gaps": indicator_history_gaps(universe, config, end_date),
    }
    fields = (
        "date,code,turn,tradestatus,pctChg,peTTM,pbMRQ,psTTM,pcfNcfTTM,isST,preclose"
    )
    updated: Set[str] = set()
    failures: list = []
    skipped = 0

    from concurrent.futures import ProcessPoolExecutor

    rows = []
    for row in universe.to_dict("records"):
        last = _last_date(PARQUET / "daily_indicators" / f"{row['code']}.parquet")
        if (
            last is not None
            and last >= end_date
            and not config.get("refresh_from")
            and not config.get("force_fetch")
            and row["code"] not in config["_indicator_history_gaps"]
        ):
            skipped += 1
        else:
            rows.append(row)
    if rows:
        worker_count = min(max(1, max_workers), 4, len(rows))
        chunks = [rows[i::worker_count] for i in range(worker_count)]
        import multiprocessing

        with ProcessPoolExecutor(
            max_workers=worker_count, mp_context=multiprocessing.get_context("spawn")
        ) as pool:
            for batch in pool.map(
                _indicator_batch,
                [(chunk, end_date, config, lookback_days, fields) for chunk in chunks],
            ):
                for code, was_updated, n_skipped, failure in batch:
                    if was_updated:
                        updated.add(code)
                    skipped += n_skipped
                    if failure:
                        failures.append(failure)
    from ..pipeline.receipts import fingerprint

    gap_path = STATE / "indicator_source_gaps.json"
    source_gaps = read_json(gap_path, {})
    remaining = indicator_history_gaps(universe, config, end_date, with_dates=True)
    for code in updated:
        if code not in remaining:
            source_gaps.pop(code, None)
        else:
            source_gaps[code] = {
                "checked_through": str(end_date.date()),
                "source": "baostock",
                "missing_dates": remaining[code],
                "raw": fingerprint(
                    [PARQUET / "daily_bars/adjustment=raw" / f"{code}.parquet"]
                ),
                "indicator": fingerprint(
                    [PARQUET / "daily_indicators" / f"{code}.parquet"]
                ),
            }
    atomic_json(source_gaps, gap_path)
    atomic_json(
        {
            "source_missing_dates": {
                code: remaining[code] for code in sorted(updated) if code in remaining
            },
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


def _indicator_batch(arguments):
    from types import SimpleNamespace

    rows, end_date, config, lookback_days, fields = arguments
    login = bs.login()
    if login.error_code != "0":
        raise RuntimeError(f"BaoStock login: {login.error_msg}")
    try:
        lock, counter = threading.Lock(), [0]
        return [
            _process_indicator_stock(
                SimpleNamespace(**row),
                end_date,
                config,
                lookback_days,
                fields,
                lock,
                counter,
                len(rows),
            )
            for row in rows
        ]
    finally:
        bs.logout()
