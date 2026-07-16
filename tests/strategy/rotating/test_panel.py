from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from strategy.rotating.features.build_panel import (
    CANDIDATE_MODEL_INPUT_FEATURES,
    MODEL_INPUT_FEATURES,
    MODEL_TARGET_COLUMN,
    build_factor_panel,
)


def test_engineered_feature_pack_is_the_selected_model_input_schema() -> None:
    assert MODEL_INPUT_FEATURES == CANDIDATE_MODEL_INPUT_FEATURES
    assert MODEL_TARGET_COLUMN == "target_rank"


def test_build_factor_panel_keeps_low_liquidity_observations_in_rolling_window() -> None:
    dates = pd.bdate_range("2024-01-02", periods=7)
    bars = pd.DataFrame(
        {
            "code": "000001",
            "date": dates,
            "open": 10.0,
            "high": 10.5,
            "low": 9.5,
            "close": 10.0,
            "amount": [100e6, 100e6, 100e6, 1e6, 100e6, 100e6, 100e6],
            "turnover_pct": 1.0,
            "float_market_cap": 10e9,
            "is_st": False,
            "trade_status": True,
        }
    )

    panel = build_factor_panel(
        start="2024-01-02",
        end="2024-01-10",
        source=bars,
        min_history=5,
        min_median_amount=50e6,
        min_float_market_cap=1e9,
    )

    row = panel.loc[panel["date"].eq(dates[5])].iloc[0]
    assert row["amount_mean_5"] == 80_200_000.0


def test_build_factor_panel_marks_missing_earnings_yield_without_ranking_it_cheap() -> None:
    dates = pd.bdate_range("2024-02-01", periods=5)
    bars = pd.DataFrame(
        [
            {
                "code": code,
                "date": date,
                "open": 10.0,
                "high": 10.5,
                "low": 9.5,
                "close": 10.0,
                "amount": 100e6,
                "turnover_pct": 1.0,
                "float_market_cap": 10e9,
                "is_st": False,
                "trade_status": True,
                "pe_ttm": pe_ttm,
            }
            for code, pe_ttm in (("000001", 10.0), ("000002", None))
            for date in dates
        ]
    )

    panel = build_factor_panel(
        start="2024-02-01",
        end="2024-02-07",
        source=bars,
        min_history=1,
        min_median_amount=0.0,
        min_float_market_cap=0.0,
    )

    missing = panel.loc[
        panel["code"].eq("000002") & panel["date"].eq(dates[-1])
    ].iloc[0]
    assert (missing["ep_missing"], pd.isna(missing["rank_ep"])) == (1, True)


def test_build_factor_panel_labels_next_open_through_sixth_open() -> None:
    dates = pd.bdate_range("2024-03-01", periods=8)
    bars = pd.DataFrame(
        {
            "code": "000001",
            "date": dates,
            "open": [10.0, 11.0, 12.0, 13.0, 14.0, 15.0, 16.0, 17.0],
            "high": 20.0,
            "low": 9.0,
            "close": 10.0,
            "amount": 100e6,
            "turnover_pct": 1.0,
            "float_market_cap": 10e9,
            "is_st": False,
            "trade_status": True,
        }
    )

    panel = build_factor_panel(
        start="2024-03-01",
        end="2024-03-12",
        source=bars,
        min_history=1,
        min_median_amount=0.0,
        min_float_market_cap=0.0,
    )

    first = panel.iloc[0]
    assert (first["target_5"], first["label_available_date"]) == (
        16.0 / 11.0 - 1.0,
        dates[6],
    )
    assert first["target_path_5"] == np.mean(
        [
            12.0 / 11.0 - 1.0,
            13.0 / 11.0 - 1.0,
            14.0 / 11.0 - 1.0,
            15.0 / 11.0 - 1.0,
            16.0 / 11.0 - 1.0,
        ]
    )


