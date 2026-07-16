from __future__ import annotations

import json

import pandas as pd
import pytest

from strategy.rotating import pipeline
from strategy.rotating.experiments import (
    build_industry_size_diagnostics as industry_diagnostics,
)


def test_score_panel_dataset_rejects_mixed_specs_in_one_score_root(tmp_path) -> None:
    score_root = tmp_path / "scores"
    stale_partition = score_root / "year=2021" / "scores.parquet"
    stale_partition.parent.mkdir(parents=True)
    pd.DataFrame({"date": pd.to_datetime(["2021-01-04"])}).to_parquet(
        stale_partition, index=False
    )
    (score_root / pipeline.SCORE_SPEC_FILENAME).write_text(
        json.dumps(
            {
                "feature_columns": ["rank_ret_5"],
                "target_column": "target_rank",
                "min_train_rows": 2_000,
                "train_years": 3,
                "random_state": 17,
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="different score specification"):
        pipeline.score_panel_dataset(
            "2022-01-01",
            "2022-12-31",
            panel_root=tmp_path / "panel",
            output_root=score_root,
            feature_columns=("rank_ret_5",),
            target_column="target_path_rank",
        )


def test_build_panel_dataset_never_reads_after_explicit_data_cutoff(
    tmp_path, monkeypatch
) -> None:
    requested: list[tuple[pd.Timestamp, pd.Timestamp]] = []

    def fake_source(start, end):
        requested.append((pd.Timestamp(start), pd.Timestamp(end)))
        return pd.DataFrame()

    monkeypatch.setattr(pipeline, "load_point_in_time_source", fake_source)
    monkeypatch.setattr(
        pipeline,
        "build_factor_panel",
        lambda *args, **kwargs: pd.DataFrame(
            {"date": pd.to_datetime(["2023-01-03"])}
        ),
    )

    pipeline.build_panel_dataset(
        "2023-01-01",
        "2023-12-31",
        output_root=tmp_path / "panel",
        data_cutoff="2023-12-31",
    )

    assert requested
    assert max(end for _, end in requested) <= pd.Timestamp("2023-12-31")


def test_industry_size_augmentation_only_adds_features_to_frozen_partitions(
    tmp_path, monkeypatch
) -> None:
    size = pd.Series(range(12), dtype=float) / 10.0 + 23.0
    frozen = pd.DataFrame(
        {
            "code": [f"000{index:03d}" for index in range(12)],
            "date": pd.Timestamp("2023-12-29"),
            "industry": ["银行"] * 6 + ["制造"] * 6,
            "eligible": True,
            "log_float_market_cap": size,
            "ep": 0.05 + size / 1_000.0,
            "bp": 0.4 + size / 100.0,
            "roe": 0.08 + size / 1_000.0,
            "target_rank": pd.Series(range(12), dtype=float) / 11.0,
            "label_available_date": pd.Timestamp("2023-12-29"),
        }
    )
    input_root = tmp_path / "frozen"
    source_path = input_root / "year=2023" / "panel.parquet"
    source_path.parent.mkdir(parents=True)
    poisoned = frozen.iloc[[0]].assign(
        code="poisoned",
        date=pd.Timestamp("2023-12-30"),
    )
    pd.concat([frozen, poisoned], ignore_index=True).to_parquet(
        source_path, index=False
    )
    real_read_parquet = pd.read_parquet
    observed_filters: list[list[tuple[str, str, pd.Timestamp]]] = []

    def guarded_read_parquet(path, *args, **kwargs):
        filters = kwargs.get("filters")
        assert filters is not None
        observed_filters.append(filters)
        loaded = real_read_parquet(path, *args, **kwargs)
        assert "poisoned" not in loaded["code"].tolist()
        return loaded

    monkeypatch.setattr(pipeline.pd, "read_parquet", guarded_read_parquet)

    outputs = pipeline.augment_panel_dataset_with_industry_size_features(
        "2023-12-29",
        "2023-12-29",
        input_root=input_root,
        output_root=tmp_path / "candidate",
    )

    candidate = real_read_parquet(outputs[0])
    pd.testing.assert_frame_equal(
        candidate[list(frozen.columns)],
        frozen,
        check_dtype=False,
    )
    assert candidate["rank_ep_industry_size_residual"].notna().all()
    assert observed_filters == [
        [
            ("date", ">=", pd.Timestamp("2023-12-29")),
            ("date", "<=", pd.Timestamp("2023-12-29")),
        ]
    ]
    assert "rank_ep_industry_size_residual" not in real_read_parquet(source_path)


def test_industry_size_augmentation_rejects_in_place_mutation(tmp_path) -> None:
    with pytest.raises(ValueError, match="separate output root"):
        pipeline.augment_panel_dataset_with_industry_size_features(
            "2023-01-01",
            "2023-12-31",
            input_root=tmp_path / "panel",
            output_root=tmp_path / "panel",
        )


def test_backtest_rejects_a_mismatched_requested_feature_set(tmp_path) -> None:
    score_root = tmp_path / "scores"
    score_root.mkdir()
    (score_root / pipeline.SCORE_SPEC_FILENAME).write_text(
        json.dumps(
            {
                "feature_columns": ["rank_ret_5"],
                "target_column": "target_rank",
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="requested feature set"):
        pipeline.run_backtest_dataset(
            "2023-01-01",
            "2023-12-31",
            score_root=score_root,
            report_root=tmp_path / "reports",
            expected_feature_columns=("rank_ret_5", "rank_ret_20"),
        )


def test_industry_size_gate_rejects_incomplete_year_and_cohort_coverage(
    tmp_path,
) -> None:
    baseline_root = tmp_path / "baseline"
    candidate_root = tmp_path / "candidate"
    baseline_root.mkdir()
    candidate_root.mkdir()

    def performance(*, candidate: bool) -> dict:
        annual_return = 0.20 if candidate else 0.10
        sharpe = 1.0 if candidate else 0.5
        yearly = [
            {
                "year": year,
                "strategy": annual_return,
                "strategy_sharpe": sharpe,
            }
            for year in (2021, 2022)
        ]
        cohorts = {
            str(index): {"rank_ic_mean": 0.10}
            for index in range(4)
        }
        scenario = {
            "returns": {"ann_return": annual_return},
            "risk": {"sharpe": sharpe, "max_drawdown": -0.10},
            "trading": {"turnover_ann": 1.0},
            "yearly": yearly,
        }
        return {
            "scenarios": {
                "gbrt_historical": scenario,
                "gbrt_flat_20bp_roundtrip": scenario,
            },
            "score_diagnostics": {
                "gbrt_score": {
                    "rank_ic_mean": 0.10,
                    "rank_ic_by_cohort": cohorts,
                }
            },
        }

    (baseline_root / "PERFORMANCE.json").write_text(
        json.dumps(performance(candidate=False)), encoding="utf-8"
    )
    (candidate_root / "PERFORMANCE.json").write_text(
        json.dumps(performance(candidate=True)), encoding="utf-8"
    )
    pd.DataFrame(
        {"date": ["2023-01-03", "2023-01-04"], "daily_ret": [0.0, 0.0]}
    ).to_csv(baseline_root / "equity_gbrt_historical.csv", index=False)
    pd.DataFrame(
        {"date": ["2023-01-03", "2023-01-04"], "daily_ret": [0.01, 0.01]}
    ).to_csv(candidate_root / "equity_gbrt_historical.csv", index=False)

    result = industry_diagnostics._gate_evaluation(
        baseline_report_root=baseline_root,
        candidate_report_root=candidate_root,
    )

    assert not result["gates"]["complete_three_year_coverage"]
    assert not result["gates"]["at_least_two_yearly_double_wins"]
    assert not result["gates"]["complete_five_cohort_coverage"]
    assert not result["gates"]["all_five_candidate_cohorts_positive"]
    assert not result["all_gates_passed"]
    assert result["experiment_decision"] == "rejected_on_development"


def test_backtest_can_report_the_single_fixed_market_gate_candidate(
    tmp_path, monkeypatch
) -> None:
    evaluation_dates = pd.to_datetime(["2024-01-02", "2024-01-03"])
    scores = pd.DataFrame(
        {
            "code": [f"00000{index}" for index in range(5)] * 2,
            "date": evaluation_dates.repeat(5),
            "eligible": True,
            "target_rank": list(range(5)) * 2,
            "label_available_date": evaluation_dates.repeat(5),
            "factor_score": list(range(5)) * 2,
            "elastic_score": list(range(5)) * 2,
            "gbrt_score": list(range(5)) * 2,
            "ensemble_score": list(range(5)) * 2,
        }
    )
    score_root = tmp_path / "scores"
    partition = score_root / "year=2024" / "scores.parquet"
    partition.parent.mkdir(parents=True)
    scores.to_parquet(partition, index=False)

    observed_index_bounds: list[tuple[pd.Timestamp, pd.Timestamp]] = []
    monkeypatch.setattr(pipeline, "load_execution_bars", lambda *args: pd.DataFrame())

    def fake_index_bars(code, start, end):
        observed_index_bounds.append((pd.Timestamp(start), pd.Timestamp(end)))
        month_ends = pd.date_range("2023-01-31", periods=12, freq="ME")
        return pd.DataFrame(
            {
                "date": month_ends,
                "open": range(100, 112),
                "close": range(100, 112),
            }
        )

    monkeypatch.setattr(pipeline, "load_index_bars", fake_index_bars)
    gate_calls: list[bool] = []

    def fake_portfolio(*args, entry_gate=None, initial_capital=1_000_000.0, **kwargs):
        is_gated = entry_gate is not None
        gate_calls.append(is_gated)
        equity = pd.DataFrame(
            {
                "date": evaluation_dates,
                "cash": [initial_capital, initial_capital],
                "market_value": [0.0, 0.0],
                "equity": [initial_capital, initial_capital * (1.02 if is_gated else 1.01)],
                "n_positions": [0, 0],
                "cash_ratio": [1.0, 1.0],
                "allow_new_buys": [is_gated, is_gated],
            }
        )
        equity["daily_ret"] = equity["equity"].pct_change().fillna(0.0)
        equity["daily_pnl"] = equity["equity"].diff().fillna(0.0)
        equity["nav"] = equity["equity"] / initial_capital
        return {
            "equity": equity,
            "trades": pd.DataFrame(),
            "pending_orders": pd.DataFrame(),
            "cash": initial_capital,
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
        str(evaluation_dates[0].date()),
        str(evaluation_dates[-1].date()),
        score_root=score_root,
        report_root=tmp_path / "reports",
        include_market_gate_candidate=True,
    )

    assert set(payload["scenarios"]).issuperset(
        {"gbrt_historical", "gbrt_market_gate_historical", "gbrt_market_gate_flat_20bp_roundtrip"}
    )
    assert any(gate_calls)
    assert payload["market_gate_candidate"]["paired_daily_return_difference"]["hac_lags"] == 20
    assert min(start for start, _ in observed_index_bounds) < evaluation_dates[0]
    assert max(end for _, end in observed_index_bounds) == evaluation_dates[-1]


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
    score_root = tmp_path / "scores"
    score_path = score_root / "year=2024" / "scores.parquet"
    score_path.parent.mkdir(parents=True)
    scores.to_parquet(score_path, index=False)
    (score_root / "_SCORE_SPEC.json").write_text(
        json.dumps(
            {
                "feature_columns": ["rank_ret_5"],
                "target_column": "target_rank",
                "min_train_rows": 2000,
                "train_years": 3,
                "random_state": 17,
            }
        ),
        encoding="utf-8",
    )
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

    payload = pipeline.run_backtest_dataset(
        "2024-01-02",
        str(end.date()),
        score_root=score_root,
        report_root=tmp_path / "reports",
        experiment_decision="rejected_on_development",
    )

    assert observed == {"start": pd.Timestamp("2024-01-02"), "end": end}
    assert payload["score_specification"]["feature_columns"] == ["rank_ret_5"]
    assert payload["admission_status"] == "rejected_on_development"
    assert payload["experiment_decision"] == "rejected_on_development"


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
            "model_target_rank": [4 - value for value in values],
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
    assert payload["model_target_diagnostics"]["gbrt_score"]["days"] == 1
    diagnostic = payload["model_target_diagnostics"]["gbrt_score"]
    assert diagnostic["rank_ic_hac_lags"] == 4
    assert diagnostic["rank_ic_by_year"]["2024"]["days"] == 1
    assert diagnostic["rank_ic_by_cohort"]["0"]["days"] == 1
    assert (
        payload["model_target_diagnostics"]["gbrt_score"]["rank_ic_mean"]
        < -0.99
    )
    markdown = (tmp_path / "reports" / "PERFORMANCE.md").read_text(
        encoding="utf-8"
    )
    assert "## 模型训练标签 Rank IC" in markdown
