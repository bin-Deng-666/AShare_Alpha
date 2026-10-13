"""Trading-calendar cutoffs; never treat the calendar's last row as today's data."""
from __future__ import annotations
import datetime as dt
import pandas as pd
from .paths import PARQUET_ROOT as PARQUET


def _target_asof(config):
    target = pd.Timestamp(config.get('end_date') or dt.date.today()).normalize()
    path = PARQUET / 'reference' / 'trade_calendar.parquet'
    if not path.exists():
        raise RuntimeError('交易日历缺失，请先更新 calendar')
    cal = pd.read_parquet(path, columns=['calendar_date','is_trading_day'])
    dates = pd.to_datetime(cal['calendar_date'],errors='coerce').dt.normalize()
    if dates.max() < target:
        raise RuntimeError(f'交易日历只覆盖到 {dates.max()}，目标 {target.date()}；请先更新 calendar')
    days = dates[(cal['is_trading_day'].astype(int)==1)&(dates<=target)]
    if days.empty:
        raise RuntimeError(f'交易日历内没有 {target.date()} 以前的交易日')
    return days.max()