def test_forward_interval_labels_require_an_executable_complete_path() -> None:
    dates = pd.bdate_range("2024-01-02", periods=8)
    source = pd.DataFrame(
        {
            "code": "000001",
            "date": dates,
            "open": np.arange(10.0, 18.0),
            "high": np.arange(10.5, 18.5),
            "low": np.arange(9.5, 17.5),
            "close": np.arange(10.0, 18.0),
            "amount": 100e6,
            "turnover_pct": 1.0,
            "float_market_cap": 10e9,
            "is_st": False,
            "trade_status": [True, True, True, False, True, True, True, True],
            "limit_up": np.nan,
            "limit_down": np.nan,
        }
    )

    panel = build_factor_panel(
        start=str(dates[0].date()),
        end=str(dates[-1].date()),
        source=source,
        min_history=1,
        min_listing_sessions=1,
        min_median_amount=0.0,
        min_float_market_cap=0.0,
    )

    first = panel.iloc[0]
    assert pd.isna(first["target_5"])
    assert pd.isna(first["target_path_5"])


def test_forward_labels_use_raw_one_price_limits_like_the_execution_engine() -> None:
    dates = pd.bdate_range("2024-01-02", periods=8)
    rows = []
    for code, entry_is_one_price in (("000001", False), ("000002", True)):
        for offset, date in enumerate(dates):
            raw_open = 11.0 if offset == 1 else 10.0
            rows.append(
                {
                    "code": code,
                    "date": date,
                    # Adjusted prices deliberately differ from the raw limit-price
                    # basis. They remain the return basis for both labels.
                    "open": raw_open / 2.0,
                    "high": raw_open / 2.0 + 0.25,
                    "low": raw_open / 2.0 - 0.25,
                    "close": raw_open / 2.0,
                    "raw_open": raw_open,
                    "raw_high": raw_open if offset == 1 and entry_is_one_price else raw_open + 0.5,
                    "raw_low": raw_open if offset == 1 and entry_is_one_price else raw_open - 0.5,
                    "amount": 100e6,
                    "turnover_pct": 1.0,
                    "float_market_cap": 10e9,
                    "is_st": False,
                    "trade_status": True,
                    "limit_up": 11.0 if offset == 1 else 12.0,
                    "limit_down": 9.0,
                    "is_limit_up": offset == 1,
                    "is_limit_down": False,
                }
            )

    panel = build_factor_panel(
        start=str(dates[0].date()),
        end=str(dates[-1].date()),
        source=pd.DataFrame(rows),
        min_history=1,
        min_listing_sessions=1,
        min_median_amount=0.0,
        min_float_market_cap=0.0,
    )

    ordinary_limit_open = panel.loc[
        panel["code"].eq("000001") & panel["date"].eq(dates[0])
    ].iloc[0]
    one_price_limit_open = panel.loc[
        panel["code"].eq("000002") & panel["date"].eq(dates[0])
    ].iloc[0]
    assert pd.notna(ordinary_limit_open["target_5"])
    assert pd.notna(ordinary_limit_open["target_path_5"])
    assert pd.isna(one_price_limit_open["target_5"])
    assert pd.isna(one_price_limit_open["target_path_5"])


def test_forward_labels_reject_raw_limits_without_raw_ohlc() -> None:
    dates = pd.bdate_range("2024-01-02", periods=8)
    source = pd.DataFrame(
        {
            "code": "000001",
            "date": dates,
            "open": 10.0,
            "high": 10.5,
            "low": 9.5,
            "close": 10.0,
            "amount": 100e6,
            "turnover_pct": 1.0,
            "float_market_cap": 10e9,
            "is_st": False,
            "trade_status": True,
            "limit_up": 11.0,
            "limit_down": 9.0,
        }
    )

    with pytest.raises(ValueError, match="raw OHLC columns are required"):
        build_factor_panel(
            start=str(dates[0].date()),
            end=str(dates[-1].date()),
            source=source,
            min_history=1,
            min_listing_sessions=1,
            min_median_amount=0.0,
            min_float_market_cap=0.0,
        )


