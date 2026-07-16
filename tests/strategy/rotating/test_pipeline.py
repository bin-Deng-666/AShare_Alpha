from __future__ import annotations

import pandas as pd

from strategy.rotating import pipeline


def test_run_backtest_dataset_does_not_cross_the_public_end_date(
    tmp_path, monkeypatch
) -> None:
    end = pd.Timestamp("2024-01-03")
    scores = pd.DataFrame(
        {
            "code": ["000001"],
            "date": [end],
            "eligible": [True],
            "target_rank": [0.5],
            "label_available_date": [end],
            "factor_score": [0.1],
            "elastic_score": [0.2],
            "gbrt_score": [0.3],
            "ensemble_score": [0.2],
        }
    )
    score_path = tmp_path / "scores.parquet"
    scores.to_parquet(score_path, index=False)
    observed: dict[str, pd.Timestamp] = {}

    def fake_execution_bars(start, requested_end):
        observed["start"] = pd.Timestamp(start)
        observed["end"] = pd.Timestamp(requested_end)
        return pd.DataFrame()

    def fake_index_bars(code, start, requested_end):
        return pd.DataFrame(
            {
                "date": pd.to_datetime(["2024-01-02", "2024-01-03"]),
                "open": [100.0, 101.0],
            }
        )

    def fake_portfolio(*args, **kwargs):
        equity = pd.DataFrame(
            {
                "date": pd.to_datetime(["2024-01-02", "2024-01-03"]),
                "cash": [1_000_000.0, 1_000_000.0],
                "market_value": [0.0, 0.0],
                "equity": [1_000_000.0, 1_000_000.0],
                "n_positions": [0, 0],
                "cash_ratio": [1.0, 1.0],
                "daily_ret": [0.0, 0.0],
                "daily_pnl": [0.0, 0.0],
                "nav": [1.0, 1.0],
            }
        )
        return {
            "equity": equity,
            "trades": pd.DataFrame(),
            "pending_orders": pd.DataFrame(),
        }

    def fake_metrics(*args, **kwargs):
        return {
            "returns": {"ann_return": 0.0},
            "risk": {"sharpe": 0.0, "max_drawdown": 0.0},
            "trading": {"turnover_ann": 0.0},
            "merged_daily": pd.DataFrame(),
        }

    monkeypatch.setattr(pipeline, "load_execution_bars", fake_execution_bars)
    monkeypatch.setattr(pipeline, "load_index_bars", fake_index_bars)
    monkeypatch.setattr(pipeline, "run_portfolio", fake_portfolio)
    monkeypatch.setattr(pipeline, "compute_metrics", fake_metrics)

    pipeline.run_backtest_dataset(
        "2024-01-02",
        str(end.date()),
        score_root=score_path,
        report_root=tmp_path / "reports",
    )

    assert observed == {"start": pd.Timestamp("2024-01-02"), "end": end}


