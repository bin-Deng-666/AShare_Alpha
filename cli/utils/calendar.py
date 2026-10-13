"""Confirmed next-session dates; unavailable calendars remain explicit."""

from pathlib import Path
import pandas as pd
from data.ashare_data.paths import PARQUET_ROOT


def next_trade_date(asof, *, calendar_dates=None, index_bars=None):
    boundary = pd.Timestamp(asof).normalize()
    dates = []
    if calendar_dates is not None:
        dates.extend(pd.to_datetime(pd.Series(calendar_dates)).dropna())
    calendar = PARQUET_ROOT / "reference/trade_calendar.parquet"
    if calendar.exists():
        frame = pd.read_parquet(calendar, columns=["calendar_date", "is_trading_day"])
        dates.extend(
            pd.to_datetime(frame.loc[frame.is_trading_day.eq(1), "calendar_date"])
        )
    if index_bars is not None and Path(index_bars).exists():
        dates.extend(
            pd.to_datetime(pd.read_parquet(index_bars, columns=["date"])["date"])
        )
    future = sorted(
        {
            pd.Timestamp(day).normalize()
            for day in dates
            if pd.Timestamp(day).normalize() > boundary
        }
    )
    return str(future[0].date()) if future else None