def test_engineered_features_use_the_preregistered_complete_windows() -> None:
    dates = pd.bdate_range("2024-01-02", periods=25)
    index = np.arange(25, dtype=float)
    close = pd.Series(100.0 + index + np.sin(index / 3.0))
    previous_close = close.shift(1)
    open_price = previous_close.fillna(close.iloc[0]) * (1.0 + 0.001 * (index % 3))
    high = pd.Series(open_price + 1.0 + 0.05 * index)
    low = pd.Series(open_price - 0.5 - 0.02 * index)
    amount = pd.Series(100e6 * np.exp(index / 50.0))
    turnover = pd.Series(1.0 + index / 20.0)
    source = pd.DataFrame(
        {
            "code": "000001",
            "date": dates,
            "open": open_price,
            "high": high,
            "low": low,
            "close": close,
            "amount": amount,
            "turnover_pct": turnover,
            "float_market_cap": 10e9,
            "is_st": False,
            "trade_status": True,
        }
    )

    panel = build_factor_panel(
        start=str(dates[0].date()),
        end=str(dates[-1].date()),
        source=source,
        min_history=1,
        min_listing_sessions=1,
        min_median_amount=0.0,
        min_float_market_cap=0.0,
    )
    last = panel.iloc[-1]
    ret_1 = close.pct_change(fill_method=None)
    expected_downside = np.sqrt(ret_1.clip(upper=0.0).pow(2.0).iloc[-20:].mean())
    expected_range = ((high - low) / previous_close).iloc[-10:].mean()
    expected_location = (
        (close - low) / (high - low + 1e-12)
    ).iloc[-10:].mean()
    expected_turnover_shock = (
        turnover.iloc[-5:].mean() / turnover.iloc[-20:].mean() - 1.0
    )

    assert np.isclose(last["downside_vol_20"], expected_downside)
    assert np.isclose(last["range_vol_10"], expected_range)
    assert np.isclose(last["close_location_mean_10"], expected_location)
    assert np.isclose(last["turnover_shock_5_20"], expected_turnover_shock)


def test_close_location_keeps_zero_range_sessions_with_epsilon() -> None:
    dates = pd.bdate_range("2024-01-02", periods=12)
    source = pd.DataFrame(
        {
            "code": "000001",
            "date": dates,
            "open": 10.0,
            "high": 10.0,
            "low": 10.0,
            "close": 10.0,
            "amount": 100e6,
            "turnover_pct": 1.0,
            "float_market_cap": 10e9,
            "is_st": False,
            "trade_status": True,
        }
    )

    panel = build_factor_panel(
        start=str(dates[0].date()),
        end=str(dates[-1].date()),
        source=source,
        min_history=1,
        min_listing_sessions=1,
        min_median_amount=0.0,
        min_float_market_cap=0.0,
    )

    assert panel.iloc[-1]["close_location_mean_10"] == 0.0


def test_build_factor_panel_uses_market_sessions_for_label_horizon() -> None:
    dates = pd.bdate_range("2024-03-01", periods=8)
    rows = []
    for code in ("000001", "000002"):
        for offset, date in enumerate(dates):
            if code == "000001" and date == dates[3]:
                continue
            rows.append(
                {
                    "code": code,
                    "date": date,
                    "open": 10.0 + offset,
                    "high": 20.0,
                    "low": 9.0,
                    "close": 10.0,
                    "amount": 100e6,
                    "turnover_pct": 1.0,
                    "float_market_cap": 10e9,
                    "is_st": False,
                    "trade_status": True,
                }
            )

    panel = build_factor_panel(
        start=str(dates[0].date()),
        end=str(dates[0].date()),
        source=pd.DataFrame(rows),
        min_history=1,
        min_listing_sessions=1,
        min_median_amount=0.0,
        min_float_market_cap=0.0,
    )

    first = panel.loc[panel["code"].eq("000001")].iloc[0]
    assert (first["exit_open_5"], first["label_available_date"]) == (
        16.0,
        dates[6],
    )


