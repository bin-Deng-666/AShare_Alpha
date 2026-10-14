"""The latest closed session uses Shanghai time and the published exchange calendar."""

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from pathlib import Path
import pandas as pd

SHANGHAI = ZoneInfo("Asia/Shanghai")


def market_clock(now=None):
    now = now or datetime.now(SHANGHAI)
    now = (
        now.replace(tzinfo=SHANGHAI) if now.tzinfo is None else now.astimezone(SHANGHAI)
    )
    cutoff = now.date() if now.hour >= 15 else now.date() - timedelta(days=1)
    return {
        "today": str(now.date()),
        "cutoff": str(cutoff),
        "before_close": now.hour < 15,
    }


def closed_session(calendar_path, cutoff=None, now=None):
    clock = market_clock(now)
    cutoff = cutoff or clock["cutoff"]
    path = Path(calendar_path)
    result = {**clock, "cutoff": cutoff, "session": None, "calendar_covered": False}
    if not path.exists():
        return result
    frame = pd.read_parquet(path)
    field = "calendar_date" if "calendar_date" in frame else "date"
    dates = pd.to_datetime(frame[field]).dt.normalize()
    boundary = pd.Timestamp(cutoff)
    if dates.empty or dates.max() < boundary:
        return result
    result["calendar_covered"] = True
    if "is_trading_day" in frame:
        dates = dates.loc[frame.is_trading_day.eq(True)]
    closed = dates.loc[dates.le(boundary)]
    if len(closed):
        result["session"] = str(closed.max().date())
    return result
