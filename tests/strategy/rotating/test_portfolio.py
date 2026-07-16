from __future__ import annotations

from dataclasses import replace

import pandas as pd
import pytest

from strategy.rotating.config.params import DEFAULT_PARAMS
from strategy.rotating.engine.portfolio import run_portfolio


def test_market_gate_cancels_delayed_buy_when_the_execution_month_turns_off() -> None:
    dates = pd.to_datetime(["2024-01-30", "2024-01-31", "2024-02-01"])
    scores = pd.DataFrame(
        {
            "code": ["000001"],
            "date": [dates[0]],
            "ensemble_score": [1.0],
            "eligible": [True],
            "industry": ["银行"],
            "adv_20": [100e6],
        }
    )
    bars = pd.DataFrame(
        {
            "code": "000001",
            "date": dates,
            "open": [10.0, 11.0, 10.5],
            "close": [10.0, 11.0, 10.6],
            "high": [10.2, 11.0, 10.8],
            "low": [9.8, 11.0, 10.3],
            "limit_up": [11.0, 11.0, 12.1],
            "trade_status": True,
        }
    )
    gate = pd.DataFrame(
        {
            "effective_month": pd.to_datetime(["2024-01-01", "2024-02-01"]),
            "allow_new_buys": [True, False],
        }
    )

    result = run_portfolio(
        scores,
        bars,
        params=replace(
            DEFAULT_PARAMS,
            names_per_sleeve=1,
            enable_realistic_costs=False,
            enable_open_slippage=False,
        ),
        initial_capital=100_000.0,
        entry_gate=gate,
    )

    assert result["trades"].empty
    assert result["pending_orders"].empty
    assert result["equity"]["allow_new_buys"].tolist() == [True, True, False]


def test_market_gate_keeps_existing_sell_orders_active_in_risk_off_month() -> None:
    dates = pd.to_datetime(["2024-01-30", "2024-01-31", "2024-02-01"])
    scores = pd.DataFrame(
        {
            "code": ["000001"],
            "date": [dates[0]],
            "ensemble_score": [1.0],
            "eligible": [True],
            "industry": ["银行"],
            "adv_20": [100e6],
        }
    )
    bars = pd.DataFrame(
        {
            "code": "000001",
            "date": dates,
            "open": [10.0, 10.0, 11.0],
            "close": [10.0, 10.5, 11.0],
            "high": [10.2, 10.7, 11.2],
            "low": [9.8, 9.8, 10.8],
            "trade_status": True,
        }
    )
    gate = pd.DataFrame(
        {
            "effective_month": pd.to_datetime(["2024-01-01", "2024-02-01"]),
            "allow_new_buys": [True, False],
        }
    )

    result = run_portfolio(
        scores,
        bars,
        params=replace(
            DEFAULT_PARAMS,
            holding_sessions=1,
            names_per_sleeve=1,
            enable_realistic_costs=False,
            enable_open_slippage=False,
        ),
        initial_capital=100_000.0,
        entry_gate=gate,
    )

    assert result["trades"]["side"].tolist() == ["BUY", "SELL"]
    assert result["trades"]["date"].tolist() == [dates[1], dates[2]]
    assert result["pending_orders"].empty


def test_run_portfolio_fills_signal_at_next_open_and_floors_to_board_lot() -> None:
    dates = pd.bdate_range("2024-01-02", periods=2)
    scores = pd.DataFrame(
        {
            "code": ["000001"],
            "date": [dates[0]],
            "ensemble_score": [1.0],
            "eligible": [True],
            "industry": ["银行"],
            "adv_20": [100e6],
        }
    )
    bars = pd.DataFrame(
        {
            "code": ["000001", "000001"],
            "date": dates,
            "open": [30.0, 31.0],
            "close": [30.0, 32.0],
            "high": [30.5, 32.5],
            "low": [29.5, 30.5],
            "trade_status": [True, True],
        }
    )
    params = replace(
        DEFAULT_PARAMS,
        names_per_sleeve=1,
        max_stock_weight=0.20,
        enable_realistic_costs=False,
        cost_bps=0.0,
        enable_open_slippage=False,
    )

    result = run_portfolio(scores, bars, params=params, initial_capital=100_000.0)

    trades = result["trades"]
    assert len(trades) == 1
    trade = trades.iloc[0]
    assert (trade["signal_date"], trade["date"], trade["price"]) == (
        dates[0],
        dates[1],
        31.0,
    )
    assert trade["shares"] == 600
    assert trade["shares"] % 100 == 0


