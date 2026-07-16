from __future__ import annotations

import numpy as np
import pandas as pd

from strategy.rotating.features.market_regime import build_monthly_trend_gate


def test_monthly_trend_gate_uses_ten_completed_month_ends_for_next_month() -> None:
    month_ends = pd.date_range("2023-01-31", periods=11, freq="ME")
    closes = np.arange(100.0, 111.0)
    index_bars = pd.DataFrame(
        {
            "date": month_ends,
            "close": closes,
        }
    )

    gate = build_monthly_trend_gate(index_bars)

    september = gate.loc[gate["effective_month"].eq(pd.Timestamp("2023-09-01"))].iloc[0]
    november = gate.loc[gate["effective_month"].eq(pd.Timestamp("2023-11-01"))].iloc[0]
    december = gate.loc[gate["effective_month"].eq(pd.Timestamp("2023-12-01"))].iloc[0]

    assert (september["lookback_months"], september["allow_new_buys"]) == (8, False)
    assert november["gate_source_month_end"] == pd.Timestamp("2023-10-31")
    assert november["gate_source_close"] == 109.0
    assert november["gate_sma_10"] == np.mean(closes[:10])
    assert bool(november["allow_new_buys"])
    assert december["gate_source_month_end"] == pd.Timestamp("2023-11-30")
    assert december["lookback_0_date"] == pd.Timestamp("2023-11-30")
    assert december["lookback_9_date"] == pd.Timestamp("2023-02-28")


def test_monthly_trend_gate_treats_equal_to_sma_as_risk_off() -> None:
    month_ends = pd.date_range("2023-01-31", periods=10, freq="ME")
    gate = build_monthly_trend_gate(
        pd.DataFrame({"date": month_ends, "close": 100.0})
    )

    october_signal = gate.loc[
        gate["effective_month"].eq(pd.Timestamp("2023-11-01"))
    ].iloc[0]

    assert october_signal["gate_source_month_end"] == pd.Timestamp("2023-10-31")
    assert october_signal["gate_sma_10"] == 100.0
    assert not bool(october_signal["allow_new_buys"])


def test_monthly_trend_gate_does_not_span_a_missing_calendar_month() -> None:
    observed_month_ends = pd.to_datetime(
        [
            "2023-01-31",
            "2023-02-28",
            "2023-03-31",
            "2023-04-28",
            "2023-05-31",
            "2023-06-30",
            "2023-07-31",
            "2023-08-31",
            # September is deliberately absent.
            "2023-10-31",
            "2023-11-30",
        ]
    )
    gate = build_monthly_trend_gate(
        pd.DataFrame(
            {
                "date": observed_month_ends,
                "close": np.arange(100.0, 110.0),
            }
        )
    )

    december = gate.loc[
        gate["effective_month"].eq(pd.Timestamp("2023-12-01"))
    ].iloc[0]

    assert december["lookback_months"] == 9
    assert pd.isna(december["gate_sma_10"])
    assert not bool(december["allow_new_buys"])
    assert pd.isna(december["lookback_2_date"])
    assert pd.isna(december["lookback_2_close"])