def test_build_factor_panel_neutralizes_training_target_by_industry_and_size() -> None:
    dates = pd.bdate_range("2024-03-01", periods=7)
    rows = []
    returns = (0.01, 0.06, 0.13, -0.04, 0.03, 0.12)
    for index, future_return in enumerate(returns):
        for offset, date in enumerate(dates):
            open_price = 10.0 * (1.0 + future_return) if offset == 6 else 10.0
            rows.append(
                {
                    "code": f"000{index + 1:03d}",
                    "date": date,
                    "open": open_price,
                    "high": max(10.5, open_price),
                    "low": min(9.5, open_price),
                    "close": 10.0,
                    "amount": 100e6,
                    "turnover_pct": 1.0,
                    "float_market_cap": 1e9 * (index + 1),
                    "industry": "银行" if index < 3 else "制造",
                    "is_st": False,
                    "trade_status": True,
                }
            )

    panel = build_factor_panel(
        start=str(dates[0].date()),
        end=str(dates[0].date()),
        source=pd.DataFrame(rows),
        min_history=1,
        min_listing_sessions=1,
        min_median_amount=0.0,
        min_float_market_cap=0.0,
    )

    assert panel["target_rank"].min() == -1.0
    assert panel["target_rank"].max() == 1.0
    assert np.allclose(panel.groupby("industry")["target_residual"].mean(), 0.0)
    assert np.isclose(
        np.dot(panel["target_residual"], panel["log_float_market_cap"]),
        0.0,
    )


def test_build_factor_panel_exposes_the_registered_model_feature_schema() -> None:
    dates = pd.bdate_range("2023-01-02", periods=260)
    steps = pd.Series(range(260), dtype=float)
    bars = pd.DataFrame(
        {
            "code": "000001",
            "date": dates,
            "open": 10.0 + steps * 0.01,
            "high": 10.2 + steps * 0.01,
            "low": 9.8 + steps * 0.01,
            "close": 10.1 + steps * 0.01,
            "amount": 100e6 + steps * 10_000,
            "turnover_pct": 1.0 + steps * 0.001,
            "float_market_cap": 10e9 + steps * 1e6,
            "is_st": False,
            "trade_status": True,
            "pe_ttm": 10.0,
            "pb_mrq": 2.0,
            "roe": 0.12,
        }
    )

    panel = build_factor_panel(
        start=str(dates[250].date()),
        end=str(dates[-1].date()),
        source=bars,
        min_history=250,
        min_median_amount=0.0,
        min_float_market_cap=0.0,
    )

    expected = {
        "ret_5",
        "ret_20",
        "ret_60",
        "mom_20_5",
        "mom_120_20",
        "reversal_20",
        "vol_20",
        "vol_60",
        "maxret_20",
        "drawdown_60",
        "trend_quality_60",
        "turnover_abnormal_20_250",
        "turnover_cv_20",
        "amount_ratio_5_20",
        "amount_ratio_20_60",
        "amount_cv_20",
        "amihud_20",
        "adv_20",
        "ep",
        "bp",
        "roe",
        "log_float_market_cap",
        "overnight_gap_mean_5",
        "close_location_mean_10",
        "trend_r2_20",
        "trend_residual_20",
        "downside_vol_20",
        "range_vol_10",
        "price_volume_corr_20",
        "turnover_shock_5_20",
        "board_star",
        "board_chinext",
    }
    assert expected.issubset(panel.columns)


