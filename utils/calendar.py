"""Trading calendar helpers for T+1 folder naming."""

from __future__ import annotations

from pathlib import Path

import pandas as pd


def next_trade_date(
    asof: str,
    *,
    calendar_dates: pd.Series | list | None = None,
    index_bars: Path | None = None,
) -> str:
    """
    Next A-share trading day after signal date `asof`.

    Prefer dates from the signal panel / index calendar; if asof is the
    last known session, fall back to next weekday (skips Sat/Sun only).
    """
    asof_ts = pd.Timestamp(asof).normalize()

    dates: list[pd.Timestamp] = []
    if calendar_dates is not None:
        s = pd.to_datetime(pd.Series(calendar_dates)).dropna().dt.normalize()
        dates.extend(s.tolist())

    if index_bars is not None and Path(index_bars).exists():
        try:
            import duckdb

            path = str(index_bars).replace("'", "''")
            df = duckdb.sql(
                f"""
                SELECT DISTINCT date
                FROM read_parquet('{path}')
                WHERE date > DATE '{asof_ts.date()}'
                ORDER BY date
                LIMIT 5
                """
            ).df()
            if len(df):
                dates.extend(pd.to_datetime(df["date"]).dt.normalize().tolist())
        except Exception:
            pass

    future = sorted({d for d in dates if pd.Timestamp(d).normalize() > asof_ts})
    if future:
        return str(pd.Timestamp(future[0]).date())

    # Last-resort: next Mon–Fri (does not skip CN holidays)
    d = asof_ts + pd.Timedelta(days=1)
    while d.weekday() >= 5:
        d += pd.Timedelta(days=1)
    return str(d.date())