def test_run_portfolio_rolls_one_price_limit_up_buy_to_next_tradable_open() -> None:
    dates = pd.bdate_range("2024-02-01", periods=3)
    scores = pd.DataFrame(
        {
            "code": ["000001"],
            "date": [dates[0]],
            "ensemble_score": [1.0],
            "eligible": [True],
            "industry": ["银行"],
            "adv_20": [100e6],
        }
    )
    bars = pd.DataFrame(
        {
            "code": "000001",
            "date": dates,
            "open": [10.0, 11.0, 10.5],
            "close": [10.0, 11.0, 10.6],
            "high": [10.2, 11.0, 10.8],
            "low": [9.8, 11.0, 10.3],
            "limit_up": [11.0, 11.0, 12.1],
            "trade_status": True,
        }
    )
    params = replace(
        DEFAULT_PARAMS,
        names_per_sleeve=1,
        max_stock_weight=0.20,
        enable_realistic_costs=False,
        cost_bps=0.0,
        enable_open_slippage=False,
    )

    result = run_portfolio(scores, bars, params=params, initial_capital=100_000.0)

    assert result["trades"].iloc[0]["date"] == dates[2]


def test_run_portfolio_holds_five_trading_sessions_not_five_calendar_days() -> None:
    dates = pd.to_datetime(
        [
            "2024-02-08",
            "2024-02-09",
            "2024-02-19",
            "2024-02-20",
            "2024-02-21",
            "2024-02-22",
            "2024-02-23",
        ]
    )
    scores = pd.DataFrame(
        {
            "code": ["000001"],
            "date": [dates[0]],
            "ensemble_score": [1.0],
            "eligible": [True],
            "adv_20": [100e6],
        }
    )
    bars = pd.DataFrame(
        {
            "code": "000001",
            "date": dates,
            "open": 10.0,
            "close": 10.0,
            "high": 10.2,
            "low": 9.8,
            "trade_status": True,
        }
    )
    params = replace(
        DEFAULT_PARAMS,
        names_per_sleeve=1,
        max_stock_weight=0.20,
        enable_realistic_costs=False,
        cost_bps=0.0,
        enable_open_slippage=False,
    )

    result = run_portfolio(scores, bars, params=params, initial_capital=100_000.0)

    trades = result["trades"]
    assert trades["side"].tolist() == ["BUY", "SELL"]
    assert trades.iloc[0]["date"] == dates[1]
    assert trades.iloc[1]["date"] == dates[6]


def test_run_portfolio_rolls_one_price_limit_down_sell_until_tradable() -> None:
    dates = pd.bdate_range("2024-03-01", periods=8)
    scores = pd.DataFrame(
        {
            "code": ["000001"],
            "date": [dates[0]],
            "ensemble_score": [1.0],
            "eligible": [True],
            "adv_20": [100e6],
        }
    )
    bars = pd.DataFrame(
        {
            "code": "000001",
            "date": dates,
            "open": [10.0, 10.0, 10.0, 10.0, 10.0, 10.0, 9.0, 9.2],
            "close": [10.0, 10.0, 10.0, 10.0, 10.0, 10.0, 9.0, 9.3],
            "high": [10.2, 10.2, 10.2, 10.2, 10.2, 10.2, 9.0, 9.5],
            "low": [9.8, 9.8, 9.8, 9.8, 9.8, 9.8, 9.0, 9.1],
            "limit_down": [9.0, 9.0, 9.0, 9.0, 9.0, 9.0, 9.0, 8.1],
            "trade_status": True,
        }
    )
    params = replace(
        DEFAULT_PARAMS,
        names_per_sleeve=1,
        max_stock_weight=0.20,
        enable_realistic_costs=False,
        cost_bps=0.0,
        enable_open_slippage=False,
    )

    result = run_portfolio(scores, bars, params=params, initial_capital=100_000.0)

    sells = result["trades"].loc[result["trades"]["side"].eq("SELL")]
    assert sells.iloc[0]["date"] == dates[7]