def test_flat_cost_stresses_hold_the_trade_path_fixed(tmp_path, monkeypatch) -> None:
    dates = pd.to_datetime(["2024-01-02", "2024-01-03"])
    scores = pd.DataFrame(
        {
            "code": ["000001", "000001"],
            "date": dates,
            "eligible": [True, True],
            "target_rank": [0.5, 0.5],
            "label_available_date": dates,
            "factor_score": [0.1, 0.1],
            "elastic_score": [0.2, 0.2],
            "gbrt_score": [0.3, 0.3],
            "ensemble_score": [0.2, 0.2],
        }
    )
    score_path = tmp_path / "scores.parquet"
    scores.to_parquet(score_path, index=False)

    monkeypatch.setattr(pipeline, "load_execution_bars", lambda *args: pd.DataFrame())
    monkeypatch.setattr(
        pipeline,
        "load_index_bars",
        lambda *args: pd.DataFrame({"date": dates, "open": [100.0, 101.0]}),
    )

    def fake_portfolio(*args, params, initial_capital, **kwargs):
        # Re-running the portfolio at different costs deliberately changes this
        # synthetic path in the wrong direction. A valid flat-cost stress must
        # run the zero-cost path once and only deduct fees afterward.
        ending_equity = initial_capital + params.cost_bps * 1_000.0
        equity = pd.DataFrame(
            {
                "date": dates,
                "cash": [initial_capital, ending_equity - 100_000.0],
                "market_value": [0.0, 100_000.0],
                "equity": [initial_capital, ending_equity],
                "n_positions": [0, 1],
                "cash_ratio": [1.0, (ending_equity - 100_000.0) / ending_equity],
                "daily_ret": [0.0, ending_equity / initial_capital - 1.0],
                "daily_pnl": [0.0, ending_equity - initial_capital],
                "nav": [1.0, ending_equity / initial_capital],
            }
        )
        trades = pd.DataFrame(
            {
                "signal_date": [dates[0]],
                "date": [dates[1]],
                "code": ["000001"],
                "side": ["BUY"],
                "shares": [10_000],
                "price": [10.0],
                "amount": [100_000.0],
                "fee": [0.0],
                "industry": ["银行"],
                "adv_20": [100_000_000.0],
                "float_market_cap": [50_000_000_000.0],
                "realized_pnl": [0.0],
                "hold_days": [0],
            }
        )
        return {
            "equity": equity,
            "trades": trades,
            "pending_orders": pd.DataFrame(),
        }

    def fake_metrics(daily, trades, benchmark, initial_capital, **kwargs):
        return {
            "returns": {
                "ann_return": float(daily["equity"].iloc[-1] / initial_capital - 1.0)
            },
            "risk": {"sharpe": 0.0, "max_drawdown": 0.0},
            "trading": {"turnover_ann": 0.0},
            "capital": {"total_fees": float(trades["fee"].sum())},
            "merged_daily": pd.DataFrame(),
        }

    monkeypatch.setattr(pipeline, "run_portfolio", fake_portfolio)
    monkeypatch.setattr(pipeline, "compute_metrics", fake_metrics)

    payload = pipeline.run_backtest_dataset(
        str(dates[0].date()),
        str(dates[-1].date()),
        score_root=score_path,
        report_root=tmp_path / "reports",
    )

    scenarios = payload["scenarios"]
    returns = [
        scenarios[f"gbrt_flat_{bps}bp_roundtrip"]["returns"]["ann_return"]
        for bps in (20, 40, 80)
    ]
    fees = [
        scenarios[f"gbrt_flat_{bps}bp_roundtrip"]["capital"]["total_fees"]
        for bps in (20, 40, 80)
    ]
    assert returns[0] > returns[1] > returns[2]
    assert fees[0] < fees[1] < fees[2]


def test_score_diagnostics_exclude_labels_realized_after_the_public_end(
    tmp_path, monkeypatch
) -> None:
    dates = pd.to_datetime(["2024-01-02"] * 5 + ["2024-01-03"] * 5)
    values = list(range(5)) * 2
    scores = pd.DataFrame(
        {
            "code": [f"00000{value}" for value in range(5)] * 2,
            "date": dates,
            "eligible": True,
            "target_rank": values,
            "label_available_date": pd.to_datetime(
                ["2024-01-03"] * 5 + ["2024-01-10"] * 5
            ),
            "factor_score": values,
            "elastic_score": values,
            "gbrt_score": values,
            "ensemble_score": values,
        }
    )
    score_path = tmp_path / "scores.parquet"
    scores.to_parquet(score_path, index=False)

    monkeypatch.setattr(pipeline, "load_execution_bars", lambda *args: pd.DataFrame())
    monkeypatch.setattr(
        pipeline,
        "load_index_bars",
        lambda *args: pd.DataFrame(
            {
                "date": pd.to_datetime(["2024-01-02", "2024-01-03"]),
                "open": [100.0, 101.0],
            }
        ),
    )

    def fake_portfolio(*args, **kwargs):
        equity = pd.DataFrame(
            {
                "date": pd.to_datetime(["2024-01-02", "2024-01-03"]),
                "cash": [1_000_000.0, 1_000_000.0],
                "market_value": [0.0, 0.0],
                "equity": [1_000_000.0, 1_000_000.0],
                "n_positions": [0, 0],
                "cash_ratio": [1.0, 1.0],
                "daily_ret": [0.0, 0.0],
                "daily_pnl": [0.0, 0.0],
                "nav": [1.0, 1.0],
            }
        )
        return {
            "equity": equity,
            "trades": pd.DataFrame(),
            "pending_orders": pd.DataFrame(),
        }

    monkeypatch.setattr(pipeline, "run_portfolio", fake_portfolio)
    monkeypatch.setattr(
        pipeline,
        "compute_metrics",
        lambda *args, **kwargs: {
            "returns": {"ann_return": 0.0},
            "risk": {"sharpe": 0.0, "max_drawdown": 0.0},
            "trading": {"turnover_ann": 0.0},
            "merged_daily": pd.DataFrame(),
        },
    )

    payload = pipeline.run_backtest_dataset(
        "2024-01-02",
        "2024-01-03",
        score_root=score_path,
        report_root=tmp_path / "reports",
    )

    assert payload["score_diagnostics"]["gbrt_score"]["days"] == 1
