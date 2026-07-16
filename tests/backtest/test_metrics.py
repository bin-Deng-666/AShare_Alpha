from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from backtest.metrics import compute_metrics


pytestmark = pytest.mark.filterwarnings(
    "ignore:The 'generic' unit.*:DeprecationWarning"
)


def test_compute_metrics_serializes_yearly_strategy_sharpe() -> None:
    dates = pd.to_datetime(
        ["2023-12-28", "2023-12-29", "2024-01-02", "2024-01-03"]
    )
    daily_returns = pd.Series([0.01, 0.02, 0.01, 0.03])
    equity = pd.Series([101.0, 103.02, 104.0502, 107.171706])
    daily = pd.DataFrame(
        {
            "date": dates,
            "equity": equity,
            "nav": equity / 100.0,
            "daily_ret": daily_returns,
            "cash": equity,
            "market_value": 0.0,
            "n_positions": 0,
            "cash_ratio": 1.0,
            "daily_pnl": equity.diff().fillna(0.0),
        }
    )
    benchmark = pd.DataFrame({"date": dates, "ret": 0.0})

    result = compute_metrics(
        daily,
        pd.DataFrame(),
        benchmark,
        initial_capital=100.0,
    )

    first_year = result["yearly"][0]
    expected_return = float((1.0 + daily_returns.iloc[:2]).prod() - 1.0)
    expected_volatility = daily_returns.iloc[:2].std() * np.sqrt(242.0)
    assert np.isclose(first_year["strategy"], expected_return)
    assert np.isclose(
        first_year["strategy_sharpe"], expected_return / expected_volatility
    )
