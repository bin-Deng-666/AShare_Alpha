#!/usr/bin/env python3
"""Daily strategy action list (signal day → next-session open).

Usage (from repo root):
  python3 -m cli.run demo
  python3 -m cli.run demo --date 2024-01-31
  python3 -m cli.run demo --capital 500000
  python3 -m cli.run --list
"""

from __future__ import annotations

import argparse
import json

from cli.utils import list_strategies, predict_next
from cli.utils.sizing import format_details_console, format_orders_console


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Next-session action list for a strategy")
    ap.add_argument(
        "strategy",
        nargs="?",
        default=None,
        help=f"strategy name; known: {', '.join(list_strategies())}",
    )
    ap.add_argument(
        "--date",
        default=None,
        help="signal date YYYY-MM-DD (default: latest audited observations)",
    )
    ap.add_argument("--rebuild", action="store_true", help="force rebuild signal panel")
    ap.add_argument(
        "--panel",
        default=None,
        help="prepared input snapshot directory (or a strategy's panel input)",
    )
    ap.add_argument(
        "--capital",
        type=float,
        default=None,
        help="initial capital for model replay (default: configured execution capital)",
    )
    ap.add_argument("--list", action="store_true", help="list registered strategies")
    ap.add_argument(
        "--csv",
        action="store_true",
        help="export buy, sell and positions CSV with the plan",
    )
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
        print(
            "\nusage: python3 -m cli.run <strategy> [--date YYYY-MM-DD] [--capital 1000000]"
        )
        return 2

    out = predict_next(
        args.strategy,
        asof=args.date,
        rebuild=args.rebuild,
        panel=args.panel,
        capital=args.capital,
        csv=args.csv,
    )
    if args.json:
        printable = {k: v for k, v in out.items() if k != "paths"}
        printable["paths"] = out.get("paths", {})
        print(json.dumps(printable, ensure_ascii=False, indent=2, default=str))
    else:
        print(
            f"{out['strategy']}  信号日 {out['asof']} → 执行日 {out.get('exec_date') or '待日历确认'} "
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
