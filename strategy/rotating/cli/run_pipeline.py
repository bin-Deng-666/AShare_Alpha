"""Build features, score walk-forward models and run the rotating backtest."""

from __future__ import annotations

import argparse

import pandas as pd

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
    parser.add_argument(
        "--report-root",
        help="Backtest report directory; defaults to strategy/rotating/reports",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.command == "build-panel":
        build_panel_dataset(args.start, args.end)
    if args.command == "all":
        history_start = pd.Timestamp(args.start) - pd.DateOffset(years=3)
        build_panel_dataset(str(history_start.date()), args.end)
    if args.command in ("score", "all"):
        score_panel_dataset(args.start, args.end)
    if args.command in ("backtest", "all"):
        run_backtest_dataset(
            args.start,
            args.end,
            initial_capital=args.capital,
            **({"report_root": args.report_root} if args.report_root else {}),
        )


if __name__ == "__main__":
    main()
