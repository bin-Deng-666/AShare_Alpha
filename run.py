#!/usr/bin/env python3
"""Daily strategy action list (signal day → next-session open).

Usage (from repo root):
  python3 run.py crowding_price_volume
  python3 run.py crowding_price_volume --date 2026-07-10
  python3 run.py crowding_price_volume --capital 500000
  python3 run.py --list
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parent
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from utils import list_strategies, predict_next
from utils.sizing import format_details_console, format_orders_console


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Next-session action list for a strategy")
    ap.add_argument(
        "strategy",
        nargs="?",
        default=None,
        help=f"strategy folder under strategies/; known: {', '.join(list_strategies())}",
    )
    ap.add_argument("--date", default=None, help="signal date YYYY-MM-DD (default: latest in panel)")
    ap.add_argument("--rebuild", action="store_true", help="force rebuild signal panel")
    ap.add_argument("--panel", default=None, help="path to signals parquet")
    ap.add_argument(
        "--capital",
        type=float,
        default=None,
        help="equity for sizing (default: strategy initial_capital, usually 1e6)",
    )
    ap.add_argument("--list", action="store_true", help="list registered strategies")
    ap.add_argument("--json", action="store_true", help="print full JSON result")
    args = ap.parse_args(argv)

    if args.list:
        print("strategies:")
        for name in list_strategies():
            print(f"  - {name}")
        return 0
    if not args.strategy:
        print("strategies:")
        for name in list_strategies():
            print(f"  - {name}")
        print("\nusage: python3 run.py <strategy> [--date YYYY-MM-DD] [--capital 1000000]")
        return 2

    out = predict_next(
        args.strategy,
        asof=args.date,
        rebuild=args.rebuild,
        panel=args.panel,
        capital=args.capital,
    )
    if args.json:
        printable = {k: v for k, v in out.items() if k != "paths"}
        printable["paths"] = out.get("paths", {})
        print(json.dumps(printable, ensure_ascii=False, indent=2, default=str))
    else:
        print(
            f"{out['strategy']}  信号日 {out['asof']} → 执行日 {out.get('exec_date')} "
            f"({out['exec_hint']})"
        )
        if out.get("bars_asof"):
            print(f"行情最新 {out['bars_asof']}", end="")
            if out.get("bars_asof") != out.get("asof"):
                print("  ≠ 面板（面板偏旧）", end="")
            print()
        for w in out.get("warnings") or []:
            print(f"⚠ {w}")
        print()
        print(format_orders_console(out.get("orders")))
        print()
        print(format_details_console(out))
        paths = out.get("paths") or {}
        if paths.get("report_md"):
            print(f"\nreport: {paths['report_md']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
