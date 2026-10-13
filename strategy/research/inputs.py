"""Audited, shared-lock reads of canonical data for offline research."""

from contextlib import contextmanager
from pathlib import Path
import fcntl
import json
import duckdb
import pandas as pd
from backtest import MarketData
from backtest.models import timestamp, symbol
from data.ashare_data.pipeline.status import read_status

from ashare_alpha.paths import CODE_ROOT, DATA_ROOT

REPO = CODE_ROOT
DEFAULT_CATALOG = DATA_ROOT / "catalog/quant.duckdb"
RAW_FIELDS = (
    "exchange",
    "open",
    "high",
    "low",
    "close",
    "amount",
    "volume",
    "trade_status",
    "is_st",
    "float_market_cap",
    "total_market_cap",
    "turnover_pct",
    "limit_up",
    "limit_down",
    "limit_validation_status",
    "corporate_action_adjusted",
    "corporate_action_formula_status",
    "cash_dividend_per_share",
    "share_distribution_ratio",
    "rights_issue_ratio",
    "rights_issue_price",
)


@contextmanager
def catalog_reader(path=DEFAULT_CATALOG):
    path = Path(path).resolve()
    root = path.parent.parent
    status = read_status(state_root=root / "state", data_root=root)
    if status["writer_active"] or not status.get("quality", {}).get(
        "current_for_latest_run"
    ):
        raise RuntimeError("Canonical data is being written or its audit is stale")
    latest = status.get("latest_run") or {}
    if latest.get("status") != "success":
        raise RuntimeError("Latest data update was unsuccessful")
    with (root / "state/data_update.lock").open("rb") as guard:
        try:
            fcntl.flock(guard, fcntl.LOCK_SH | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RuntimeError("Data changed during research acquisition") from exc
        runs = sorted(
            (root / "state/runs").glob("*.json"), key=lambda p: p.stat().st_mtime_ns
        )
        if not runs or json.loads(runs[-1].read_text()).get("batch_id") != latest.get(
            "batch_id"
        ):
            raise RuntimeError("Data batch changed during research acquisition")
        quality = json.loads((root / "quality_report.json").read_text())
        if any(
            quality.get(k, 0)
            for k in (
                "invalid_ohlc_rows",
                "duplicate_bar_keys",
                "qfq_date_mismatch_count",
            )
        ):
            raise RuntimeError("Structural errors in audited data")
        with duckdb.connect(str(path), read_only=True) as connection:
            yield (
                connection,
                {
                    "catalog": str(path),
                    "batch_id": latest["batch_id"],
                    "quality": status["quality"],
                },
            )


def research_window(connection, start, end, warmup, observed):
    start, end = timestamp(start).normalize(), timestamp(end).normalize()
    if start > end:
        raise ValueError("start must not follow end")
    calendar = pd.DatetimeIndex(
        connection.execute(
            "SELECT calendar_date FROM trade_calendar WHERE is_trading_day AND calendar_date<=? ORDER BY 1",
            [end.date()],
        )
        .fetchdf()
        .iloc[:, 0]
    )
    first = int(calendar.searchsorted(start))
    if calendar.empty or first >= len(calendar):
        raise ValueError("No requested sessions")
    if observed is None or calendar[-1] > timestamp(observed).normalize():
        raise ValueError("Requested end exceeds audited observations")
    coverage = connection.execute(
        "SELECT max(calendar_date) FROM trade_calendar"
    ).fetchone()[0]
    if coverage is None or coverage < end.date():
        raise ValueError("Requested end exceeds calendar coverage")
    if first < warmup:
        raise ValueError(
            "Insufficient research warmup; evaluation interval was not shifted"
        )
    return calendar[first - warmup :], calendar[first - 1], calendar[first - warmup]


def execution_inputs(
    connection, calendar, start, end, codes, features, metadata, *, cash_yield=None
):
    """Raw execution prices plus explicitly named research prices, never substituted."""
    selected = pd.DataFrame({"code": sorted({symbol(c) for c in codes})})
    connection.register("_execution_codes", selected)
    fields = ",".join("r." + name for name in RAW_FIELDS)
    bars = connection.execute(
        f"""SELECT r.code,r.date,{fields},
          q.close AS adjusted_close,q.high AS adjusted_high
        FROM stock_daily_raw r JOIN _execution_codes c USING(code)
        LEFT JOIN stock_daily q ON r.code=q.code AND r.date=q.date
        WHERE r.date BETWEEN ? AND ? ORDER BY r.date,r.code""",
        [pd.Timestamp(start).date(), pd.Timestamp(end).date()],
    ).fetchdf()
    master = connection.execute("SELECT * FROM security_master").fetchdf()
    bench = connection.execute(
        "SELECT date,close FROM index_daily_bars WHERE index_code=? AND date<=? ORDER BY date",
        ["000300", pd.Timestamp(end).date()],
    ).fetchdf()
    bench["ret"] = bench.close.pct_change(fill_method=None)
    bench = bench.loc[bench.date.ge(pd.Timestamp(start)), ["date", "ret"]]
    return MarketData(
        bars,
        calendar=calendar,
        master=master,
        features=features,
        benchmark=bench,
        cash_yield=cash_yield,
        metadata={
            **metadata,
            "benchmark": "000300",
            "benchmark_basis": "close_to_close",
        },
    )