def test_run_portfolio_applies_stamp_tax_rate_on_the_actual_trade_date() -> None:
    def sell_fee(dates: pd.DatetimeIndex) -> float:
        scores = pd.DataFrame(
            {
                "code": ["000001"],
                "date": [dates[0]],
                "ensemble_score": [1.0],
                "eligible": [True],
                "adv_20": [100e6],
            }
        )
        bars = pd.DataFrame(
            {
                "code": "000001",
                "date": dates,
                "open": 10.0,
                "close": 10.0,
                "high": 10.2,
                "low": 9.8,
                "trade_status": True,
            }
        )
        params = replace(
            DEFAULT_PARAMS,
            holding_sessions=1,
            names_per_sleeve=1,
            max_stock_weight=0.20,
            enable_open_slippage=False,
        )
        result = run_portfolio(scores, bars, params=params, initial_capital=100_000.0)
        return float(
            result["trades"].loc[result["trades"]["side"].eq("SELL"), "fee"].iloc[0]
        )

    before = sell_fee(pd.bdate_range("2023-08-23", periods=3))
    after = sell_fee(pd.bdate_range("2023-08-28", periods=3))

    assert before - after == pytest.approx(10.0)


def test_run_portfolio_applies_transfer_fee_rate_on_the_actual_trade_date() -> None:
    def buy_fee(dates: pd.DatetimeIndex) -> float:
        scores = pd.DataFrame(
            {
                "code": ["000001"],
                "date": [dates[0]],
                "ensemble_score": [1.0],
                "eligible": [True],
                "adv_20": [100e6],
            }
        )
        bars = pd.DataFrame(
            {
                "code": "000001",
                "date": dates,
                "open": 10.0,
                "close": 10.0,
                "high": 10.2,
                "low": 9.8,
                "trade_status": True,
            }
        )
        params = replace(
            DEFAULT_PARAMS,
            names_per_sleeve=1,
            max_stock_weight=0.20,
            enable_open_slippage=False,
        )
        result = run_portfolio(scores, bars, params=params, initial_capital=100_000.0)
        return float(
            result["trades"].loc[result["trades"]["side"].eq("BUY"), "fee"].iloc[0]
        )

    before = buy_fee(pd.bdate_range("2022-04-25", periods=2))
    after = buy_fee(pd.bdate_range("2022-04-29", periods=2))

    assert before - after == pytest.approx(0.2)


def test_run_portfolio_caps_aggregate_industry_exposure() -> None:
    dates = pd.bdate_range("2024-04-01", periods=2)
    codes = ["000001", "000002", "000003"]
    scores = pd.DataFrame(
        {
            "code": codes,
            "date": dates[0],
            "ensemble_score": [3.0, 2.0, 1.0],
            "eligible": True,
            "industry": "银行",
            "adv_20": 100e6,
        }
    )
    bars = pd.DataFrame(
        [
            {
                "code": code,
                "date": date,
                "open": 10.0,
                "close": 10.0,
                "high": 10.2,
                "low": 9.8,
                "trade_status": True,
            }
            for code in codes
            for date in dates
        ]
    )
    params = replace(
        DEFAULT_PARAMS,
        names_per_sleeve=3,
        enter_quantile=1.0,
        max_stock_weight=0.20,
        max_industry_weight=0.10,
        enable_realistic_costs=False,
        cost_bps=0.0,
        enable_open_slippage=False,
    )

    result = run_portfolio(scores, bars, params=params, initial_capital=100_000.0)

    assert result["trades"]["amount"].sum() <= 10_000.0


def test_run_portfolio_caps_each_order_to_two_percent_of_adv() -> None:
    dates = pd.bdate_range("2024-04-08", periods=2)
    scores = pd.DataFrame(
        {
            "code": ["000001"],
            "date": [dates[0]],
            "ensemble_score": [1.0],
            "eligible": [True],
            "industry": ["银行"],
            "adv_20": [500_000.0],
        }
    )
    bars = pd.DataFrame(
        {
            "code": "000001",
            "date": dates,
            "open": 10.0,
            "close": 10.0,
            "high": 10.2,
            "low": 9.8,
            "trade_status": True,
        }
    )
    params = replace(
        DEFAULT_PARAMS,
        names_per_sleeve=1,
        max_stock_weight=0.20,
        enable_realistic_costs=False,
        cost_bps=0.0,
        enable_open_slippage=False,
    )

    result = run_portfolio(scores, bars, params=params, initial_capital=100_000.0)

    assert result["trades"].iloc[0]["amount"] == 10_000.0


