"""Reproducible panel, score and backtest pipeline for the rotating strategy."""

from __future__ import annotations

import json
from dataclasses import asdict, replace
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from backtest.metrics import compute_metrics
from strategy.rotating.config.params import DEFAULT_PARAMS, Params
from strategy.rotating.engine.portfolio import run_portfolio
from strategy.rotating.features.build_panel import (
    MODEL_INPUT_FEATURES,
    MODEL_TARGET_COLUMN,
    build_factor_panel,
)
from strategy.rotating.lib.data import (
    load_execution_bars,
    load_index_bars,
    load_point_in_time_source,
)
from strategy.rotating.model.walkforward import walk_forward_score


STRATEGY_ROOT = Path(__file__).resolve().parent
CACHE_ROOT = STRATEGY_ROOT / "cache"
PANEL_ROOT = CACHE_ROOT / "panel"
SCORE_ROOT = CACHE_ROOT / "scores"
REPORT_ROOT = STRATEGY_ROOT / "reports"
SELECTED_RESEARCH_SCORE_COLUMN = "gbrt_score"
SCORE_SPEC_FILENAME = "_SCORE_SPEC.json"


def _atomic_parquet(frame: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp.parquet")
    frame.to_parquet(temporary, index=False)
    temporary.replace(path)


def build_panel_dataset(
    start: str,
    end: str,
    *,
    output_root: str | Path = PANEL_ROOT,
    data_cutoff: str | None = None,
) -> list[Path]:
    """Build year partitions with sufficient pre-history and future label bars."""
    first = pd.Timestamp(start).normalize()
    last = pd.Timestamp(end).normalize()
    cutoff = pd.Timestamp(data_cutoff).normalize() if data_cutoff else None
    if cutoff is not None and last > cutoff:
        raise ValueError("panel end cannot be later than data_cutoff")
    root = Path(output_root)
    outputs: list[Path] = []
    for year in range(first.year, last.year + 1):
        partition_start = max(first, pd.Timestamp(year=year, month=1, day=1))
        partition_end = min(last, pd.Timestamp(year=year, month=12, day=31))
        load_start = partition_start - pd.DateOffset(days=450)
        load_end = partition_end + pd.DateOffset(days=30)
        if cutoff is not None:
            load_end = min(load_end, cutoff)
        source = load_point_in_time_source(load_start, load_end)
        panel = build_factor_panel(
            str(partition_start.date()),
            str(partition_end.date()),
            source=source,
        )
        output = root / f"year={year}" / "panel.parquet"
        _atomic_parquet(panel, output)
        outputs.append(output)
    return outputs


def score_panel_dataset(
    start: str,
    end: str,
    *,
    panel_root: str | Path = PANEL_ROOT,
    output_root: str | Path = SCORE_ROOT,
    min_train_rows: int = 2_000,
    train_years: int = 3,
    random_state: int = 17,
    feature_columns: tuple[str, ...] = MODEL_INPUT_FEATURES,
    target_column: str = MODEL_TARGET_COLUMN,
) -> list[Path]:
    """Create leakage-safe daily scores, fitting each model once per quarter."""
    first = pd.Timestamp(start).normalize()
    last = pd.Timestamp(end).normalize()
    panel_path = Path(panel_root)
    score_path = Path(output_root)
    columns = list(
        dict.fromkeys(
            [
                "code",
                "date",
                "eligible",
                "target_rank",
                target_column,
                "target_5",
                "label_available_date",
                "industry",
                "adv_20",
                "float_market_cap",
                "amount",
                *feature_columns,
            ]
        )
    )
    outputs: list[Path] = []
    for year in range(first.year, last.year + 1):
        prediction_start = max(first, pd.Timestamp(year=year, month=1, day=1))
        prediction_end = min(last, pd.Timestamp(year=year, month=12, day=31))
        train_start = prediction_start - pd.DateOffset(years=train_years)
        panel = pd.read_parquet(
            panel_path,
            columns=columns,
            filters=[
                ("date", ">=", train_start),
                ("date", "<=", prediction_end),
            ],
        )
        panel["date"] = pd.to_datetime(panel["date"]).dt.normalize()
        prediction_dates = sorted(
            panel.loc[
                panel["eligible"].astype(bool)
                & panel["date"].between(prediction_start, prediction_end),
                "date",
            ].unique()
        )
        scores = walk_forward_score(
            panel,
            prediction_dates=prediction_dates,
            feature_columns=feature_columns,
            target_column=target_column,
            min_train_rows=min_train_rows,
            train_years=train_years,
            random_state=random_state,
        )
        metadata_columns = [
            "code",
            "date",
            "eligible",
            "industry",
            "adv_20",
            "float_market_cap",
            "amount",
            "target_5",
            "target_rank",
            "label_available_date",
        ]
        if target_column != "target_rank":
            metadata_columns.append(target_column)
        metadata = panel.loc[
            panel["date"].between(prediction_start, prediction_end),
            metadata_columns,
        ].drop_duplicates(["code", "date"], keep="last")
        if target_column == "target_rank":
            metadata["model_target_rank"] = metadata["target_rank"]
        else:
            metadata = metadata.rename(columns={target_column: "model_target_rank"})
        scores = scores.merge(
            metadata,
            on=["code", "date"],
            how="left",
            validate="one_to_one",
        )
        output = score_path / f"year={year}" / "scores.parquet"
        _atomic_parquet(scores, output)
        outputs.append(output)
    score_path.mkdir(parents=True, exist_ok=True)
    (score_path / SCORE_SPEC_FILENAME).write_text(
        json.dumps(
            {
                "feature_columns": list(feature_columns),
                "target_column": target_column,
                "min_train_rows": int(min_train_rows),
                "train_years": int(train_years),
                "random_state": int(random_state),
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return outputs


def _json_ready(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _json_ready(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_ready(item) for item in value]
    if hasattr(value, "item"):
        return value.item()
    if pd.isna(value):
        return None
    return value


def _load_score_specification(score_root: str | Path) -> dict[str, Any] | None:
    root = Path(score_root)
    if not root.is_dir():
        return None
    path = root / SCORE_SPEC_FILENAME
    if not path.exists():
        return None
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"score specification at {path} must be a JSON object")
    return payload


def _apply_flat_cost_stress(
    base_result: dict[str, Any],
    *,
    per_side_bps: float,
    initial_capital: float,
) -> dict[str, Any]:
    """Deduct flat all-in costs from one fixed zero-cost trade path."""
    stressed = {
        key: value.copy(deep=True) if isinstance(value, pd.DataFrame) else value
        for key, value in base_result.items()
    }
    trades = stressed["trades"]
    equity = stressed["equity"]
    if trades.empty:
        return stressed

    trades["date"] = pd.to_datetime(trades["date"]).dt.normalize()
    trades["fee"] = (
        pd.to_numeric(trades["amount"], errors="coerce").fillna(0.0)
        * float(per_side_bps)
        / 10_000.0
    )
    entry_fees: dict[str, float] = {}
    for index, trade in trades.sort_values(["date", "side"]).iterrows():
        code = str(trade["code"])
        fee = float(trade["fee"])
        if str(trade["side"]).upper() == "BUY":
            entry_fees[code] = fee
            continue
        original_pnl = float(trade.get("realized_pnl", 0.0))
        trades.at[index, "realized_pnl"] = (
            original_pnl - fee - entry_fees.pop(code, 0.0)
        )

    equity["date"] = pd.to_datetime(equity["date"]).dt.normalize()
    fee_by_date = trades.groupby("date", sort=True)["fee"].sum()
    cumulative_cost = (
        equity["date"].map(fee_by_date).fillna(0.0).astype(float).cumsum()
    )
    equity["cash"] = pd.to_numeric(equity["cash"], errors="coerce") - cumulative_cost
    equity["equity"] = (
        pd.to_numeric(equity["equity"], errors="coerce") - cumulative_cost
    )
    equity["cash_ratio"] = equity["cash"].div(equity["equity"].replace(0.0, pd.NA))
    equity["daily_ret"] = equity["equity"].pct_change().fillna(0.0)
    equity["daily_pnl"] = equity["equity"].diff().fillna(
        equity["equity"].iloc[0] - float(initial_capital)
    )
    equity["nav"] = equity["equity"] / float(initial_capital)
    if "cash" in stressed:
        stressed["cash"] = float(stressed["cash"]) - float(cumulative_cost.iloc[-1])
    return stressed


def _rank_ic_summary(
    values: pd.Series, *, hac_lags: int = 4
) -> dict[str, Any]:
    series = pd.Series(values, dtype=float).dropna().reset_index(drop=True)
    standard_deviation = series.std()
    hac_standard_error = None
    hac_t_stat = None
    if len(series) > 1:
        centered = series.to_numpy(dtype=float) - float(series.mean())
        gamma_zero = float(np.dot(centered, centered) / len(centered))
        long_run_variance = gamma_zero
        effective_lags = min(int(hac_lags), len(centered) - 1)
        for lag in range(1, effective_lags + 1):
            covariance = float(
                np.dot(centered[lag:], centered[:-lag]) / len(centered)
            )
            weight = 1.0 - lag / (hac_lags + 1.0)
            long_run_variance += 2.0 * weight * covariance
        hac_standard_error = float(
            np.sqrt(max(long_run_variance, 0.0) / len(centered))
        )
        if hac_standard_error > 0.0:
            hac_t_stat = float(series.mean() / hac_standard_error)
    return {
        "days": int(len(series)),
        "rank_ic_mean": float(series.mean()) if len(series) else None,
        "rank_ic_std": float(standard_deviation) if len(series) > 1 else None,
        "rank_icir_annualized": (
            float(series.mean() / standard_deviation * (242.0**0.5))
            if len(series) > 1 and standard_deviation > 0.0
            else None
        ),
        "positive_day_fraction": (
            float(series.gt(0.0).mean()) if len(series) else None
        ),
        "rank_ic_hac_lags": int(hac_lags),
        "rank_ic_hac_standard_error": hac_standard_error,
        "rank_ic_hac_t_stat": hac_t_stat,
    }


def _score_diagnostics(
    scores: pd.DataFrame,
    *,
    evaluation_end: pd.Timestamp,
    target_column: str = "target_rank",
) -> dict[str, Any]:
    diagnostics: dict[str, Any] = {}
    if target_column not in scores:
        return diagnostics
    if "label_available_date" not in scores:
        raise ValueError("score diagnostics require label_available_date")
    available = pd.to_datetime(scores["label_available_date"]).dt.normalize()
    realized = scores.loc[
        scores[target_column].notna()
        & available.le(pd.Timestamp(evaluation_end).normalize())
    ]
    ordered_dates = sorted(pd.to_datetime(realized["date"]).dt.normalize().unique())
    cohort_by_date = {
        pd.Timestamp(date): int(index % 5)
        for index, date in enumerate(ordered_dates)
    }
    for score_column in (
        "factor_score",
        "elastic_score",
        "gbrt_score",
        "ensemble_score",
    ):
        daily_ic: list[dict[str, Any]] = []
        for date, cross_section in realized.groupby("date", sort=True):
            if len(cross_section) < 5:
                continue
            ic = cross_section[score_column].corr(
                cross_section[target_column], method="spearman"
            )
            if pd.notna(ic):
                normalized_date = pd.Timestamp(date).normalize()
                daily_ic.append(
                    {
                        "date": normalized_date,
                        "rank_ic": float(ic),
                        "year": int(normalized_date.year),
                        "cohort": cohort_by_date[normalized_date],
                    }
                )
        daily_frame = pd.DataFrame(daily_ic)
        summary = _rank_ic_summary(
            daily_frame["rank_ic"] if not daily_frame.empty else pd.Series(dtype=float)
        )
        summary["rank_ic_by_year"] = (
            {
                str(year): _rank_ic_summary(group["rank_ic"])
                for year, group in daily_frame.groupby("year", sort=True)
            }
            if not daily_frame.empty
            else {}
        )
        summary["rank_ic_by_cohort"] = (
            {
                str(cohort): _rank_ic_summary(group["rank_ic"])
                for cohort, group in daily_frame.groupby("cohort", sort=True)
            }
            if not daily_frame.empty
            else {}
        )
        diagnostics[score_column] = summary
    return diagnostics


def _admission_diagnostics(result: dict[str, Any]) -> dict[str, Any]:
    """Expose capacity/concentration evidence without claiming production admission."""
    equity = result["equity"].copy()
    trades = result["trades"].copy()
    pending = result["pending_orders"]
    by_year: dict[str, float] = {}
    if not equity.empty:
        equity["date"] = pd.to_datetime(equity["date"])
        for year, group in equity.groupby(equity["date"].dt.year, sort=True):
            start_equity = float(group["equity"].iloc[0])
            end_equity = float(group["equity"].iloc[-1])
            by_year[str(year)] = end_equity / start_equity - 1.0

    buys = trades.loc[trades.get("side", pd.Series(dtype=str)).eq("BUY")].copy()
    utilization = pd.Series(dtype=float)
    if not buys.empty and "adv_20" in buys:
        utilization = buys["amount"] / pd.to_numeric(
            buys["adv_20"], errors="coerce"
        ).replace(0.0, pd.NA)

    sells = trades.loc[trades.get("side", pd.Series(dtype=str)).eq("SELL")].copy()
    industry_concentration = None
    microcap_pnl_fraction = None
    if not sells.empty:
        pnl = pd.to_numeric(sells["realized_pnl"], errors="coerce").fillna(0.0)
        gross_abs_pnl = float(pnl.abs().sum())
        if gross_abs_pnl > 0.0:
            by_industry = sells.assign(_pnl=pnl).groupby("industry")["_pnl"].sum()
            industry_concentration = float(by_industry.abs().max() / gross_abs_pnl)
            microcap = pd.to_numeric(
                sells["float_market_cap"], errors="coerce"
            ).lt(10e9)
            microcap_pnl_fraction = float(pnl.loc[microcap].abs().sum() / gross_abs_pnl)

    return {
        "annual_returns": by_year,
        "end_pending_orders": int(len(pending)),
        "max_positions": (
            int(equity["n_positions"].max()) if not equity.empty else 0
        ),
        "max_buy_adv_fraction": (
            float(utilization.max()) if utilization.notna().any() else None
        ),
        "largest_industry_abs_pnl_fraction": industry_concentration,
        "microcap_abs_pnl_fraction": microcap_pnl_fraction,
        "dsr_evaluated": False,
    }


def _append_score_diagnostics_markdown(
    markdown: list[str],
    *,
    title: str,
    diagnostics: dict[str, Any],
) -> None:
    if not diagnostics:
        return
    markdown.extend(
        [
            "",
            f"## {title}",
            "",
            "| 分数 | 日数 | 平均 Rank IC | 年化 ICIR | HAC(4) t | 正 IC 日占比 |",
            "|---|---:|---:|---:|---:|---:|",
        ]
    )
    for name, diagnostic in diagnostics.items():
        markdown.append(
            "| {name} | {days} | {mean:.4f} | {icir:.2f} | {hac:.2f} | "
            "{positive:.1%} |".format(
                name=name,
                days=diagnostic["days"],
                mean=(
                    diagnostic["rank_ic_mean"]
                    if diagnostic["rank_ic_mean"] is not None
                    else float("nan")
                ),
                icir=(
                    diagnostic["rank_icir_annualized"]
                    if diagnostic["rank_icir_annualized"] is not None
                    else float("nan")
                ),
                hac=(
                    diagnostic["rank_ic_hac_t_stat"]
                    if diagnostic["rank_ic_hac_t_stat"] is not None
                    else float("nan")
                ),
                positive=(
                    diagnostic["positive_day_fraction"]
                    if diagnostic["positive_day_fraction"] is not None
                    else float("nan")
                ),
            )
        )


def run_backtest_dataset(
    start: str,
    end: str,
    *,
    score_root: str | Path = SCORE_ROOT,
    report_root: str | Path = REPORT_ROOT,
    params: Params = DEFAULT_PARAMS,
    initial_capital: float = 1_000_000.0,
) -> dict[str, Any]:
    """Run realistic execution plus fixed-path 20/40/80bp cost stresses."""
    first = pd.Timestamp(start).normalize()
    last = pd.Timestamp(end).normalize()
    scores = pd.read_parquet(
        Path(score_root),
        filters=[("date", ">=", first), ("date", "<=", last)],
    )
    # ``end`` is the public performance boundary. Signals emitted on the final
    # day may remain pending, but later bars and fills must not leak into the
    # report's fees, turnover or realized PnL.
    bars = load_execution_bars(first, last)
    benchmark = load_index_bars("000300", first, last)
    benchmark["date"] = pd.to_datetime(benchmark["date"]).dt.normalize()
    benchmark["ret"] = benchmark["open"].pct_change().fillna(0.0)

    scenarios: dict[str, tuple[str, Params | None, float | None]] = {
        "factor_historical": ("factor_score", params, None),
        "elastic_historical": ("elastic_score", params, None),
        "gbrt_historical": ("gbrt_score", params, None),
        "ensemble_historical": ("ensemble_score", params, None),
    }
    for score_column in (
        "factor_score",
        "elastic_score",
        "gbrt_score",
        "ensemble_score",
    ):
        label = score_column.removesuffix("_score")
        scenarios[f"{label}_flat_20bp_roundtrip"] = (score_column, None, 20.0)
    for round_trip_bps in (40, 80):
        scenarios[f"gbrt_flat_{round_trip_bps}bp_roundtrip"] = (
            SELECTED_RESEARCH_SCORE_COLUMN,
            None,
            float(round_trip_bps),
        )

    root = Path(report_root)
    root.mkdir(parents=True, exist_ok=True)
    summaries: dict[str, Any] = {}
    zero_cost_params = replace(
        params,
        enable_realistic_costs=False,
        cost_bps=0.0,
        enable_open_slippage=False,
    )
    flat_paths: dict[str, dict[str, Any]] = {}
    for name, (score_column, scenario_params, round_trip_bps) in scenarios.items():
        scenario_scores = scores.copy()
        scenario_scores["ensemble_score"] = scenario_scores[score_column]
        if round_trip_bps is None:
            assert scenario_params is not None
            result = run_portfolio(
                scenario_scores,
                bars,
                params=scenario_params,
                initial_capital=initial_capital,
            )
        else:
            if score_column not in flat_paths:
                flat_paths[score_column] = run_portfolio(
                    scenario_scores,
                    bars,
                    params=zero_cost_params,
                    initial_capital=initial_capital,
                )
            result = _apply_flat_cost_stress(
                flat_paths[score_column],
                per_side_bps=round_trip_bps / 2.0,
                initial_capital=initial_capital,
            )
        metrics = compute_metrics(
            result["equity"],
            result["trades"],
            benchmark,
            initial_capital,
            benchmark_name="沪深300",
            benchmark_code="000300",
        )
        metrics["admission_diagnostics"] = _admission_diagnostics(result)
        merged = metrics.pop("merged_daily", pd.DataFrame())
        result["equity"].to_csv(root / f"equity_{name}.csv", index=False)
        result["trades"].to_csv(root / f"trades_{name}.csv", index=False)
        if not merged.empty:
            merged.to_csv(root / f"benchmark_{name}.csv", index=False)
        if name == "gbrt_historical":
            result["equity"].to_csv(root / "ledger_daily.csv", index=False)
            result["trades"].to_csv(root / "trades.csv", index=False)
        summaries[name] = metrics

    payload = {
        "strategy": "rotating",
        "start": str(first.date()),
        "end": str(last.date()),
        "selected_research_score_column": SELECTED_RESEARCH_SCORE_COLUMN,
        "admission_status": "research_only_forward_validation_required",
        "params": asdict(params),
        "score_specification": _load_score_specification(score_root),
        "score_diagnostics": _score_diagnostics(scores, evaluation_end=last),
        "model_target_diagnostics": _score_diagnostics(
            scores,
            evaluation_end=last,
            target_column="model_target_rank",
        ),
        "scenarios": summaries,
    }
    (root / "PERFORMANCE.json").write_text(
        json.dumps(_json_ready(payload), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    markdown = [
        "# rotating performance",
        "",
        f"区间：{first.date()} 至 {last.date()}；初始资金：{initial_capital:,.0f} 元。",
        "",
        "| 场景 | 年化收益 | Sharpe | 最大回撤 | 年化换手 |",
        "|---|---:|---:|---:|---:|",
    ]
    for name, metrics in summaries.items():
        returns = metrics.get("returns", {})
        risk = metrics.get("risk", {})
        trading = metrics.get("trading", {})
        markdown.append(
            "| {name} | {ann:.2%} | {sharpe:.2f} | {drawdown:.2%} | {turnover:.2f} |".format(
                name=name,
                ann=float(returns.get("ann_return", float("nan"))),
                sharpe=float(risk.get("sharpe", float("nan"))),
                drawdown=float(risk.get("max_drawdown", float("nan"))),
                turnover=float(trading.get("turnover_ann", float("nan"))),
            )
        )
    _append_score_diagnostics_markdown(
        markdown,
        title="Rank IC",
        diagnostics=payload["score_diagnostics"],
    )
    _append_score_diagnostics_markdown(
        markdown,
        title="模型训练标签 Rank IC",
        diagnostics=payload["model_target_diagnostics"],
    )
    markdown.extend(
        [
            "",
            "ICIR 基于重叠的五日标签，仅作模型比较；显著性判断需使用重叠收益稳健误差。",
            "",
            "结果仅用于研究，不构成收益承诺；正式结论必须同时检查数据覆盖、未成交积压、容量和未参与调参的封存区间。",
            "",
        ]
    )
    (root / "PERFORMANCE.md").write_text("\n".join(markdown), encoding="utf-8")
    return payload
