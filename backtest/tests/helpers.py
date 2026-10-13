"""Shared deterministic fixtures, with explicit execution metadata."""

import pandas as pd
from backtest.config import BacktestConfig, CostConfig, SlippageConfig
from backtest.data import MarketData


def market(days=7, codes=("000001",), changes=None, features=None, benchmark=None):
    dates = pd.bdate_range("2024-01-02", periods=days)
    rows = []
    for date in dates:
        for code in codes:
            row = {
                "code": code,
                "date": date,
                "open": 10.0,
                "close": 10.0,
                "high": 10.0,
                "low": 10.0,
                "trade_status": True,
                "limit_validation_status": "valid",
                "limit_up": 100.0,
                "limit_down": 0.01,
                "corporate_action_formula_status": "none",
                "cash_dividend_per_share": 0.0,
                "share_distribution_ratio": 0.0,
                "rights_issue_ratio": 0.0,
            }
            row.update((changes or {}).get((code, date), {}))
            rows.append(row)
    return MarketData(
        pd.DataFrame(rows), calendar=dates, features=features, benchmark=benchmark
    )


def config(data, **kw):
    values = {
        "start": str(data.calendar[0].date()),
        "end": str(data.calendar[-1].date()),
        "initial_capital": 10_000,
        "costs": CostConfig(0, 0, 0, 0, False),
        "slippage": SlippageConfig(0),
    }
    values.update(kw)
    return BacktestConfig(**values)
