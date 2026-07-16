"""Build diagnostics for the preregistered five-day path-label race."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import duckdb

from strategy.rotating.experiments.build_industry_size_diagnostics import (
    _file_fingerprint,
    _gate_evaluation,
    _model_stability,
    _sql_path,
)
from strategy.rotating.experiments.build_price_limit_diagnostics import (
    _portfolio_action_data_gates,
)
from strategy.rotating.features.build_panel import (
    ENDPOINT_RETURN_COLUMN,
    ENDPOINT_TARGET_COLUMN,
    PATH_TARGET_COLUMN,
    PRICE_LIMIT_MODEL_INPUT_FEATURES,
)
from strategy.rotating.pipeline import SCORE_SPEC_FILENAME


def _label_score_identity(
    connection: duckdb.DuckDBPyConnection,
    *,
    panel_glob: str,
    baseline_glob: str,
    candidate_glob: str,
    start: str,
    end: str,
) -> dict[str, Any]:
    """Compare score keys/economic targets while allowing model targets to differ."""
    row = connection.execute(
        f"""
        WITH panel AS (
            SELECT code, date, {PATH_TARGET_COLUMN}
            FROM read_parquet('{panel_glob}')
            WHERE date BETWEEN DATE '{start}' AND DATE '{end}'
        ), baseline AS (
            SELECT * FROM read_parquet('{baseline_glob}')
            WHERE date BETWEEN DATE '{start}' AND DATE '{end}'
        ), candidate AS (
            SELECT * FROM read_parquet('{candidate_glob}')
            WHERE date BETWEEN DATE '{start}' AND DATE '{end}'
        ), joined AS (
            SELECT b.*, c.code AS c_code, c.date AS c_date,
                   c.{ENDPOINT_RETURN_COLUMN} AS c_endpoint_return,
                   c.{ENDPOINT_TARGET_COLUMN} AS c_endpoint_target,
                   c.label_available_date AS c_label_available_date,
                   c.factor_score AS c_factor_score,
                   c.model_target_rank AS c_model_target_rank,
                   c.train_max_label_available_date AS c_train_max_label_date,
                   p.{PATH_TARGET_COLUMN} AS panel_path_target
            FROM baseline b
            FULL OUTER JOIN candidate c
              ON b.code = c.code AND b.date = c.date
            LEFT JOIN panel p
              ON p.code = coalesce(b.code, c.code)
             AND p.date = coalesce(b.date, c.date)
        )
        SELECT
            count(*) FILTER (WHERE code IS NOT NULL),
            count(*) FILTER (WHERE c_code IS NOT NULL),
            count(*) FILTER (WHERE code IS NULL),
            count(*) FILTER (WHERE c_code IS NULL),
            count(*) FILTER (
                WHERE {ENDPOINT_RETURN_COLUMN} IS DISTINCT FROM c_endpoint_return
            ),
            count(*) FILTER (
                WHERE {ENDPOINT_TARGET_COLUMN} IS DISTINCT FROM c_endpoint_target
            ),
            count(*) FILTER (
                WHERE label_available_date IS DISTINCT FROM c_label_available_date
            ),
            count(*) FILTER (
                WHERE factor_score IS DISTINCT FROM c_factor_score
            ),
            count(*) FILTER (
                WHERE model_target_rank IS DISTINCT FROM {ENDPOINT_TARGET_COLUMN}
            ),
            count(*) FILTER (
                WHERE c_model_target_rank IS DISTINCT FROM panel_path_target
            ),
            count(*) FILTER (
                WHERE code IS NOT NULL AND c_code IS NOT NULL
                  AND model_target_rank IS DISTINCT FROM c_model_target_rank
            ),
            count(*) FILTER (
                WHERE train_max_label_available_date > date
            ),
            count(*) FILTER (
                WHERE c_train_max_label_date > c_date
            ),
            max(date), max(c_date),
            max(label_available_date), max(c_label_available_date)
        FROM joined
        """
    ).fetchone()
    names = (
        "baseline_rows",
        "candidate_rows",
        "candidate_only_keys",
        "baseline_only_keys",
        "actual_target_differences",
        "endpoint_rank_differences",
        "label_available_date_differences",
        "factor_score_differences",
        "baseline_model_target_mismatches",
        "candidate_model_target_mismatches",
        "different_model_target_rows",
        "baseline_training_label_leaks",
        "candidate_training_label_leaks",
        "baseline_max_score_date",
        "candidate_max_score_date",
        "baseline_max_label_available_date",
        "candidate_max_label_available_date",
    )
    result: dict[str, Any] = {}
    for name, value in zip(names, row, strict=True):
        if name.endswith("_date"):
            result[name] = str(value) if value is not None else None
        else:
            result[name] = int(value)
    return result


def _label_coverage(
    connection: duckdb.DuckDBPyConnection,
    *,
    panel_glob: str,
    start: str,
    end: str,
) -> dict[str, Any]:
    row = connection.execute(
        f"""
        SELECT
            count(*) FILTER (WHERE eligible),
            count(*) FILTER (WHERE eligible AND {ENDPOINT_TARGET_COLUMN} IS NOT NULL),
            count(*) FILTER (WHERE eligible AND {PATH_TARGET_COLUMN} IS NOT NULL),
            count(*) FILTER (
                WHERE eligible AND (
                    ({ENDPOINT_TARGET_COLUMN} IS NULL)
                    <> ({PATH_TARGET_COLUMN} IS NULL)
                )
            ),
            corr({ENDPOINT_TARGET_COLUMN}, {PATH_TARGET_COLUMN}) FILTER (
                WHERE eligible
            )
        FROM read_parquet('{panel_glob}')
        WHERE date BETWEEN DATE '{start}' AND DATE '{end}'
        """
    ).fetchone()
    return {
        "eligible_rows": int(row[0]),
        "endpoint_target_rows": int(row[1]),
        "path_target_rows": int(row[2]),
        "target_missingness_differences": int(row[3]),
        "endpoint_path_target_correlation": (
            float(row[4]) if row[4] is not None else None
        ),
    }


def _score_specification_identity(
    baseline_root: Path, candidate_root: Path
) -> dict[str, Any]:
    baseline = json.loads(
        (baseline_root / SCORE_SPEC_FILENAME).read_text(encoding="utf-8")
    )
    candidate = json.loads(
        (candidate_root / SCORE_SPEC_FILENAME).read_text(encoding="utf-8")
    )
    non_target_keys = sorted((set(baseline) | set(candidate)) - {"target_column"})
    return {
        "baseline": baseline,
        "candidate": candidate,
        "baseline_target_is_endpoint": (
            baseline.get("target_column") == ENDPOINT_TARGET_COLUMN
        ),
        "candidate_target_is_path": (
            candidate.get("target_column") == PATH_TARGET_COLUMN
        ),
        "non_target_fields_identical": all(
            baseline.get(key) == candidate.get(key) for key in non_target_keys
        ),
        "feature_schema_is_frozen_price_limit": (
            tuple(candidate.get("feature_columns", ()))
            == PRICE_LIMIT_MODEL_INPUT_FEATURES
        ),
    }


def _action_execution_audit(
    *, baseline_performance: dict[str, Any], candidate_performance: dict[str, Any]
) -> dict[str, dict[str, dict[str, Any]]]:
    return {
        variant: {
            scenario: performance["scenarios"][scenario][
                "admission_diagnostics"
            ]
            for scenario in (
                "gbrt_historical",
                "gbrt_flat_20bp_roundtrip",
            )
        }
        for variant, performance in (
            ("endpoint", baseline_performance),
            ("path", candidate_performance),
        )
    }


def build_diagnostics(args: argparse.Namespace) -> dict[str, Any]:
    repo_root = Path(__file__).resolve().parents[3]
    panel_root = Path(args.panel_root)
    baseline_scores = Path(args.baseline_score_root)
    candidate_scores = Path(args.candidate_score_root)
    baseline_report = Path(args.baseline_report_root)
    candidate_report = Path(args.candidate_report_root)
    panel_glob = _sql_path(panel_root, filename="panel.parquet")
    baseline_glob = _sql_path(baseline_scores, filename="scores.parquet")
    candidate_glob = _sql_path(candidate_scores, filename="scores.parquet")
    connection = duckdb.connect()
    identity = _label_score_identity(
        connection,
        panel_glob=panel_glob,
        baseline_glob=baseline_glob,
        candidate_glob=candidate_glob,
        start=args.start,
        end=args.end,
    )
    coverage = _label_coverage(
        connection,
        panel_glob=panel_glob,
        start=args.start,
        end=args.end,
    )
    model_stability = _model_stability(
        connection,
        baseline_glob=baseline_glob,
        candidate_glob=candidate_glob,
        start=args.start,
        end=args.end,
        top_fraction=0.25,
    )
    connection.close()
    specifications = _score_specification_identity(
        baseline_scores, candidate_scores
    )
    baseline_performance = json.loads(
        (baseline_report / "PERFORMANCE.json").read_text(encoding="utf-8")
    )
    candidate_performance = json.loads(
        (candidate_report / "PERFORMANCE.json").read_text(encoding="utf-8")
    )
    action_audit = _action_execution_audit(
        baseline_performance=baseline_performance,
        candidate_performance=candidate_performance,
    )
    frozen_price_limit_diagnostics = json.loads(
        Path(args.price_limit_diagnostics).read_text(encoding="utf-8")
    )
    identity_fields = (
        "candidate_only_keys",
        "baseline_only_keys",
        "actual_target_differences",
        "endpoint_rank_differences",
        "label_available_date_differences",
        "factor_score_differences",
        "baseline_model_target_mismatches",
        "candidate_model_target_mismatches",
        "baseline_training_label_leaks",
        "candidate_training_label_leaks",
    )
    data_gates = {
        "same_score_rows_and_keys": (
            identity["baseline_rows"] == identity["candidate_rows"]
            and identity["baseline_rows"] > 0
            and identity["candidate_only_keys"] == 0
            and identity["baseline_only_keys"] == 0
        ),
        "economic_targets_dates_and_factor_are_identical": all(
            identity[field] == 0 for field in identity_fields[2:6]
        ),
        "model_targets_match_their_registered_labels": (
            identity["baseline_model_target_mismatches"] == 0
            and identity["candidate_model_target_mismatches"] == 0
            and identity["different_model_target_rows"] > 0
        ),
        "training_labels_are_available_before_prediction": (
            identity["baseline_training_label_leaks"] == 0
            and identity["candidate_training_label_leaks"] == 0
        ),
        "score_and_label_dates_respect_public_cutoff": all(
            identity[field] is not None and identity[field] <= args.end
            for field in (
                "baseline_max_score_date",
                "candidate_max_score_date",
                "baseline_max_label_available_date",
                "candidate_max_label_available_date",
            )
        ),
        "endpoint_and_path_use_the_same_complete_samples": (
            coverage["endpoint_target_rows"] == coverage["path_target_rows"]
            and coverage["endpoint_target_rows"] > 0
            and coverage["target_missingness_differences"] == 0
        ),
        "score_specifications_differ_only_by_registered_target": (
            specifications["baseline_target_is_endpoint"]
            and specifications["candidate_target_is_path"]
            and specifications["non_target_fields_identical"]
            and specifications["feature_schema_is_frozen_price_limit"]
        ),
        "frozen_price_limit_data_gates_remain_passed": all(
            frozen_price_limit_diagnostics["data_gates"].values()
        ),
        **_portfolio_action_data_gates(action_audit),
    }
    performance_gates = _gate_evaluation(
        baseline_report_root=baseline_report,
        candidate_report_root=candidate_report,
    )
    all_gates_passed = all(data_gates.values()) and performance_gates[
        "all_gates_passed"
    ]
    performance_gates["all_gates_passed"] = all_gates_passed
    performance_gates["experiment_decision"] = (
        "development_winner_pending_fresh_forward_validation"
        if all_gates_passed
        else "rejected_on_development"
    )
    return {
        "experiment": "20260717_price_limit_path_label_race",
        "start": args.start,
        "end": args.end,
        "physical_read_cutoff": args.end,
        "economic_label": "T+1 open through T+6 open endpoint return",
        "candidate_training_label": (
            "mean of T+2 through T+6 open returns relative to T+1 open"
        ),
        "panel_fingerprints": [
            _file_fingerprint(path, repo_root)
            for path in sorted(panel_root.glob("year=*/panel.parquet"))
        ],
        "score_specifications": specifications,
        "identity": identity,
        "label_coverage": coverage,
        "model_stability": model_stability,
        "portfolio_action_execution_audit": action_audit,
        "frozen_price_limit_data_gate_summary": (
            frozen_price_limit_diagnostics["data_gates"]
        ),
        "data_gates": data_gates,
        "gate_evaluation": performance_gates,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--panel-root", required=True)
    parser.add_argument("--baseline-score-root", required=True)
    parser.add_argument("--candidate-score-root", required=True)
    parser.add_argument("--baseline-report-root", required=True)
    parser.add_argument("--candidate-report-root", required=True)
    parser.add_argument("--price-limit-diagnostics", required=True)
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
