"""Build features, score walk-forward models and run the rotating backtest."""

from __future__ import annotations

import argparse

import pandas as pd

from strategy.rotating.features.build_panel import (
    BASELINE_MODEL_INPUT_FEATURES,
    CAPITAL_GAIN_OVERHANG_MODEL_INPUT_FEATURES,
    CANDIDATE_MODEL_INPUT_FEATURES,
    ENDPOINT_TARGET_COLUMN,
    INDUSTRY_SIZE_MODEL_INPUT_FEATURES,
    MODEL_TARGET_COLUMN,
    PATH_TARGET_COLUMN,
    PRICE_LIMIT_MODEL_INPUT_FEATURES,
)
from strategy.rotating.pipeline import (
    augment_panel_dataset_with_capital_gain_overhang_features,
    augment_panel_dataset_with_industry_size_features,
    augment_panel_dataset_with_price_limit_features,
    build_panel_dataset,
    run_backtest_dataset,
    score_panel_dataset,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "command",
        choices=(
            "build-panel",
            "augment-frozen-panel",
            "augment-price-limit-panel",
            "augment-overhang-panel",
            "score",
            "backtest",
            "all",
        ),
    )
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    parser.add_argument("--capital", type=float, default=1_000_000.0)
    parser.add_argument("--data-cutoff")
    parser.add_argument("--panel-root")
    parser.add_argument(
        "--frozen-panel-root",
        help=(
            "Input panel root for either augmentation command; --panel-root "
            "is its output"
        ),
    )
    parser.add_argument("--score-root")
    parser.add_argument(
        "--target-column",
        choices=(ENDPOINT_TARGET_COLUMN, PATH_TARGET_COLUMN),
        default=MODEL_TARGET_COLUMN,
    )
    parser.add_argument(
        "--feature-set",
        choices=(
            "baseline",
            "enhanced",
            "industry_size",
            "price_limit",
            "overhang",
        ),
        default="enhanced",
    )
    parser.add_argument(
        "--report-root",
        help="Backtest report directory; defaults to strategy/rotating/reports",
    )
    parser.add_argument(
        "--market-gate-candidate",
        action="store_true",
        help="Also report the preregistered CSI 300 10-month entry-gate candidate",
    )
    parser.add_argument(
        "--experiment-decision",
        choices=(
            "rejected_on_development",
            "development_winner_pending_fresh_forward_validation",
        ),
        help="Machine-readable development decision for an experiment report",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    feature_columns = {
        "baseline": BASELINE_MODEL_INPUT_FEATURES,
        "enhanced": CANDIDATE_MODEL_INPUT_FEATURES,
        "industry_size": INDUSTRY_SIZE_MODEL_INPUT_FEATURES,
        "price_limit": PRICE_LIMIT_MODEL_INPUT_FEATURES,
        "overhang": CAPITAL_GAIN_OVERHANG_MODEL_INPUT_FEATURES,
    }[args.feature_set]
    panel_options = {
        **({"output_root": args.panel_root} if args.panel_root else {}),
        "include_industry_size_features": args.feature_set == "industry_size",
        "include_price_limit_features": args.feature_set == "price_limit",
    }
    if args.command == "augment-frozen-panel":
        if args.feature_set != "industry_size":
            raise ValueError(
                "augment-frozen-panel requires --feature-set industry_size"
            )
        if not args.frozen_panel_root or not args.panel_root:
            raise ValueError(
                "augment-frozen-panel requires --frozen-panel-root and --panel-root"
            )
        augment_panel_dataset_with_industry_size_features(
            args.start,
            args.end,
            input_root=args.frozen_panel_root,
            output_root=args.panel_root,
        )
        return
    if args.command == "augment-price-limit-panel":
        if args.feature_set != "price_limit":
            raise ValueError(
                "augment-price-limit-panel requires --feature-set price_limit"
            )
        if not args.frozen_panel_root or not args.panel_root:
            raise ValueError(
                "augment-price-limit-panel requires --frozen-panel-root and --panel-root"
            )
        augment_panel_dataset_with_price_limit_features(
            args.start,
            args.end,
            input_root=args.frozen_panel_root,
            output_root=args.panel_root,
        )
        return
    if args.command == "augment-overhang-panel":
        if args.feature_set != "overhang":
            raise ValueError(
                "augment-overhang-panel requires --feature-set overhang"
            )
        if not args.frozen_panel_root or not args.panel_root:
            raise ValueError(
                "augment-overhang-panel requires --frozen-panel-root and --panel-root"
            )
        augment_panel_dataset_with_capital_gain_overhang_features(
            args.start,
            args.end,
            input_root=args.frozen_panel_root,
            output_root=args.panel_root,
        )
        return
    if args.command == "build-panel":
        build_panel_dataset(
            args.start,
            args.end,
            data_cutoff=args.data_cutoff,
            **panel_options,
        )
    if args.command == "all":
        history_start = pd.Timestamp(args.start) - pd.DateOffset(years=3)
        build_panel_dataset(
            str(history_start.date()),
            args.end,
            data_cutoff=args.data_cutoff or args.end,
            **panel_options,
        )
    if args.command in ("score", "all"):
        score_panel_dataset(
            args.start,
            args.end,
            feature_columns=feature_columns,
            target_column=args.target_column,
            **({"panel_root": args.panel_root} if args.panel_root else {}),
            **({"output_root": args.score_root} if args.score_root else {}),
        )
    if args.command in ("backtest", "all"):
        run_backtest_dataset(
            args.start,
            args.end,
            initial_capital=args.capital,
            include_market_gate_candidate=args.market_gate_candidate,
            expected_feature_columns=feature_columns,
            experiment_decision=args.experiment_decision,
            **({"score_root": args.score_root} if args.score_root else {}),
            **({"report_root": args.report_root} if args.report_root else {}),
        )


if __name__ == "__main__":
    main()