def test_run_portfolio_keeps_mature_sleeve_holding_inside_exit_buffer() -> None:
    dates = pd.bdate_range("2024-05-06", periods=7)
    codes = ["000001", "000002", "000003", "000004"]
    scores = pd.concat(
        [
            pd.DataFrame(
                {
                    "code": ["000001"],
                    "date": [dates[0]],
                    "ensemble_score": [4.0],
                    "eligible": [True],
                    "industry": ["银行"],
                    "adv_20": [100e6],
                }
            ),
            pd.DataFrame(
                {
                    "code": codes,
                    "date": dates[5],
                    "ensemble_score": [4.0, 3.0, 2.0, 1.0],
                    "eligible": True,
                    "industry": ["银行", "制造", "消费", "材料"],
                    "adv_20": 100e6,
                }
            ),
        ],
        ignore_index=True,
    )
    bars = pd.DataFrame(
        [
            {
                "code": code,
                "date": date,
                "open": 10.0,
                "close": 10.0,
                "high": 10.2,
                "low": 9.8,
                "trade_status": True,
            }
            for code in codes
            for date in dates
        ]
    )
    params = replace(
        DEFAULT_PARAMS,
        names_per_sleeve=1,
        max_stock_weight=0.20,
        enable_realistic_costs=False,
        cost_bps=0.0,
        enable_open_slippage=False,
    )

    result = run_portfolio(scores, bars, params=params, initial_capital=100_000.0)

    assert result["trades"]["side"].tolist() == ["BUY"]
    assert result["positions"]["code"].tolist() == ["000001"]


def test_run_portfolio_executes_pending_sells_before_pending_buys() -> None:
    dates = pd.bdate_range("2024-06-03", periods=3)
    scores = pd.DataFrame(
        {
            "code": ["000001", "000002"],
            "date": dates[0],
            "ensemble_score": [2.0, 1.0],
            "eligible": True,
            "industry": ["银行", "制造"],
            "adv_20": 100e6,
        }
    )
    bars = pd.DataFrame(
        [
            {
                "code": code,
                "date": date,
                "open": 11.0 if code == "000002" and date == dates[1] else 10.0,
                "close": 11.0 if code == "000002" and date == dates[1] else 10.0,
                "high": 11.0 if code == "000002" and date == dates[1] else 10.2,
                "low": 11.0 if code == "000002" and date == dates[1] else 9.8,
                "limit_up": 11.0 if code == "000002" and date == dates[1] else 12.0,
                "trade_status": True,
            }
            for code in ("000001", "000002")
            for date in dates
        ]
    )
    params = replace(
        DEFAULT_PARAMS,
        holding_sessions=1,
        names_per_sleeve=2,
        enter_quantile=1.0,
        max_stock_weight=0.20,
        enable_realistic_costs=False,
        cost_bps=0.0,
        enable_open_slippage=False,
    )

    result = run_portfolio(scores, bars, params=params, initial_capital=100_000.0)

    trades_on_last_day = result["trades"].loc[result["trades"]["date"].eq(dates[2])]
    assert trades_on_last_day["side"].tolist() == ["SELL", "BUY"]


def test_run_portfolio_does_not_replace_a_position_until_its_sell_fills() -> None:
    dates = pd.bdate_range("2024-07-01", periods=4)
    scores = pd.concat(
        [
            pd.DataFrame(
                {
                    "code": ["000001"],
                    "date": [dates[0]],
                    "ensemble_score": [2.0],
                    "eligible": [True],
                    "industry": ["银行"],
                    "adv_20": [100e6],
                }
            ),
            pd.DataFrame(
                {
                    "code": ["000002"],
                    "date": [dates[1]],
                    "ensemble_score": [2.0],
                    "eligible": [True],
                    "industry": ["制造"],
                    "adv_20": [100e6],
                }
            ),
        ],
        ignore_index=True,
    )
    bars = pd.DataFrame(
        [
            {
                "code": code,
                "date": date,
                "open": 9.0 if code == "000001" and date == dates[2] else 10.0,
                "close": 9.0 if code == "000001" and date == dates[2] else 10.0,
                "high": 9.0 if code == "000001" and date == dates[2] else 10.2,
                "low": 9.0 if code == "000001" and date == dates[2] else 9.8,
                "limit_down": 9.0 if code == "000001" and date == dates[2] else 8.0,
                "trade_status": True,
            }
            for code in ("000001", "000002")
            for date in dates
        ]
    )
    params = replace(
        DEFAULT_PARAMS,
        sleeves=1,
        holding_sessions=1,
        names_per_sleeve=1,
        enter_quantile=1.0,
        max_stock_weight=0.5,
        max_industry_weight=1.0,
        enable_realistic_costs=False,
        cost_bps=0.0,
        enable_open_slippage=False,
    )

    result = run_portfolio(scores, bars, params=params, initial_capital=100_000.0)

    assert result["equity"]["n_positions"].max() == 1
    assert result["trades"].loc[result["trades"]["side"].eq("BUY"), "code"].tolist() == [
        "000001",
        "000002",
    ]
