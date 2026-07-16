"""Build machine-readable diagnostics for the preregistered price-limit race."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import duckdb
import numpy as np
import pandas as pd

from data.scripts.corporate_action_status import (
    CORPORATE_ACTION_ASOF_SOURCE,
    CORPORATE_ACTION_ASOF_UNIVERSE_RULE,
    CorporateActionFormulaStatus,
    requires_conservative_invalidation,
    serialized_status_values_for,
)

from strategy.rotating.experiments.build_industry_size_diagnostics import (
    _file_fingerprint,
    _finite,
    _gate_evaluation,
    _model_stability,
    _panel_identity,
    _score_identity,
    _sql_path,
)
from strategy.rotating.features.build_panel import (
    MODEL_INPUT_FEATURES,
    PRICE_LIMIT_MODEL_FEATURES,
    PRICE_LIMIT_MODEL_INPUT_FEATURES,
)


NEW_FEATURES = PRICE_LIMIT_MODEL_FEATURES + ("limit_history_missing_5",)
EXPECTED_YEARS = (2021, 2022, 2023)
BOARD_SQL = """CASE
  WHEN code LIKE '688%' OR code LIKE '689%' THEN 'STAR'
  WHEN code LIKE '300%' OR code LIKE '301%' THEN 'CHINEXT'
  ELSE 'MAIN'
