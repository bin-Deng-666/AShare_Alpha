"""Build diagnostics for the preregistered capital-gain overhang race."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import duckdb
import pandas as pd

from strategy.rotating.experiments.build_industry_size_diagnostics import (
    _file_fingerprint,
    _finite,
    _gate_evaluation,
    _model_stability,
    _sql_path,
)
from strategy.rotating.experiments.build_price_limit_diagnostics import (
    _portfolio_action_data_gates,
)
from strategy.rotating.features.build_panel import (
    CAPITAL_GAIN_OVERHANG_MODEL_INPUT_FEATURES,
    PRICE_LIMIT_MODEL_INPUT_FEATURES,
)
from strategy.rotating.pipeline import SCORE_SPEC_FILENAME


def _panel_identity(
    connection: duckdb.DuckDBPyConnection,
    *,
    baseline_glob: str,
    candidate_glob: str,
    start: str,
    end: str,
) -> dict[str, Any]:
    baseline_columns = [
        row[0]
        for row in connection.execute(
            f"DESCRIBE SELECT * FROM read_parquet('{baseline_glob}')"
        ).fetchall()
        if row[0] != "year"
    ]
    candidate_columns = {
        row[0]
        for row in connection.execute(
            f"DESCRIBE SELECT * FROM read_parquet('{candidate_glob}')"
        ).fetchall()
    }
    missing_columns = sorted(set(baseline_columns) - candidate_columns)
    comparisons = " OR ".join(
        f'b."{column}" IS DISTINCT FROM c."{column}"'
        for column in baseline_columns
        if column not in {"code", "date"} and column in candidate_columns
    )
    row = connection.execute(
        f"""
        WITH baseline AS (
            SELECT * FROM read_parquet('{baseline_glob}')
            WHERE date BETWEEN DATE '{start}' AND DATE '{end}'
        ), candidate AS (
            SELECT * FROM read_parquet('{candidate_glob}')
            WHERE date BETWEEN DATE '{start}' AND DATE '{end}'
        )
        SELECT
            count(b.code), count(c.code),
            count(*) FILTER (WHERE b.code IS NULL),
            count(*) FILTER (WHERE c.code IS NULL),
            count(*) FILTER (
                WHERE b.code IS NOT NULL AND c.code IS NOT NULL
                  AND ({comparisons})
            ),
            max(b.date), max(c.date)
        FROM baseline b
        FULL OUTER JOIN candidate c USING (code, date)
        """
    ).fetchone()
    return {
        "baseline_rows": int(row[0]),
        "candidate_rows": int(row[1]),
        "candidate_only_keys": int(row[2]),
        "baseline_only_keys": int(row[3]),
        "old_value_differences": int(row[4]),
        "baseline_max_date": str(row[5]) if row[5] is not None else None,
        "candidate_max_date": str(row[6]) if row[6] is not None else None,
        "baseline_columns": baseline_columns,
        "missing_baseline_columns": missing_columns,
    }


def _score_identity(
    connection: duckdb.DuckDBPyConnection,
    *,
    reference_glob: str,
    baseline_glob: str,
    candidate_glob: str,
) -> dict[str, Any]:
    reference_columns = [
        row[0]
        for row in connection.execute(
            f"DESCRIBE SELECT * FROM read_parquet('{reference_glob}')"
        ).fetchall()
        if row[0] != "year"
    ]
    exact_comparisons = " OR ".join(
        f'r."{column}" IS DISTINCT FROM b."{column}"'
        for column in reference_columns
        if column not in {"code", "date"}
    )
    reference_row = connection.execute(
        f"""
        WITH reference AS (SELECT * FROM read_parquet('{reference_glob}')),
        baseline AS (SELECT * FROM read_parquet('{baseline_glob}'))
        SELECT
            count(r.code), count(b.code),
            count(*) FILTER (WHERE r.code IS NULL),
            count(*) FILTER (WHERE b.code IS NULL),
            count(*) FILTER (
                WHERE r.code IS NOT NULL AND b.code IS NOT NULL
                  AND ({exact_comparisons})
            )
        FROM reference r
        FULL OUTER JOIN baseline b USING (code, date)
        """
    ).fetchone()
    candidate_row = connection.execute(
        f"""
        WITH baseline AS (SELECT * FROM read_parquet('{baseline_glob}')),
        candidate AS (SELECT * FROM read_parquet('{candidate_glob}'))
        SELECT
            count(b.code), count(c.code),
            count(*) FILTER (WHERE b.code IS NULL),
            count(*) FILTER (WHERE c.code IS NULL),
            count(*) FILTER (
                WHERE b.target_5 IS DISTINCT FROM c.target_5
                   OR b.target_rank IS DISTINCT FROM c.target_rank
                   OR b.label_available_date IS DISTINCT FROM c.label_available_date
                   OR b.factor_score IS DISTINCT FROM c.factor_score
            ),
            count(*) FILTER (WHERE b.elastic_score IS DISTINCT FROM c.elastic_score),
            count(*) FILTER (WHERE b.gbrt_score IS DISTINCT FROM c.gbrt_score),
            count(*) FILTER (WHERE b.train_max_label_available_date > b.date),
            count(*) FILTER (WHERE c.train_max_label_available_date > c.date),
            max(b.date), max(c.date),
            max(b.label_available_date), max(c.label_available_date)
        FROM baseline b
        FULL OUTER JOIN candidate c USING (code, date)
        """
    ).fetchone()
    return {
        "reference_baseline": {
            "reference_rows": int(reference_row[0]),
            "baseline_rows": int(reference_row[1]),
            "baseline_only_keys": int(reference_row[2]),
            "reference_only_keys": int(reference_row[3]),
            "value_differences": int(reference_row[4]),
        },
        "baseline_candidate": {
            "baseline_rows": int(candidate_row[0]),
            "candidate_rows": int(candidate_row[1]),
            "candidate_only_keys": int(candidate_row[2]),
            "baseline_only_keys": int(candidate_row[3]),
            "economic_target_date_factor_differences": int(candidate_row[4]),
            "elastic_score_differences": int(candidate_row[5]),
            "gbrt_score_differences": int(candidate_row[6]),
            "baseline_training_label_leaks": int(candidate_row[7]),
            "candidate_training_label_leaks": int(candidate_row[8]),
            "baseline_max_score_date": str(candidate_row[9]),
            "candidate_max_score_date": str(candidate_row[10]),
            "baseline_max_label_available_date": str(candidate_row[11]),
            "candidate_max_label_available_date": str(candidate_row[12]),
        },
    }


def _score_specifications(
    *, baseline_root: Path, candidate_root: Path
) -> dict[str, Any]:
    baseline = json.loads(
        (baseline_root / SCORE_SPEC_FILENAME).read_text(encoding="utf-8")
    )
    candidate = json.loads(
        (candidate_root / SCORE_SPEC_FILENAME).read_text(encoding="utf-8")
    )
    other_keys = sorted((set(baseline) | set(candidate)) - {"feature_columns"})
    baseline_features = baseline.get("feature_columns", [])
    candidate_features = candidate.get("feature_columns", [])
    added = candidate_features[len(baseline_features) :]
    return {
        "baseline": baseline,
        "candidate": candidate,
        "baseline_is_frozen_price_limit": (
            tuple(baseline.get("feature_columns", ()))
            == PRICE_LIMIT_MODEL_INPUT_FEATURES
        ),
        "candidate_is_registered_overhang": (
            tuple(candidate.get("feature_columns", ()))
            == CAPITAL_GAIN_OVERHANG_MODEL_INPUT_FEATURES
        ),
        "baseline_is_candidate_prefix": (
            candidate_features[: len(baseline_features)] == baseline_features
        ),
        "added_features": added,
        "non_feature_fields_identical": all(
            baseline.get(key) == candidate.get(key) for key in other_keys
        ),
    }


def _feature_diagnostics(
    connection: duckdb.DuckDBPyConnection,
    *,
    panel_glob: str,
    start: str,
    end: str,
) -> dict[str, Any]:
    yearly = connection.execute(
        f"""
        SELECT
            year(date) AS year,
            count(*) FILTER (WHERE eligible) AS eligible_rows,
            count(capital_gain_overhang_3y) FILTER (WHERE eligible) AS cgo_rows,
            count(overhang_contrarian_5) FILTER (WHERE eligible) AS contrarian_rows,
            count(*) FILTER (
                WHERE eligible AND capital_gain_overhang_3y > 0
            ) AS cgo_positive,
            count(*) FILTER (
                WHERE eligible AND capital_gain_overhang_3y < 0
            ) AS cgo_negative,
            count(*) FILTER (
                WHERE eligible AND overhang_contrarian_5 > 0
            ) AS contrarian_positive,
            count(*) FILTER (
                WHERE eligible AND overhang_contrarian_5 < 0
            ) AS contrarian_negative,
            count(*) FILTER (
                WHERE eligible AND overhang_contrarian_5 = 0
            ) AS contrarian_zero,
            max(turnover_above_100_count_3y) FILTER (WHERE eligible) AS max_over_100,
            sum(turnover_above_100_count_3y) FILTER (
                WHERE eligible
            ) AS summed_window_counts,
            count(*) FILTER (
                WHERE eligible AND (
                    (capital_gain_overhang_3y IS NULL)
                    <> (capital_gain_overhang_3y_missing = 1)
                    OR (overhang_contrarian_5 IS NULL)
                    <> (overhang_contrarian_5_missing = 1)
                    OR (capital_gain_overhang_3y IS NULL)
                    <> (rank_capital_gain_overhang_3y IS NULL)
                    OR (overhang_contrarian_5 IS NULL)
                    <> (rank_overhang_contrarian_5 IS NULL)
                )
            ) AS missing_identity_differences
        FROM read_parquet('{panel_glob}')
        WHERE date BETWEEN DATE '{start}' AND DATE '{end}'
        GROUP BY year(date)
        ORDER BY year(date)
        """
    ).df().to_dict(orient="records")
    reason_rows = connection.execute(
        f"""
        SELECT capital_gain_overhang_3y_missing_reason AS reason, count(*) AS rows
        FROM read_parquet('{panel_glob}')
        WHERE eligible AND date BETWEEN DATE '{start}' AND DATE '{end}'
        GROUP BY reason ORDER BY rows DESC
        """
    ).fetchall()
    new_inputs = list(CAPITAL_GAIN_OVERHANG_MODEL_INPUT_FEATURES[-4:])
    existing_inputs = list(PRICE_LIMIT_MODEL_INPUT_FEATURES)
    pairs = [
        (new_feature, existing_feature)
        for new_feature in new_inputs
        for existing_feature in existing_inputs
    ]
    expressions = ", ".join(
        f'corr("{left}", "{right}") AS "{left}__{right}"'
        for left, right in pairs
    )
    correlation_row = connection.execute(
        f"""
        SELECT {expressions}
        FROM read_parquet('{panel_glob}')
        WHERE eligible AND date BETWEEN DATE '{start}' AND DATE '{end}'
        """
    ).fetchone()
    correlations = {
        f"{left}__{right}": _finite(value)
        for (left, right), value in zip(pairs, correlation_row, strict=True)
    }
    internal_pairs = [
        (left, right)
        for index, left in enumerate(new_inputs)
        for right in new_inputs[index:]
    ]
    internal_expressions = ", ".join(
        f'corr("{left}", "{right}") AS "{left}__{right}"'
        for left, right in internal_pairs
    )
    internal_row = connection.execute(
        f"""
        SELECT {internal_expressions}
        FROM read_parquet('{panel_glob}')
        WHERE eligible AND date BETWEEN DATE '{start}' AND DATE '{end}'
        """
    ).fetchone()
    internal_correlations = {
        f"{left}__{right}": _finite(value)
        for (left, right), value in zip(
            internal_pairs, internal_row, strict=True
        )
    }
    return {
        "yearly": yearly,
        "missing_reasons": {reason: int(rows) for reason, rows in reason_rows},
        "new_vs_existing_correlations": correlations,
        "internal_correlations": internal_correlations,
    }


def _nontrading_status_audit(
    connection: duckdb.DuckDBPyConnection,
    *,
    panel_glob: str,
    start: str,
    end: str,
) -> dict[str, int]:
    row = connection.execute(
        f"""
        SELECT
            count(*) FILTER (WHERE trade_status IS NULL),
            count(*) FILTER (
                WHERE trade_status = FALSE AND amount = 0 AND volume = 0
            ),
            count(*) FILTER (
                WHERE trade_status = FALSE
                  AND (coalesce(amount, 0) > 0 OR coalesce(volume, 0) > 0)
            ),
            count(*) FILTER (
                WHERE trade_status = FALSE
                  AND NOT (coalesce(amount, 0) > 0 OR coalesce(volume, 0) > 0)
                  AND NOT coalesce(amount = 0 AND volume = 0, FALSE)
            ),
            count(*) FILTER (
                WHERE trade_status = FALSE
                  AND (coalesce(amount, 0) > 0 OR coalesce(volume, 0) > 0)
                  AND (
                      capital_gain_overhang_3y IS NOT NULL
                      OR overhang_contrarian_5 IS NOT NULL
                  )
            )
        FROM read_parquet('{panel_glob}')
        WHERE date BETWEEN DATE '{start}' AND DATE '{end}'
        """
    ).fetchone()
    return {
        "unknown_trade_status_rows": int(row[0]),
        "verified_zero_activity_suspension_rows": int(row[1]),
        "contradictory_nontrading_rows": int(row[2]),
        "unverified_nontrading_rows": int(row[3]),
        "contradictory_nontrading_rows_with_nonnull_signal": int(row[4]),
    }


def _history_partition_audit(
    connection: duckdb.DuckDBPyConnection,
    *,
    root: Path,
    start_year: int,
    end_year: int,
) -> dict[str, Any]:
    expected_years = list(range(start_year, end_year + 1))
    partitions: list[dict[str, Any]] = []
    missing_years: list[int] = []
    for year in expected_years:
        path = root / f"year={year}" / "panel.parquet"
        if not path.is_file():
            missing_years.append(year)
            continue
        escaped = path.as_posix().replace("'", "''")
        row = connection.execute(
            "SELECT count(*), CAST(min(date) AS DATE), CAST(max(date) AS DATE) "
            f"FROM read_parquet('{escaped}')"
        ).fetchone()
        partitions.append(
            {
                "year": year,
                "rows": int(row[0]),
                "min_date": str(row[1]) if row[1] is not None else None,
                "max_date": str(row[2]) if row[2] is not None else None,
            }
        )
    return {
        "expected_years": expected_years,
        "present_years": [item["year"] for item in partitions],
        "missing_years": missing_years,
        "partitions": partitions,
    }


def _action_audit(
    baseline_performance: dict[str, Any], candidate_performance: dict[str, Any]
) -> dict[str, dict[str, dict[str, Any]]]:
    return {
        variant: {
            scenario: performance["scenarios"][scenario]["admission_diagnostics"]
            for scenario in ("gbrt_historical", "gbrt_flat_20bp_roundtrip")
        }
        for variant, performance in (
            ("baseline", baseline_performance),
            ("overhang", candidate_performance),
        )
    }


def build_diagnostics(args: argparse.Namespace) -> dict[str, Any]:
    repo_root = Path(__file__).resolve().parents[3]
    baseline_panel = Path(args.baseline_panel_root)
    history_panel = Path(args.history_panel_root)
    candidate_panel = Path(args.candidate_panel_root)
    reference_scores = Path(args.reference_score_root)
    baseline_scores = Path(args.baseline_score_root)
    candidate_scores = Path(args.candidate_score_root)
    baseline_report = Path(args.baseline_report_root)
    candidate_report = Path(args.candidate_report_root)
    baseline_panel_glob = _sql_path(baseline_panel, filename="panel.parquet")
    candidate_panel_glob = _sql_path(candidate_panel, filename="panel.parquet")
    reference_score_glob = _sql_path(reference_scores, filename="scores.parquet")
    baseline_score_glob = _sql_path(baseline_scores, filename="scores.parquet")
    candidate_score_glob = _sql_path(candidate_scores, filename="scores.parquet")

    connection = duckdb.connect()
    panel_identity = _panel_identity(
        connection,
        baseline_glob=baseline_panel_glob,
        candidate_glob=candidate_panel_glob,
        start=args.panel_start,
        end=args.end,
    )
    score_identity = _score_identity(
        connection,
        reference_glob=reference_score_glob,
        baseline_glob=baseline_score_glob,
        candidate_glob=candidate_score_glob,
    )
    feature_diagnostics = _feature_diagnostics(
        connection,
        panel_glob=candidate_panel_glob,
        start=args.start,
        end=args.end,
    )
    nontrading_status_audit = _nontrading_status_audit(
        connection,
        panel_glob=candidate_panel_glob,
        start=args.panel_start,
        end=args.end,
    )
    history_partitions = _history_partition_audit(
        connection,
        root=history_panel,
        start_year=pd.Timestamp(args.panel_start).year - 3,
        end_year=pd.Timestamp(args.end).year,
    )
    model_stability = _model_stability(
        connection,
        baseline_glob=baseline_score_glob,
        candidate_glob=candidate_score_glob,
        start=args.start,
        end=args.end,
        top_fraction=0.25,
    )
    connection.close()

    specifications = _score_specifications(
        baseline_root=baseline_scores, candidate_root=candidate_scores
    )
    baseline_performance = json.loads(
        (baseline_report / "PERFORMANCE.json").read_text(encoding="utf-8")
    )
    candidate_performance = json.loads(
        (candidate_report / "PERFORMANCE.json").read_text(encoding="utf-8")
    )
    action_audit = _action_audit(baseline_performance, candidate_performance)
    frozen_price_limit = json.loads(
        Path(args.price_limit_diagnostics).read_text(encoding="utf-8")
    )
    reference = score_identity["reference_baseline"]
    comparison = score_identity["baseline_candidate"]
    years = feature_diagnostics["yearly"]
    data_gates = {
        "panel_old_columns_are_identical": (
            not panel_identity["missing_baseline_columns"]
            and panel_identity["baseline_rows"] == panel_identity["candidate_rows"]
            and panel_identity["candidate_only_keys"] == 0
            and panel_identity["baseline_only_keys"] == 0
            and panel_identity["old_value_differences"] == 0
        ),
        "rebuilt_baseline_scores_match_v4": (
            reference["reference_rows"] == reference["baseline_rows"]
            and reference["baseline_only_keys"] == 0
            and reference["reference_only_keys"] == 0
            and reference["value_differences"] == 0
        ),
        "baseline_and_candidate_scores_share_economic_identity": (
            comparison["baseline_rows"] == comparison["candidate_rows"]
            and comparison["candidate_only_keys"] == 0
            and comparison["baseline_only_keys"] == 0
            and comparison["economic_target_date_factor_differences"] == 0
            and comparison["elastic_score_differences"] > 0
            and comparison["gbrt_score_differences"] > 0
        ),
        "training_is_leakage_free": (
            comparison["baseline_training_label_leaks"] == 0
            and comparison["candidate_training_label_leaks"] == 0
        ),
        "public_cutoff_is_respected": all(
            value is not None and value <= args.end
            for value in (
                panel_identity["baseline_max_date"],
                panel_identity["candidate_max_date"],
                comparison["baseline_max_score_date"],
                comparison["candidate_max_score_date"],
                comparison["baseline_max_label_available_date"],
                comparison["candidate_max_label_available_date"],
            )
        ),
        "score_specification_has_only_registered_additions": (
            specifications["baseline_is_frozen_price_limit"]
            and specifications["candidate_is_registered_overhang"]
            and specifications["baseline_is_candidate_prefix"]
            and specifications["non_feature_fields_identical"]
        ),
        "features_have_complete_yearly_diagnostics": (
            {int(row["year"]) for row in years} == {2021, 2022, 2023}
            and all(
                row["eligible_rows"] > 0
                and row["cgo_rows"] > 0
                and row["contrarian_rows"] > 0
                and row["cgo_positive"] > 0
                and row["cgo_negative"] > 0
                and row["contrarian_positive"] > 0
                and row["contrarian_negative"] > 0
                and row["missing_identity_differences"] == 0
                for row in years
            )
        ),
        "feature_missing_reasons_are_complete": (
            sum(feature_diagnostics["missing_reasons"].values())
            == sum(int(row["eligible_rows"]) for row in years)
        ),
        "history_source_partitions_are_complete": (
            not history_partitions["missing_years"]
            and history_partitions["present_years"]
            == history_partitions["expected_years"]
            and all(item["rows"] > 0 for item in history_partitions["partitions"])
        ),
        "history_source_dates_respect_public_cutoff": all(
            item["max_date"] is not None and item["max_date"] <= args.end
            for item in history_partitions["partitions"]
        ),
        "contradictory_nontrading_rows_never_receive_signal": (
            nontrading_status_audit[
                "contradictory_nontrading_rows_with_nonnull_signal"
            ]
            == 0
        ),
        "frozen_price_limit_data_gates_remain_passed": all(
            frozen_price_limit["data_gates"].values()
        ),
        **_portfolio_action_data_gates(action_audit),
    }
    performance_gates = _gate_evaluation(
        baseline_report_root=baseline_report,
        candidate_report_root=candidate_report,
    )
    all_gates = all(data_gates.values()) and performance_gates["all_gates_passed"]
    performance_gates["all_gates_passed"] = all_gates
    performance_gates["experiment_decision"] = (
        "development_winner_pending_fresh_forward_validation"
        if all_gates
        else "rejected_on_development"
    )
    return {
        "experiment": "20260717_capital_gain_overhang_feature_race",
        "start": args.start,
        "end": args.end,
        "physical_read_cutoff": args.end,
        "panel_fingerprints": [
            _file_fingerprint(path, repo_root)
            for path in sorted(candidate_panel.glob("year=*/panel.parquet"))
        ],
        "panel_identity": panel_identity,
        "score_identity": score_identity,
        "score_specifications": specifications,
        "feature_diagnostics": feature_diagnostics,
        "history_source_partition_audit": history_partitions,
        "nontrading_status_audit": nontrading_status_audit,
        "model_stability": model_stability,
        "portfolio_action_execution_audit": action_audit,
        "frozen_price_limit_data_gate_summary": frozen_price_limit["data_gates"],
        "data_gates": data_gates,
        "gate_evaluation": performance_gates,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline-panel-root", required=True)
    parser.add_argument("--history-panel-root", required=True)
    parser.add_argument("--candidate-panel-root", required=True)
    parser.add_argument("--reference-score-root", required=True)
    parser.add_argument("--baseline-score-root", required=True)
    parser.add_argument("--candidate-score-root", required=True)
    parser.add_argument("--baseline-report-root", required=True)
    parser.add_argument("--candidate-report-root", required=True)
    parser.add_argument("--price-limit-diagnostics", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--panel-start", default="2018-01-01")
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
