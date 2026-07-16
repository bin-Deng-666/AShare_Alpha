"""Build features, score walk-forward models and run the rotating backtest."""

from __future__ import annotations

import argparse

import pandas as pd

from strategy.rotating.features.build_panel import (
    BASELINE_MODEL_INPUT_FEATURES,
    CANDIDATE_MODEL_INPUT_FEATURES,
    MODEL_TARGET_COLUMN,
)
from strategy.rotating.pipeline import (
    build_panel_dataset,
    run_backtest_dataset,
    score_panel_dataset,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("build-panel", "score", "backtest", "all"))
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    parser.add_argument("--capital", type=float, default=1_000_000.0)
    parser.add_argument("--data-cutoff")
    parser.add_argument("--panel-root")
    parser.add_argument("--score-root")
    parser.add_argument(
        "--target-column",
        choices=("target_rank", "target_path_rank"),
        default=MODEL_TARGET_COLUMN,
    )
    parser.add_argument(
        "--feature-set",
        choices=("baseline", "enhanced"),
        default="enhanced",
    )
    parser.add_argument(
        "--report-root",
        help="Backtest report directory; defaults to strategy/rotating/reports",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    feature_columns = (
        BASELINE_MODEL_INPUT_FEATURES
        if args.feature_set == "baseline"
        else CANDIDATE_MODEL_INPUT_FEATURES
    )
    panel_options = {"output_root": args.panel_root} if args.panel_root else {}
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
            **({"score_root": args.score_root} if args.score_root else {}),
            **({"report_root": args.report_root} if args.report_root else {}),
        )


if __name__ == "__main__":
    main()