END"""


def _average_rank_expression(column: str) -> str:
    quoted = f'"{column}"'
    return (
        f"CASE WHEN {quoted} IS NOT NULL THEN "
        f"rank() OVER (PARTITION BY date ORDER BY {quoted}) + "
        f"(count(*) OVER (PARTITION BY date, {quoted}) - 1) / 2.0 END"
    )


def _mean_daily_spearman(
    connection: duckdb.DuckDBPyConnection,
    *,
    panel_glob: str,
    start: str,
    end: str,
    left: tuple[str, ...],
    right: tuple[str, ...],
) -> dict[str, dict[str, float | None]]:
    columns = tuple(dict.fromkeys((*left, *right)))
    aliases = {column: f"r{index}" for index, column in enumerate(columns)}
    rank_sql = ", ".join(
        f'{_average_rank_expression(column)} AS "{aliases[column]}"'
        for column in columns
    )
    pairs: list[tuple[str, str, str]] = []
    correlation_sql: list[str] = []
    for left_name in left:
        for right_name in right:
            alias = f"c{len(pairs)}"
            pairs.append((left_name, right_name, alias))
            correlation_sql.append(
                f'corr("{aliases[left_name]}", "{aliases[right_name]}") AS {alias}'
            )
    daily = connection.execute(
        f"""
        WITH ranked AS (
            SELECT date, {rank_sql}
            FROM read_parquet('{panel_glob}')
            WHERE eligible
              AND date >= DATE '{start}' AND date <= DATE '{end}'
        )
        SELECT date, {', '.join(correlation_sql)}
        FROM ranked
        GROUP BY date
        ORDER BY date
        """
    ).df()
    result: dict[str, dict[str, float | None]] = {name: {} for name in left}
    for left_name, right_name, alias in pairs:
        result[left_name][right_name] = _finite(daily[alias].mean())
    return result


def _coverage_and_events(
    connection: duckdb.DuckDBPyConnection,
    *,
    panel_glob: str,
    start: str,
    end: str,
) -> dict[str, Any]:
    feature_sql = ", ".join(
        f"count(*) FILTER (WHERE \"{feature}\" = 0) AS {feature}_zero, "
        f"count(*) FILTER (WHERE \"{feature}\" > 0) AS {feature}_nonzero"
        for feature in PRICE_LIMIT_MODEL_FEATURES
    )
    complete_window_sql = " AND ".join(
        f'"{feature}" IS NOT NULL' for feature in PRICE_LIMIT_MODEL_FEATURES
    )
    yearly = connection.execute(
        f"""
        SELECT
            year(date) AS year,
            count(*) AS eligible_rows,
            count(*) FILTER (
                WHERE limit_history_missing_5 = 0
                  AND {complete_window_sql}
            ) AS valid_windows,
            {feature_sql}
        FROM read_parquet('{panel_glob}')
        WHERE eligible AND date >= DATE '{start}' AND date <= DATE '{end}'
        GROUP BY 1 ORDER BY 1
        """
    ).df()
    years: dict[str, Any] = {}
    for _, row in yearly.iterrows():
        eligible_rows = int(row["eligible_rows"])
        years[str(int(row["year"]))] = {
            "eligible_rows": eligible_rows,
            "valid_windows": int(row["valid_windows"]),
            "coverage": (
                float(row["valid_windows"] / eligible_rows)
                if eligible_rows
                else None
            ),
            "features": {
                feature: {
                    "zero_rows": int(row[f"{feature}_zero"]),
                    "nonzero_rows": int(row[f"{feature}_nonzero"]),
                }
                for feature in PRICE_LIMIT_MODEL_FEATURES
            },
        }
    total_rows = sum(item["eligible_rows"] for item in years.values())
    valid_rows = sum(item["valid_windows"] for item in years.values())
    nonzero_count_sql = " + ".join(
        f'count(*) FILTER (WHERE "{feature}" > 0)'
        for feature in PRICE_LIMIT_MODEL_FEATURES
    )
    daily_nonzero = connection.execute(
        f"""
        SELECT date,
               {nonzero_count_sql} AS nonzero_feature_rows
        FROM read_parquet('{panel_glob}')
        WHERE eligible AND date >= DATE '{start}' AND date <= DATE '{end}'
        GROUP BY date ORDER BY date
        """
    ).df()
    return {
        "eligible_rows": total_rows,
        "valid_windows": valid_rows,
        "coverage": valid_rows / total_rows if total_rows else None,
        "years": years,
        "daily_nonzero_feature_rows": {
            "min": int(daily_nonzero["nonzero_feature_rows"].min()),
            "median": _finite(daily_nonzero["nonzero_feature_rows"].median()),
            "max": int(daily_nonzero["nonzero_feature_rows"].max()),
        },
    }


def _data_validation(
    connection: duckdb.DuckDBPyConnection,
    *,
    baseline_glob: str,
    legacy_glob: str | None,
    start: str,
    end: str,
) -> dict[str, Any]:
    row = connection.execute(
        f"""
        SELECT
            count(*) FILTER (
                WHERE limit_validation_status = 'valid'
                  AND ((round(raw_high, 2) > round(limit_up, 2))
                    OR (round(raw_low, 2) < round(limit_down, 2)))
            ) AS valid_boundary_violations,
            count(*) FILTER (
                WHERE limit_validation_status = 'valid'
                  AND (is_limit_up AND round(raw_high, 2) <> round(limit_up, 2)
                    OR is_limit_down AND round(raw_low, 2) <> round(limit_down, 2))
            ) AS close_without_touch,
            count(*) FILTER (
                WHERE limit_validation_status = 'raw_ohlc_outside_limit'
            ) AS invalid_boundary_rows,
            count(*) FILTER (
                WHERE limit_reference_source = 'corporate_actions_ex_right'
            ) AS corporate_action_reference_rows,
            count(*) FILTER (
                WHERE target_5 IS NOT NULL
                  AND label_available_date > DATE '{end}'
            ) AS labels_realized_after_cutoff,
            count(*) FILTER (
                WHERE label_path_has_unsupported_action
            ) AS labels_crossing_unsupported_action,
            count(*) FILTER (
                WHERE label_path_has_unsupported_action
                  AND target_5 IS NOT NULL
            ) AS unsupported_action_labels_not_invalidated
        FROM read_parquet('{baseline_glob}')
        WHERE eligible AND date >= DATE '{start}' AND date <= DATE '{end}'
        """
    ).fetchone()
    result = {
        "valid_boundary_violations": int(row[0]),
        "close_without_touch": int(row[1]),
        "invalid_boundary_rows": int(row[2]),
        "corporate_action_reference_rows": int(row[3]),
        "labels_realized_after_cutoff": int(row[4]),
        "labels_crossing_unsupported_action": int(row[5]),
        "unsupported_action_labels_not_invalidated": int(row[6]),
        "limit_validation_status": {
            str(status): int(count)
            for status, count in connection.execute(
                f"""
                SELECT coalesce(limit_validation_status, 'NULL'), count(*)
                FROM read_parquet('{baseline_glob}')
                WHERE eligible AND date >= DATE '{start}' AND date <= DATE '{end}'
                GROUP BY 1 ORDER BY 1
                """
            ).fetchall()
        },
        "reference_sources": {
            str(source): int(count)
            for source, count in connection.execute(
                f"""
                SELECT coalesce(limit_reference_source, 'NULL'), count(*)
                FROM read_parquet('{baseline_glob}')
                WHERE eligible AND date >= DATE '{start}' AND date <= DATE '{end}'
                GROUP BY 1 ORDER BY 1
                """
            ).fetchall()
        },
    }
    rights_status_values = ", ".join(
        f"'{value}'"
        for value in serialized_status_values_for(
            CorporateActionFormulaStatus.RIGHTS_ISSUE_PRICE_ONLY
        )
    )
    rights_price_rows, rights_price_failures = connection.execute(
        f"""
        SELECT
            count(*),
            count(*) FILTER (
                WHERE limit_validation_status <> 'valid'
                   OR limit_up IS NULL
                   OR limit_down IS NULL
                   OR limit_reference_source <> 'corporate_actions_ex_right'
            )
        FROM read_parquet('{baseline_glob}')
        WHERE corporate_action_formula_status IN ({rights_status_values})
        """
    ).fetchone()
    result["rights_issue_price_formula_rows"] = int(rights_price_rows)
    result["rights_issue_price_formula_failures"] = int(rights_price_failures)
    if legacy_glob:
        result["legacy_boundary_violations"] = int(
            connection.execute(
                f"""
                SELECT count(*)
                FROM read_parquet('{legacy_glob}')
                WHERE eligible AND date >= DATE '{start}' AND date <= DATE '{end}'
                  AND ((round(raw_high, 2) > round(limit_up, 2))
                    OR (round(raw_low, 2) < round(limit_down, 2)))
                """
            ).fetchone()[0]
        )
    return result


def _event_distribution_and_execution(
    connection: duckdb.DuckDBPyConnection,
    *,
    panel_glob: str,
    start: str,
    end: str,
    order_audit_path: Path | None = None,
) -> dict[str, Any]:
    rows = connection.execute(
        f"""
        WITH source AS (
            SELECT *,
                   {BOARD_SQL} AS board,
                   lead(date) OVER (PARTITION BY code ORDER BY date) AS next_date,
                   lead(trade_status) OVER (PARTITION BY code ORDER BY date) AS next_trade,
                   lead(raw_open) OVER (PARTITION BY code ORDER BY date) AS next_open,
                   lead(raw_high) OVER (PARTITION BY code ORDER BY date) AS next_high,
                   lead(raw_low) OVER (PARTITION BY code ORDER BY date) AS next_low,
                   lead(limit_up) OVER (PARTITION BY code ORDER BY date) AS next_limit_up,
                   lead(limit_down) OVER (PARTITION BY code ORDER BY date) AS next_limit_down
            FROM read_parquet('{panel_glob}')
            WHERE date >= DATE '{start}' - INTERVAL 1 DAY
              AND date <= DATE '{end}'
        ), dated AS (
            SELECT *, lead(date) OVER (ORDER BY date) AS next_market_date
            FROM (SELECT DISTINCT date FROM source)
        ), events AS (
            SELECT s.*,
                   d.next_market_date,
                   next_trade AND next_open IS NOT NULL
                     AND NOT (
                       round(next_open, 2) = round(next_high, 2)
                       AND round(next_open, 2) = round(next_low, 2)
                       AND round(next_open, 2) = round(next_limit_up, 2)
                     ) AS can_buy_next_open,
                   next_trade AND next_open IS NOT NULL
                     AND NOT (
                       round(next_open, 2) = round(next_high, 2)
                       AND round(next_open, 2) = round(next_low, 2)
                       AND round(next_open, 2) = round(next_limit_down, 2)
                     ) AS can_sell_next_open
            FROM source s JOIN dated d USING (date)
            WHERE eligible AND date >= DATE '{start}' AND date <= DATE '{end}'
        ), event_rows AS (
            SELECT events.*, event_values.event_name AS event
            FROM events
            CROSS JOIN LATERAL (
                VALUES
                    ('up_close', is_limit_up),
                    ('up_unsealed', round(raw_high, 2) = round(limit_up, 2)
                        AND NOT is_limit_up),
                    ('down_close', is_limit_down),
                    ('down_unsealed', round(raw_low, 2) = round(limit_down, 2)
                        AND NOT is_limit_down)
            ) AS event_values(event_name, hit)
            WHERE event_values.hit
        )
        SELECT year(date), board, event, count(*),
               count(*) FILTER (WHERE next_date = next_market_date),
               avg(can_buy_next_open::INTEGER) FILTER (
                   WHERE next_date = next_market_date
               ),
               avg(can_sell_next_open::INTEGER) FILTER (
                   WHERE next_date = next_market_date
               )
        FROM event_rows
        GROUP BY 1, 2, 3 ORDER BY 1, 2, 3
        """
    ).fetchall()
    result = {
        "rows": [
            {
                "year": int(year),
                "board": board,
                "event": event,
                "events": int(count),
                "with_next_market_row": int(next_rows),
                "next_open_buyable_rate": _finite(buyable),
                "next_open_sellable_rate": _finite(sellable),
            }
            for year, board, event, count, next_rows, buyable, sellable in rows
        ]
    }
    if order_audit_path is not None and order_audit_path.exists():
        audit_glob = order_audit_path.as_posix()
        audit_rows = connection.execute(
            f"""
            WITH events AS (
                SELECT code, date, target_5,
                       {BOARD_SQL} AS board,
                       event_values.event_name AS event
                FROM read_parquet('{panel_glob}')
                CROSS JOIN LATERAL (
                    VALUES
                        ('up_close', is_limit_up),
                        ('up_unsealed', round(raw_high, 2) = round(limit_up, 2)
                            AND NOT is_limit_up),
                        ('down_close', is_limit_down),
                        ('down_unsealed', round(raw_low, 2) = round(limit_down, 2)
                            AND NOT is_limit_down)
                ) AS event_values(event_name, hit)
                WHERE eligible AND event_values.hit
                  AND date >= DATE '{start}' AND date <= DATE '{end}'
            ), orders AS (
                SELECT CAST(order_id AS BIGINT) AS order_id,
                       CAST(signal_date AS DATE) AS signal_date,
                       lpad(CAST(code AS VARCHAR), 6, '0') AS code,
                       any_value(upper(CAST(side AS VARCHAR))) AS side,
                       bool_or(outcome = 'filled') AS ever_filled,
                       bool_or(outcome = 'deferred') AS ever_deferred,
                       max(CAST(requested_amount AS DOUBLE)) AS requested_amount,
                       sum(CAST(filled_amount AS DOUBLE)) AS filled_amount
                FROM read_csv_auto('{audit_glob}', header=true)
                GROUP BY 1, 2, 3
            )
            SELECT year(e.date), e.board, e.event, o.side,
                   count(*) AS selected_orders,
                   count(*) FILTER (WHERE o.ever_filled) AS filled_orders,
                   avg(o.ever_filled::INTEGER) AS actual_fill_rate,
                   sum(o.filled_amount) AS actual_fill_amount,
                   count(*) FILTER (
                       WHERE NOT o.ever_filled AND o.ever_deferred
                   ) AS backlog_orders,
                   sum(o.requested_amount) FILTER (
                       WHERE NOT o.ever_filled AND o.ever_deferred
                   ) AS backlog_requested_amount,
                   count(*) FILTER (
                       WHERE NOT o.ever_filled AND e.target_5 IS NOT NULL
                   ) AS unexecuted_orders_with_label,
                   sum(o.requested_amount * e.target_5) FILTER (
                       WHERE NOT o.ever_filled AND e.target_5 IS NOT NULL
                   ) AS unexecuted_endpoint_pnl_contribution
            FROM events e
            JOIN orders o ON o.code = e.code AND o.signal_date = e.date
            GROUP BY 1, 2, 3, 4 ORDER BY 1, 2, 3, 4
            """
        ).fetchall()
        result["actual_order_execution_rows"] = [
            {
                "year": int(year),
                "board": board,
                "event": event,
                "side": side,
                "selected_orders": int(selected),
                "filled_orders": int(filled),
                "actual_fill_rate": _finite(fill_rate),
                "actual_fill_amount": _finite(fill_amount),
                "backlog_orders": int(backlog),
                "backlog_requested_amount": _finite(backlog_amount),
                "unexecuted_orders_with_label": int(unexecuted),
                "unexecuted_endpoint_pnl_contribution": _finite(
                    unexecuted_contribution
                ),
            }
            for (
                year,
                board,
                event,
                side,
                selected,
                filled,
                fill_rate,
                fill_amount,
                backlog,
                backlog_amount,
                unexecuted,
                unexecuted_contribution,
            ) in audit_rows
        ]
    return result


def _corporate_action_coverage(
    *, baseline_root: Path, corporate_action_root: Path, cutoff: str
) -> dict[str, Any]:
    panel_codes: set[str] = set()
    for path in baseline_root.glob("year=*/panel.parquet"):
        panel_codes.update(
            pd.read_parquet(path, columns=["code"])["code"]
            .astype(str)
            .str.zfill(6)
            .unique()
        )
    all_files = list(corporate_action_root.glob("*.parquet"))
    file_codes = {path.stem for path in all_files}
    files = [
        corporate_action_root / f"{code}.parquet"
        for code in sorted(panel_codes & file_codes)
    ]
    nonempty = 0
    event_rows = 0
    formula_status_counts: dict[str, int] = {}
    last = pd.Timestamp(cutoff).normalize()
    for path in files:
        metadata = pd.read_parquet(
            path,
            columns=["ex_date", "formula_status"],
        )
        ex_dates = pd.to_datetime(metadata["ex_date"], errors="coerce").dt.normalize()
        through_cutoff = ex_dates.notna() & ex_dates.le(last)
        rows = int(through_cutoff.sum())
        event_rows += rows
        nonempty += rows > 0
        for status, count in metadata.loc[
            through_cutoff, "formula_status"
        ].value_counts().items():
            formula_status_counts[str(status)] = (
                formula_status_counts.get(str(status), 0) + int(count)
            )
    missing = sorted(panel_codes - file_codes)
    return {
        "panel_codes": len(panel_codes),
        "available_files_total": len(all_files),
        "files": len(files),
        "panel_codes_with_files": len(panel_codes & file_codes),
        "panel_file_coverage": (
            len(panel_codes & file_codes) / len(panel_codes) if panel_codes else None
        ),
        "nonempty_files": nonempty,
        "dated_event_rows": event_rows,
        "formula_status_counts": dict(sorted(formula_status_counts.items())),
        "unsupported_event_rows": sum(
            count
            for status, count in formula_status_counts.items()
            if requires_conservative_invalidation(status)
        ),
        "missing_panel_codes": missing,
    }


def _qfq_rebuild_coverage(path: Path, *, cutoff: str) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    last_year = pd.Timestamp(cutoff).year
    adjusted = {
        str(year): int(count)
        for year, count in payload.get("adjusted_rows_by_year", {}).items()
        if int(year) <= last_year
    }
    return {
        "built_files": int(payload.get("built", 0)),
        "failures": payload.get("failures", []),
        "adjusted_rows_by_year_through_cutoff": adjusted,
        "adjusted_rows_through_cutoff": sum(adjusted.values()),
        "event_ratio_by_year": payload.get("event_ratio_by_year", {}),
        "formula_status_counts": payload.get("formula_status_counts", {}),
        "action_source_layer": payload.get("action_source_layer"),
        "continuity_max_abs_error": _finite(
            payload.get("continuity_max_abs_error")
        ),
        "physical_read_cutoff": payload.get("physical_read_cutoff"),
        "frozen_universe_files": payload.get("frozen_universe_files"),
        "universe_rule": payload.get("universe_rule"),
        "updated_at": payload.get("updated_at"),
    }


def _portfolio_action_data_gates(
    action_execution_audit: dict[str, dict[str, dict[str, Any]]],
) -> dict[str, bool]:
    """Require complete holder entitlements on every compared execution path."""
    audited_paths = [
        diagnostics
        for variant in action_execution_audit.values()
        for diagnostics in variant.values()
    ]
    return {
        "portfolio_holder_entitlements_are_complete": (
            bool(audited_paths)
            and all(
                diagnostics.get("corporate_action_events", 0) > 0
                and diagnostics.get(
                    "corporate_action_unresolved_holder_entitlement_events",
                    -1,
                )
                == 0
                for diagnostics in audited_paths
            )
        ),
        "reference_mismatch_entitlements_are_credited": (
            bool(audited_paths)
            and all(
                diagnostics.get(
                    "corporate_action_reference_mismatch_events", 0
                )
                > 0
                and diagnostics.get(
                    "corporate_action_reference_mismatch_entitlements_credited",
                    -1,
                )
                == diagnostics.get(
                    "corporate_action_reference_mismatch_events", 0
                )
                for diagnostics in audited_paths
            )
        ),
    }


def build_diagnostics(args: argparse.Namespace) -> dict[str, Any]:
    repo_root = Path(__file__).resolve().parents[3]
    baseline_root = Path(args.baseline_panel_root)
    candidate_root = Path(args.candidate_panel_root)
    baseline_scores = Path(args.baseline_score_root)
    candidate_scores = Path(args.candidate_score_root)
    baseline_report = Path(args.baseline_report_root)
    candidate_report = Path(args.candidate_report_root)
    baseline_performance = json.loads(
        (baseline_report / "PERFORMANCE.json").read_text(encoding="utf-8")
    )
    candidate_performance = json.loads(
        (candidate_report / "PERFORMANCE.json").read_text(encoding="utf-8")
    )
    candidate_admission = candidate_performance["scenarios"][
        "gbrt_historical"
    ]["admission_diagnostics"]
    action_execution_audit = {
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
            ("baseline", baseline_performance),
            ("candidate", candidate_performance),
        )
    }
    baseline_glob = _sql_path(baseline_root, filename="panel.parquet")
    candidate_glob = _sql_path(candidate_root, filename="panel.parquet")
    baseline_score_glob = _sql_path(baseline_scores, filename="scores.parquet")
    candidate_score_glob = _sql_path(candidate_scores, filename="scores.parquet")
    legacy_glob = (
        _sql_path(Path(args.legacy_panel_root), filename="panel.parquet")
        if args.legacy_panel_root
        else None
    )
    connection = duckdb.connect()
    coverage = _coverage_and_events(
        connection,
        panel_glob=candidate_glob,
        start=args.start,
        end=args.end,
    )
    validation = _data_validation(
        connection,
        baseline_glob=baseline_glob,
        legacy_glob=legacy_glob,
        start=args.start,
        end=args.end,
    )
    identity = _panel_identity(
        connection,
        frozen_glob=baseline_glob,
        candidate_glob=candidate_glob,
    )
    panel_identity_passed = all(
        item["candidate_only_keys"] == 0
        and item["baseline_only_keys"] == 0
        and item["existing_field_hash_differences"] == 0
        for item in identity["years"].values()
    )
    score_identity = _score_identity(
        connection,
        baseline_glob=baseline_score_glob,
        candidate_glob=candidate_score_glob,
        start=args.start,
        end=args.end,
    )
    score_identity_passed = (
        score_identity["baseline_rows"] == score_identity["candidate_rows"]
        and score_identity["candidate_only_keys"] == 0
        and score_identity["baseline_only_keys"] == 0
        and score_identity["factor_score_differences"] == 0
        and score_identity["target_differences"] == 0
        and score_identity["label_available_date_differences"] == 0
        and score_identity["candidate_training_label_leaks"] == 0
    )
    corporate_actions = _corporate_action_coverage(
        baseline_root=baseline_root,
        corporate_action_root=Path(args.corporate_action_root),
        cutoff=args.end,
    )
    qfq_rebuild = _qfq_rebuild_coverage(
        Path(args.qfq_coverage), cutoff=args.end
    )
    action_event_fetch = json.loads(
        Path(args.corporate_action_event_coverage).read_text(encoding="utf-8")
    )
    qfq_event_ratios_valid = bool(qfq_rebuild["event_ratio_by_year"]) and all(
        int(summary.get("count", 0)) > 0
        and all(
            np.isfinite(float(summary.get(key, np.nan)))
            for key in ("min", "median", "max")
        )
        and 0.0 < float(summary["min"])
        <= float(summary["median"])
        <= float(summary["max"])
        for summary in qfq_rebuild["event_ratio_by_year"].values()
    )
    data_gates = {
        "full_period_coverage_at_least_99_5pct": coverage["coverage"] >= 0.995,
        "each_year_coverage_at_least_99pct": (
            set(coverage["years"]) == {str(year) for year in EXPECTED_YEARS}
            and all(
                coverage["years"][str(year)]["coverage"] >= 0.99
                for year in EXPECTED_YEARS
            )
        ),
        "each_event_has_zero_and_nonzero_each_year": all(
            coverage["years"].get(str(year), {}).get("features", {}).get(
                feature, {}
            ).get("zero_rows", 0)
            > 0
            and coverage["years"].get(str(year), {}).get("features", {}).get(
                feature, {}
            ).get("nonzero_rows", 0)
            > 0
            for year in EXPECTED_YEARS
            for feature in PRICE_LIMIT_MODEL_FEATURES
        ),
        "valid_limits_within_raw_ohlc": validation["valid_boundary_violations"] == 0,
        "close_flags_imply_touch": validation["close_without_touch"] == 0,
        "labels_do_not_cross_cutoff": validation["labels_realized_after_cutoff"] == 0,
        "unsupported_action_labels_are_invalidated": (
            validation["unsupported_action_labels_not_invalidated"] == 0
        ),
        "rights_issue_price_capability_is_used_for_limits": (
            validation["rights_issue_price_formula_rows"] > 0
            and validation["rights_issue_price_formula_failures"] == 0
        ),
        "candidate_preserves_all_baseline_fields": panel_identity_passed,
        "score_keys_targets_and_factor_are_identical": score_identity_passed,
        "corporate_action_files_cover_panel": (
            corporate_actions["panel_file_coverage"] == 1.0
            and not corporate_actions["missing_panel_codes"]
        ),
        "corporate_action_event_fetch_completed_without_failures": (
            not action_event_fetch.get("failures")
            and action_event_fetch.get("physical_read_cutoff") == args.end
            and action_event_fetch.get("source")
            == CORPORATE_ACTION_ASOF_SOURCE
            and action_event_fetch.get("query_boundary")
            == "server_side_end_date"
            and action_event_fetch.get("universe_rule")
            == CORPORATE_ACTION_ASOF_UNIVERSE_RULE
            and len(action_event_fetch.get("completed_codes", []))
            == int(action_event_fetch.get("files_expected", -1))
            and len(action_event_fetch.get("preclose_completed_codes", []))
            == int(action_event_fetch.get("files_expected", -1))
            and int(
                action_event_fetch.get("reference_validation", {}).get(
                    "checked_reference_rows", 0
                )
            )
            > 0
            and int(
                action_event_fetch.get("reference_validation", {}).get(
                    "missing_reference_rows", -1
                )
            )
            == int(
                action_event_fetch.get("reference_validation", {}).get(
                    "reference_gap_invalidations", -2
                )
            )
        ),
        "all_missing_reference_prices_were_conservatively_invalidated": (
            int(
                action_event_fetch.get("reference_validation", {}).get(
                    "missing_reference_rows", -1
                )
            )
            == int(
                action_event_fetch.get("reference_validation", {}).get(
                    "reference_gap_invalidations", -2
                )
            )
        ),
        "corporate_action_reference_was_exercised": (
            validation["corporate_action_reference_rows"] > 0
        ),
        "qfq_rebuild_completed_without_failures": (
            qfq_rebuild["built_files"] > 0 and not qfq_rebuild["failures"]
        ),
        "qfq_rebuild_exercised_company_actions": (
            qfq_rebuild["adjusted_rows_through_cutoff"] > 0
        ),
        "qfq_used_complete_event_layer": (
            qfq_rebuild["action_source_layer"] == "corporate_action_events"
        ),
        "qfq_physical_cutoff_matches_public_boundary": (
            qfq_rebuild["physical_read_cutoff"] == args.end
        ),
        "qfq_universe_is_frozen_at_public_boundary": (
            qfq_rebuild["universe_rule"]
            == CORPORATE_ACTION_ASOF_UNIVERSE_RULE
        ),
        "qfq_event_ratios_are_finite_positive_and_ordered": (
            qfq_event_ratios_valid
        ),
        "qfq_continuity_identity_holds": (
            qfq_rebuild["continuity_max_abs_error"] is not None
            and qfq_rebuild["continuity_max_abs_error"] <= 1e-8
        ),
        "portfolio_company_action_share_credits_are_audited": (
            candidate_admission.get("corporate_action_events", 0) > 0
            and candidate_admission.get("corporate_action_prior_shares", 0.0) > 0.0
            and candidate_admission.get("corporate_action_new_shares", 0.0)
            >= candidate_admission.get("corporate_action_prior_shares", 0.0)
            and candidate_admission.get("corporate_action_credited_shares", -1.0)
            >= 0.0
        ),
        **_portfolio_action_data_gates(action_execution_audit),
    }
    performance_gates = _gate_evaluation(
        baseline_report_root=baseline_report,
        candidate_report_root=candidate_report,
    )
    all_gates_passed = all(data_gates.values()) and performance_gates[
        "all_gates_passed"
    ]
    performance_gates["experiment_decision"] = (
        "development_winner_pending_fresh_forward_validation"
        if all_gates_passed
        else "rejected_on_development"
    )
    performance_gates["all_gates_passed"] = all_gates_passed
    payload = {
        "experiment": "20260717_price_limit_state_feature_race",
        "start": args.start,
        "end": args.end,
        "physical_read_cutoff": args.end,
        "label": "T+1 open through T+6 open endpoint return",
        "feature_schema": {
            "baseline": list(MODEL_INPUT_FEATURES),
            "candidate": list(PRICE_LIMIT_MODEL_INPUT_FEATURES),
            "new": list(NEW_FEATURES),
        },
        "baseline_input_fingerprints": [
            _file_fingerprint(path, repo_root)
            for path in sorted(baseline_root.glob("year=*/panel.parquet"))
        ],
        "corporate_actions": corporate_actions,
        "corporate_action_event_fetch": action_event_fetch,
        "qfq_rebuild": qfq_rebuild,
        "data_validation": validation,
        "candidate_gbrt_historical_admission_diagnostics": candidate_admission,
        "portfolio_action_execution_audit": action_execution_audit,
        "coverage_and_events": coverage,
        "event_distribution_and_next_open_execution": (
            _event_distribution_and_execution(
                connection,
                panel_glob=candidate_glob,
                start=args.start,
                end=args.end,
                order_audit_path=(
                    candidate_report / "order_audit_gbrt_historical.csv"
                ),
            )
        ),
        "identity": {
            "panel": identity,
            "scores": score_identity,
        },
        "new_by_existing_mean_daily_spearman": _mean_daily_spearman(
            connection,
            panel_glob=candidate_glob,
            start=args.start,
            end=args.end,
            left=NEW_FEATURES,
            right=MODEL_INPUT_FEATURES,
        ),
        "new_feature_mean_daily_spearman": _mean_daily_spearman(
            connection,
            panel_glob=candidate_glob,
            start=args.start,
            end=args.end,
            left=NEW_FEATURES,
            right=NEW_FEATURES,
        ),
        "model_stability": _model_stability(
            connection,
            baseline_glob=baseline_score_glob,
            candidate_glob=candidate_score_glob,
            start=args.start,
            end=args.end,
        ),
        "data_gates": data_gates,
        "gate_evaluation": performance_gates,
    }
    connection.close()
    return payload


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline-panel-root", required=True)
    parser.add_argument("--candidate-panel-root", required=True)
    parser.add_argument("--legacy-panel-root")
    parser.add_argument("--baseline-score-root", required=True)
    parser.add_argument("--candidate-score-root", required=True)
    parser.add_argument("--baseline-report-root", required=True)
    parser.add_argument("--candidate-report-root", required=True)
    parser.add_argument(
        "--corporate-action-root",
        default="data/parquet/corporate_action_events",
    )
    parser.add_argument(
        "--qfq-coverage",
        default="data/state/corporate_action_qfq_coverage.json",
    )
    parser.add_argument(
        "--corporate-action-event-coverage",
        default="data/state/corporate_action_event_coverage.json",
    )
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
