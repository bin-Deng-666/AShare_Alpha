"""Build the preregistered machine-readable industry/size experiment diagnostics."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

import duckdb
import numpy as np
import pandas as pd

from strategy.rotating.features.build_panel import (
    INDUSTRY_SIZE_MODEL_FEATURES,
    INDUSTRY_SIZE_MODEL_INPUT_FEATURES,
    MODEL_INPUT_FEATURES,
)


NEW_FEATURES = tuple(f"rank_{name}" for name in INDUSTRY_SIZE_MODEL_FEATURES)
VALUE_QUALITY_FEATURES = (
    "rank_ep",
    "rank_bp",
    "rank_roe",
    *NEW_FEATURES,
)
EXPECTED_DEVELOPMENT_YEARS = {2021, 2022, 2023}
EXPECTED_COHORTS = {"0", "1", "2", "3", "4"}


def _sql_path(path: Path, *, filename: str) -> str:
    return (path.resolve() / "year=*" / filename).as_posix().replace("'", "''")


def _finite(value: Any) -> float | None:
    if value is None or pd.isna(value):
        return None
    number = float(value)
    return number if np.isfinite(number) else None


def _file_fingerprint(path: Path, root: Path) -> dict[str, Any]:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            digest.update(block)
    return {
        "path": path.resolve().relative_to(root.resolve()).as_posix(),
        "size_bytes": path.stat().st_size,
        "sha256": digest.hexdigest(),
    }


def _mean_daily_correlations(
    connection: duckdb.DuckDBPyConnection,
    *,
    panel_glob: str,
    start: str,
    end: str,
    left: tuple[str, ...],
    right: tuple[str, ...],
) -> dict[str, dict[str, float | None]]:
    expressions: list[str] = []
    aliases: list[tuple[str, str, str]] = []
    for left_name in left:
        for right_name in right:
            alias = f"c{len(aliases)}"
            expressions.append(f'corr("{left_name}", "{right_name}") AS {alias}')
            aliases.append((left_name, right_name, alias))
    daily = connection.execute(
        f"""
        SELECT date, {', '.join(expressions)}
        FROM read_parquet('{panel_glob}')
        WHERE eligible
          AND date >= DATE '{start}'
          AND date <= DATE '{end}'
        GROUP BY date
        ORDER BY date
        """
    ).df()
    result: dict[str, dict[str, float | None]] = {name: {} for name in left}
    for left_name, right_name, alias in aliases:
        result[left_name][right_name] = _finite(daily[alias].mean())
    return result


def _feature_coverage(
    connection: duckdb.DuckDBPyConnection,
    *,
    panel_glob: str,
    start: str,
    end: str,
) -> dict[str, Any]:
    total = connection.execute(
        f"""
        SELECT count(*)
        FROM read_parquet('{panel_glob}')
        WHERE eligible
          AND date >= DATE '{start}'
          AND date <= DATE '{end}'
        """
    ).fetchone()[0]
    result: dict[str, Any] = {"eligible_rows": int(total), "features": {}}
    raw_validity = {
        "ep_industry_size_residual": "ep > 0 AND isfinite(ep)",
        "bp_industry_size_residual": "bp > 0 AND isfinite(bp)",
        "roe_industry_size_residual": "roe IS NOT NULL AND isfinite(roe)",
    }
    known = "coalesce(trim(industry), '') NOT IN ('', 'UNKNOWN')"
    sized = "log_float_market_cap IS NOT NULL AND isfinite(log_float_market_cap)"
    for residual, raw_valid in raw_validity.items():
        daily = connection.execute(
            f"""
            SELECT
                date,
                count("{residual}") AS valid_rows,
                count(DISTINCT industry) FILTER (
                    WHERE "{residual}" IS NOT NULL
                ) AS valid_industries
            FROM read_parquet('{panel_glob}')
            WHERE eligible
              AND date >= DATE '{start}'
              AND date <= DATE '{end}'
            GROUP BY date
            ORDER BY date
            """
        ).df()
        small_exclusions = connection.execute(
            f"""
            WITH counted AS (
                SELECT
                    *,
                    count(*) FILTER (
                        WHERE eligible AND {raw_valid} AND {known} AND {sized}
                    ) OVER (PARTITION BY date, industry) AS valid_industry_rows
                FROM read_parquet('{panel_glob}')
                WHERE date >= DATE '{start}' AND date <= DATE '{end}'
            )
            SELECT count(*)
            FROM counted
            WHERE eligible AND {raw_valid} AND {known} AND {sized}
              AND valid_industry_rows < 5
            """
        ).fetchone()[0]
        valid_rows = int(daily["valid_rows"].sum())
        result["features"][f"rank_{residual}"] = {
            "valid_rows": valid_rows,
            "coverage": valid_rows / total if total else None,
            "daily_valid_rows": {
                "min": int(daily["valid_rows"].min()),
                "median": _finite(daily["valid_rows"].median()),
                "max": int(daily["valid_rows"].max()),
            },
            "daily_valid_industries": {
                "min": int(daily["valid_industries"].min()),
                "median": _finite(daily["valid_industries"].median()),
                "max": int(daily["valid_industries"].max()),
            },
            "small_industry_excluded_rows": int(small_exclusions),
        }
    return result


def _orthogonality(
    connection: duckdb.DuckDBPyConnection,
    *,
    panel_glob: str,
    start: str,
    end: str,
) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for residual in INDUSTRY_SIZE_MODEL_FEATURES:
        max_industry_mean = connection.execute(
            f"""
            SELECT max(abs(industry_mean))
            FROM (
                SELECT date, industry, avg("{residual}") AS industry_mean
                FROM read_parquet('{panel_glob}')
                WHERE eligible AND "{residual}" IS NOT NULL
                  AND date >= DATE '{start}' AND date <= DATE '{end}'
                GROUP BY date, industry
            )
            """
        ).fetchone()[0]
        max_size_inner_product = connection.execute(
            f"""
            SELECT max(abs(size_inner_product))
            FROM (
                SELECT date,
                       sum("{residual}" * log_float_market_cap)
                           AS size_inner_product
                FROM read_parquet('{panel_glob}')
                WHERE eligible AND "{residual}" IS NOT NULL
                  AND date >= DATE '{start}' AND date <= DATE '{end}'
                GROUP BY date
            )
            """
        ).fetchone()[0]
        result[residual] = {
            "max_abs_date_industry_mean": _finite(max_industry_mean),
            "max_abs_daily_size_inner_product": _finite(max_size_inner_product),
        }
    return result


def _panel_identity(
    connection: duckdb.DuckDBPyConnection,
    *,
    frozen_glob: str,
    candidate_glob: str,
) -> dict[str, Any]:
    columns = [
        row[0]
        for row in connection.execute(
            f"DESCRIBE SELECT * FROM read_parquet('{frozen_glob}')"
        ).fetchall()
    ]
    hash_arguments = ", ".join(f'"{column}"' for column in columns)
    rows = connection.execute(
        f"""
        WITH baseline AS (
            SELECT code, date, hash({hash_arguments}) AS row_hash
            FROM read_parquet('{frozen_glob}')
        ), candidate AS (
            SELECT code, date, hash({hash_arguments}) AS row_hash
            FROM read_parquet('{candidate_glob}')
        )
        SELECT
            year(coalesce(b.date, i.date)) AS year,
            count(*) FILTER (WHERE b.code IS NOT NULL) AS baseline_rows,
            count(*) FILTER (WHERE i.code IS NOT NULL) AS candidate_rows,
            count(*) FILTER (WHERE b.code IS NULL) AS candidate_only_keys,
            count(*) FILTER (WHERE i.code IS NULL) AS baseline_only_keys,
            count(*) FILTER (
                WHERE b.code IS NOT NULL AND i.code IS NOT NULL
                  AND b.row_hash != i.row_hash
            ) AS existing_field_hash_differences
        FROM baseline b
        FULL OUTER JOIN candidate i
          ON b.code = i.code AND b.date = i.date
        GROUP BY 1
        ORDER BY 1
        """
    ).df()
    return {
        "existing_field_count": len(columns),
        "years": {
            str(int(row["year"])): {
                "baseline_rows": int(row["baseline_rows"]),
                "candidate_rows": int(row["candidate_rows"]),
                "candidate_only_keys": int(row["candidate_only_keys"]),
                "baseline_only_keys": int(row["baseline_only_keys"]),
                "existing_field_hash_differences": int(
                    row["existing_field_hash_differences"]
                ),
            }
            for _, row in rows.iterrows()
        },
    }


def _score_identity(
    connection: duckdb.DuckDBPyConnection,
    *,
    baseline_glob: str,
    candidate_glob: str,
    start: str,
    end: str,
) -> dict[str, int]:
    row = connection.execute(
        f"""
        WITH baseline AS (
            SELECT * FROM read_parquet('{baseline_glob}')
            WHERE date >= DATE '{start}' AND date <= DATE '{end}'
        ), candidate AS (
            SELECT * FROM read_parquet('{candidate_glob}')
            WHERE date >= DATE '{start}' AND date <= DATE '{end}'
        )
        SELECT
            count(*) FILTER (WHERE b.code IS NOT NULL) AS baseline_rows,
            count(*) FILTER (WHERE i.code IS NOT NULL) AS candidate_rows,
            count(*) FILTER (WHERE b.code IS NULL) AS candidate_only_keys,
            count(*) FILTER (WHERE i.code IS NULL) AS baseline_only_keys,
            count(*) FILTER (
                WHERE b.factor_score IS DISTINCT FROM i.factor_score
            ) AS factor_score_differences,
            count(*) FILTER (
                WHERE b.target_rank IS DISTINCT FROM i.target_rank
            ) AS target_differences,
            count(*) FILTER (
                WHERE b.label_available_date IS DISTINCT FROM i.label_available_date
            ) AS label_available_date_differences,
            count(*) FILTER (
                WHERE i.train_max_label_available_date > i.date
            ) AS candidate_training_label_leaks
        FROM baseline b
        FULL OUTER JOIN candidate i
          ON b.code = i.code AND b.date = i.date
        """
    ).fetchone()
    names = (
        "baseline_rows",
        "candidate_rows",
        "candidate_only_keys",
        "baseline_only_keys",
        "factor_score_differences",
        "target_differences",
        "label_available_date_differences",
        "candidate_training_label_leaks",
    )
    return {name: int(value) for name, value in zip(names, row, strict=True)}


def _paired_mean_summary(values: pd.Series, *, hac_lags: int) -> dict[str, Any]:
    series = pd.to_numeric(values, errors="coerce").dropna().reset_index(drop=True)
    standard_error = None
    t_stat = None
    if len(series) > 1:
        centered = series.to_numpy(dtype=float) - float(series.mean())
        long_run_variance = float(np.dot(centered, centered) / len(centered))
        effective_lags = min(hac_lags, len(centered) - 1)
        for lag in range(1, effective_lags + 1):
            covariance = float(
                np.dot(centered[lag:], centered[:-lag]) / len(centered)
            )
            long_run_variance += 2.0 * (1.0 - lag / (hac_lags + 1.0)) * covariance
        standard_error = float(
            np.sqrt(max(long_run_variance, 0.0) / len(centered))
        )
        if standard_error > 0.0:
            t_stat = float(series.mean() / standard_error)
    return {
        "days": int(len(series)),
        "mean_daily_difference": _finite(series.mean()) if len(series) else None,
        "annualized_mean_difference": (
            _finite(series.mean() * 242.0) if len(series) else None
        ),
        "hac_lags": hac_lags,
        "hac_standard_error": standard_error,
        "hac_t_stat": t_stat,
    }


def _gate_evaluation(
    *, baseline_report_root: Path, candidate_report_root: Path
) -> dict[str, Any]:
    baseline = json.loads(
        (baseline_report_root / "PERFORMANCE.json").read_text(encoding="utf-8")
    )
    candidate = json.loads(
        (candidate_report_root / "PERFORMANCE.json").read_text(encoding="utf-8")
    )
    b_historical = baseline["scenarios"]["gbrt_historical"]
    i_historical = candidate["scenarios"]["gbrt_historical"]
    b_20 = baseline["scenarios"]["gbrt_flat_20bp_roundtrip"]
    i_20 = candidate["scenarios"]["gbrt_flat_20bp_roundtrip"]
    paired = pd.read_csv(
        baseline_report_root / "equity_gbrt_historical.csv",
        usecols=["date", "daily_ret"],
    ).merge(
        pd.read_csv(
            candidate_report_root / "equity_gbrt_historical.csv",
            usecols=["date", "daily_ret"],
        ),
        on="date",
        how="inner",
        validate="one_to_one",
        suffixes=("_baseline", "_candidate"),
    )
    paired_summary = _paired_mean_summary(
        paired["daily_ret_candidate"] - paired["daily_ret_baseline"],
        hac_lags=4,
    )
    b_yearly = {row["year"]: row for row in b_historical["yearly"]}
    i_yearly = {row["year"]: row for row in i_historical["yearly"]}
    complete_year_coverage = (
        set(b_yearly) == EXPECTED_DEVELOPMENT_YEARS
        and set(i_yearly) == EXPECTED_DEVELOPMENT_YEARS
    )
    yearly_double_wins = sum(
        i_yearly[year]["strategy"] > b_yearly[year]["strategy"]
        and i_yearly[year]["strategy_sharpe"]
        > b_yearly[year]["strategy_sharpe"]
        for year in b_yearly.keys() & i_yearly.keys()
    )
    b_ic = baseline["score_diagnostics"]["gbrt_score"]
    i_ic = candidate["score_diagnostics"]["gbrt_score"]
    candidate_cohorts = i_ic["rank_ic_by_cohort"]
    complete_cohort_coverage = set(candidate_cohorts) == EXPECTED_COHORTS
    gates = {
        "full_period_return_and_sharpe_better": (
            i_historical["returns"]["ann_return"]
            > b_historical["returns"]["ann_return"]
            and i_historical["risk"]["sharpe"]
            > b_historical["risk"]["sharpe"]
        ),
        "complete_three_year_coverage": complete_year_coverage,
        "at_least_two_yearly_double_wins": (
            complete_year_coverage and yearly_double_wins >= 2
        ),
        "flat_20bp_return_and_sharpe_better": (
            i_20["returns"]["ann_return"] > b_20["returns"]["ann_return"]
            and i_20["risk"]["sharpe"] > b_20["risk"]["sharpe"]
        ),
        "drawdown_not_more_than_2pp_worse": (
            i_historical["risk"]["max_drawdown"]
            >= b_historical["risk"]["max_drawdown"] - 0.02
        ),
        "turnover_not_more_than_25pct_higher": (
            i_historical["trading"]["turnover_ann"]
            <= b_historical["trading"]["turnover_ann"] * 1.25
        ),
        "rank_ic_within_tolerance": (
            i_ic["rank_ic_mean"] >= b_ic["rank_ic_mean"] - 0.002
        ),
        "complete_five_cohort_coverage": complete_cohort_coverage,
        "all_five_candidate_cohorts_positive": (
            complete_cohort_coverage
            and all(
                cohort["rank_ic_mean"] > 0
                for cohort in candidate_cohorts.values()
            )
        ),
        "paired_daily_difference_positive": (
            paired_summary["mean_daily_difference"] is not None
            and paired_summary["mean_daily_difference"] > 0
        ),
    }
    passed = all(gates.values())
    return {
        "gates": gates,
        "yearly_double_wins": yearly_double_wins,
        "paired_daily_return_difference": paired_summary,
        "all_gates_passed": passed,
        "experiment_decision": (
            "development_winner_pending_fresh_forward_validation"
            if passed
            else "rejected_on_development"
        ),
    }


def _model_stability(
    connection: duckdb.DuckDBPyConnection,
    *,
    baseline_glob: str,
    candidate_glob: str,
    start: str,
    end: str,
    top_fraction: float | None = None,
) -> dict[str, Any]:
    if top_fraction is None:
        cutoff_sql = "25"
        overlap_suffix = "top25"
    else:
        if not 0.0 < top_fraction <= 1.0:
            raise ValueError("top_fraction must be in (0, 1]")
        fraction_sql = format(top_fraction, ".17g")
        cutoff_sql = (
            f"greatest(1, cast(ceil(day_rows * {fraction_sql}) as BIGINT))"
        )
        overlap_suffix = f"top{round(top_fraction * 100)}pct"
    daily = connection.execute(
        f"""
        WITH joined AS (
            SELECT
                b.code,
                b.date,
                b.elastic_score AS b_elastic,
                i.elastic_score AS i_elastic,
                b.gbrt_score AS b_gbrt,
                i.gbrt_score AS i_gbrt
            FROM read_parquet('{baseline_glob}') b
            INNER JOIN read_parquet('{candidate_glob}') i USING (code, date)
            WHERE b.date >= DATE '{start}' AND b.date <= DATE '{end}'
        ), ranked AS (
            SELECT
                *,
                rank() OVER (PARTITION BY date ORDER BY b_elastic)
                    + (count(*) OVER (PARTITION BY date, b_elastic) - 1) / 2.0
                        AS rb_elastic,
                rank() OVER (PARTITION BY date ORDER BY i_elastic)
                    + (count(*) OVER (PARTITION BY date, i_elastic) - 1) / 2.0
                        AS ri_elastic,
                rank() OVER (PARTITION BY date ORDER BY b_gbrt)
                    + (count(*) OVER (PARTITION BY date, b_gbrt) - 1) / 2.0
                        AS rb_gbrt,
                rank() OVER (PARTITION BY date ORDER BY i_gbrt)
                    + (count(*) OVER (PARTITION BY date, i_gbrt) - 1) / 2.0
                        AS ri_gbrt,
                count(*) OVER (PARTITION BY date) AS day_rows,
                row_number() OVER (
                    PARTITION BY date ORDER BY b_elastic DESC, code
                ) AS top_b_elastic,
                row_number() OVER (
                    PARTITION BY date ORDER BY i_elastic DESC, code
                ) AS top_i_elastic,
                row_number() OVER (
                    PARTITION BY date ORDER BY b_gbrt DESC, code
                ) AS top_b_gbrt,
                row_number() OVER (
                    PARTITION BY date ORDER BY i_gbrt DESC, code
                ) AS top_i_gbrt
            FROM joined
        )
        SELECT
            date,
            count(*) AS rows,
            corr(rb_elastic, ri_elastic) AS elastic_spearman,
            corr(rb_gbrt, ri_gbrt) AS gbrt_spearman,
            sum((
                top_b_elastic <= {cutoff_sql}
                AND top_i_elastic <= {cutoff_sql}
            )::INTEGER) / max({cutoff_sql})::DOUBLE
                AS elastic_{overlap_suffix}_overlap,
            sum((
                top_b_gbrt <= {cutoff_sql}
                AND top_i_gbrt <= {cutoff_sql}
            )::INTEGER) / max({cutoff_sql})::DOUBLE
                AS gbrt_{overlap_suffix}_overlap
        FROM ranked
        GROUP BY date
        ORDER BY date
        """
    ).df()
    daily["quarter"] = pd.to_datetime(daily["date"]).dt.to_period("Q").astype(str)
    result: dict[str, Any] = {
        "joined_rows": int(daily["rows"].sum()),
        "days": int(len(daily)),
        "models": {},
    }
    if top_fraction is not None:
        result["overlap_definition"] = {
            "daily_top_fraction": top_fraction,
            "cutoff_rounding": "ceil",
        }
    for model in ("elastic", "gbrt"):
        spearman = f"{model}_spearman"
        overlap = f"{model}_{overlap_suffix}_overlap"
        quarterly = daily.groupby("quarter", sort=True)[[spearman, overlap]].mean()
        result["models"][model] = {
            "mean_daily_spearman": _finite(daily[spearman].mean()),
            "min_daily_spearman": _finite(daily[spearman].min()),
            f"mean_daily_{overlap_suffix}_overlap": _finite(
                daily[overlap].mean()
            ),
            f"min_daily_{overlap_suffix}_overlap": _finite(
                daily[overlap].min()
            ),
            "quarterly": {
                quarter: {
                    "mean_daily_spearman": _finite(row[spearman]),
                    f"mean_daily_{overlap_suffix}_overlap": _finite(
                        row[overlap]
                    ),
                }
                for quarter, row in quarterly.iterrows()
            },
        }
    return result


def build_diagnostics(args: argparse.Namespace) -> dict[str, Any]:
    repo_root = Path(__file__).resolve().parents[3]
    frozen_root = Path(args.frozen_panel_root)
    candidate_root = Path(args.candidate_panel_root)
    baseline_scores = Path(args.baseline_score_root)
    candidate_scores = Path(args.candidate_score_root)
    baseline_report_root = Path(args.baseline_report_root)
    candidate_report_root = Path(args.candidate_report_root)
    frozen_glob = _sql_path(frozen_root, filename="panel.parquet")
    panel_glob = _sql_path(candidate_root, filename="panel.parquet")
    baseline_glob = _sql_path(baseline_scores, filename="scores.parquet")
    candidate_score_glob = _sql_path(candidate_scores, filename="scores.parquet")
    connection = duckdb.connect()
    complete_matrix = _mean_daily_correlations(
        connection,
        panel_glob=panel_glob,
        start=args.start,
        end=args.end,
        left=NEW_FEATURES,
        right=MODEL_INPUT_FEATURES,
    )
    three_by_three = _mean_daily_correlations(
        connection,
        panel_glob=panel_glob,
        start=args.start,
        end=args.end,
        left=NEW_FEATURES,
        right=NEW_FEATURES,
    )
    value_quality_matrix = _mean_daily_correlations(
        connection,
        panel_glob=panel_glob,
        start=args.start,
        end=args.end,
        left=VALUE_QUALITY_FEATURES,
        right=VALUE_QUALITY_FEATURES,
    )
    matrix = np.array(
        [
            [value_quality_matrix[left][right] for right in VALUE_QUALITY_FEATURES]
            for left in VALUE_QUALITY_FEATURES
        ],
        dtype=float,
    )
    inverse = np.linalg.pinv(matrix)
    source_files = sorted(frozen_root.glob("year=*/panel.parquet"))
    payload = {
        "experiment": "20260716_industry_size_value_quality_race",
        "start": args.start,
        "end": args.end,
        "physical_read_cutoff": args.end,
        "label": "T+1 open through T+6 open endpoint return",
        "correlation_method": (
            "mean of daily cross-sectional Spearman correlations; rank_* inputs "
            "are already average-tie ranks and binary controls are affine to "
            "their average-tie ranks"
        ),
        "feature_schema": {
            "baseline": list(MODEL_INPUT_FEATURES),
            "candidate": list(INDUSTRY_SIZE_MODEL_INPUT_FEATURES),
            "new": list(NEW_FEATURES),
        },
        "frozen_input_fingerprints": [
            _file_fingerprint(path, repo_root) for path in source_files
        ],
        "identity": {
            "panel": _panel_identity(
                connection,
                frozen_glob=frozen_glob,
                candidate_glob=panel_glob,
            ),
            "scores": _score_identity(
                connection,
                baseline_glob=baseline_glob,
                candidate_glob=candidate_score_glob,
                start=args.start,
                end=args.end,
            ),
        },
        "coverage": _feature_coverage(
            connection,
            panel_glob=panel_glob,
            start=args.start,
            end=args.end,
        ),
        "new_by_all_existing_mean_daily_spearman": complete_matrix,
        "new_feature_mean_daily_spearman": three_by_three,
        "value_quality_six_feature_mean_daily_correlation": {
            "columns": list(VALUE_QUALITY_FEATURES),
            "matrix": matrix.tolist(),
            "condition_number": _finite(np.linalg.cond(matrix)),
            "vif_diagonal": {
                feature: _finite(inverse[index, index])
                for index, feature in enumerate(VALUE_QUALITY_FEATURES)
            },
        },
        "residual_orthogonality": _orthogonality(
            connection,
            panel_glob=panel_glob,
            start=args.start,
            end=args.end,
        ),
        "model_stability": _model_stability(
            connection,
            baseline_glob=baseline_glob,
            candidate_glob=candidate_score_glob,
            start=args.start,
            end=args.end,
        ),
        "gate_evaluation": _gate_evaluation(
            baseline_report_root=baseline_report_root,
            candidate_report_root=candidate_report_root,
        ),
    }
    connection.close()
    return payload


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--frozen-panel-root", required=True)
    parser.add_argument("--candidate-panel-root", required=True)
    parser.add_argument("--baseline-score-root", required=True)
    parser.add_argument("--candidate-score-root", required=True)
    parser.add_argument("--baseline-report-root", required=True)
    parser.add_argument("--candidate-report-root", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--start", default="2021-01-01")
    parser.add_argument("--end", default="2023-12-31")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    payload = build_diagnostics(args)
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(".tmp.json")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    temporary.replace(output)


if __name__ == "__main__":
    main()
