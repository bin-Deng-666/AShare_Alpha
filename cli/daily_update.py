#!/usr/bin/env python3
"""Update/audit canonical data, then prepare class-based strategy inputs."""

import argparse
import json
import subprocess
import sys

from ashare_alpha.paths import CODE_ROOT, DATA_ROOT

REPO = CODE_ROOT


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target")
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--parallel-tasks", type=int, default=4)
    parser.add_argument(
        "--resume",
        action="store_true",
        help="Data task receipts already provide resumption",
    )
    parser.add_argument("--data-only", action="store_true")
    from ashare_alpha.strategies import STRATEGIES, class_strategies

    parser.add_argument("--strategy", choices=class_strategies(), default="demo")
    parser.add_argument(
        "--panel-only",
        action="store_true",
        help="Prepare strategy inputs without data updates",
    )
    parser.add_argument("--force-panel", action="store_true")
    parser.add_argument("--start", default="2020-01-01")
    args = parser.parse_args(argv)
    review = False
    if not args.panel_only:
        command = [
            sys.executable,
            "-m",
            "data",
            "accept",
            "--workers",
            str(args.workers),
            "--parallel-tasks",
            str(args.parallel_tasks),
        ]
        if args.target:
            command += ["--target", args.target]
        rc = subprocess.call(command, cwd=REPO)
        if rc:
            report = json.loads((DATA_ROOT / "state/acceptance.json").read_text())
            # Completion and quality remain separate: review is never reported as pass.
            completed = (
                report.get("status") == "review"
                and (args.target is None or report.get("target") == args.target)
                and report.get("phase") == "finished"
                and report.get("runs")
                and report["runs"][-1]["status"] == "success"
                and report.get("repeat", {}).get("all_skipped")
            )
            if not completed:
                return rc
            review = True
            print(
                "[strategy] data quality remains REVIEW: "
                + json.dumps(report.get("issues", []), ensure_ascii=False),
                flush=True,
            )
        if args.data_only:
            print(
                json.dumps(
                    {"workflow": "complete", "quality": "review" if review else "pass"},
                    ensure_ascii=False,
                ),
                flush=True,
            )
            return 0
    if not args.data_only:
        from importlib import import_module

        build = import_module(STRATEGIES[args.strategy].runner).build

        data, path = build(args.start, args.target, force=args.force_panel)
        print(
            json.dumps(
                {
                    "status": (
                        "review" if review else data.metadata["quality"]["status"]
                    ),
                    "workflow": "complete",
                    "inputs": str(path),
                },
                ensure_ascii=False,
            ),
            flush=True,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