def test_build_factor_panel_maps_daily_cross_section_ranks_to_minus_one_and_one() -> None:
    dates = pd.bdate_range("2024-04-01", periods=6)
    frames = []
    for code, final_close in (("000001", 10.0), ("000002", 11.0), ("000003", 12.0)):
        frames.append(
            pd.DataFrame(
                {
                    "code": code,
                    "date": dates,
                    "open": 10.0,
                    "high": 12.5,
                    "low": 9.5,
                    "close": [10.0] * 5 + [final_close],
                    "amount": 100e6,
                    "turnover_pct": 1.0,
                    "float_market_cap": 10e9,
                    "is_st": False,
                    "trade_status": True,
                }
            )
        )

    panel = build_factor_panel(
        start=str(dates[-1].date()),
        end=str(dates[-1].date()),
        source=pd.concat(frames, ignore_index=True),
        min_history=1,
        min_listing_sessions=1,
        min_median_amount=0.0,
        min_float_market_cap=0.0,
    )

    ranked = panel.sort_values("code")["rank_ret_5"].tolist()
    assert ranked == [-1.0, 0.0, 1.0]


def test_build_factor_panel_ranks_only_the_point_in_time_eligible_universe() -> None:
    dates = pd.bdate_range("2024-04-01", periods=6)
    frames = []
    for code, final_close, is_st in (
        ("000001", 10.0, False),
        ("000002", 11.0, False),
        ("000003", 50.0, True),
    ):
        frames.append(
            pd.DataFrame(
                {
                    "code": code,
                    "date": dates,
                    "open": 10.0,
                    "high": 50.5,
                    "low": 9.5,
                    "close": [10.0] * 5 + [final_close],
                    "amount": 100e6,
                    "turnover_pct": 1.0,
                    "float_market_cap": 10e9,
                    "is_st": is_st,
                    "trade_status": True,
                }
            )
        )

    panel = build_factor_panel(
        start=str(dates[-1].date()),
        end=str(dates[-1].date()),
        source=pd.concat(frames, ignore_index=True),
        min_history=1,
        min_listing_sessions=1,
        min_median_amount=0.0,
        min_float_market_cap=0.0,
    ).sort_values("code")

    assert panel["rank_ret_5"].iloc[:2].tolist() == [-1.0, 1.0]
    assert pd.isna(panel["rank_ret_5"].iloc[2])


def test_build_factor_panel_uses_dated_st_state_and_trading_session_age() -> None:
    dates = pd.bdate_range("2024-01-02", periods=121)
    bars = pd.DataFrame(
        {
            "code": "000001",
            "name": "*ST 后来改名",
            "date": dates,
            "open": 10.0,
            "high": 10.5,
            "low": 9.5,
            "close": 10.0,
            "amount": 100e6,
            "turnover_pct": 1.0,
            "float_market_cap": 10e9,
            "is_st": [False] * 120 + [True],
            "trade_status": True,
        }
    )

    panel = build_factor_panel(
        start=str(dates[-2].date()),
        end=str(dates[-1].date()),
        source=bars,
        min_history=1,
        min_listing_sessions=120,
        min_median_amount=0.0,
        min_float_market_cap=0.0,
    )

    assert panel["eligible"].tolist() == [True, False]


def test_build_factor_panel_requires_fifteen_tradable_observations_in_twenty() -> None:
    dates = pd.bdate_range("2024-01-02", periods=25)
    bars = pd.DataFrame(
        {
            "code": "000001",
            "date": dates,
            "open": 10.0,
            "high": 10.5,
            "low": 9.5,
            "close": 10.0,
            "amount": 100e6,
            "turnover_pct": 1.0,
            "float_market_cap": 10e9,
            "is_st": False,
            "trade_status": [True] * 15 + [False] * 9 + [True],
        }
    )

    panel = build_factor_panel(
        start=str(dates[-1].date()),
        end=str(dates[-1].date()),
        source=bars,
        min_history=1,
        min_listing_sessions=1,
        min_tradable_observations=15,
        min_median_amount=0.0,
        min_float_market_cap=0.0,
    )

    assert panel.loc[0, "tradable_observations_20"] == 11
    assert not bool(panel.loc[0, "eligible"])
