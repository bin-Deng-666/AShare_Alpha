#!/usr/bin/env python3
"""Data maintenance CLI; independent of strategies and signal panels."""

from __future__ import annotations
import argparse
from datetime import date
import json


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("assets", help="show implemented tasks and dependencies")
    for name in ("plan", "update", "accept"):
        p = sub.add_parser(name)
        p.add_argument(
            "--target", type=date.fromisoformat, help="data cutoff (YYYY-MM-DD)"
        )
        p.add_argument(
            "--tasks", nargs="+", help="selected tasks, including dependencies"
        )
        p.add_argument(
            "--codes", nargs="+", help="six-digit securities (state is scoped)"
        )
        p.add_argument(
            "--workers", type=int, default=4, help="per-task securities workers"
        )
        p.add_argument(
            "--parallel-tasks",
            type=int,
            default=4,
            help="maximum concurrent dependency tasks",
        )
        p.add_argument(
            "--refresh-from",
            type=date.fromisoformat,
            help="recheck historical market data from this date and merge corrections",
        )
        p.add_argument("--force", action="store_true")
        p.add_argument("--json", action="store_true")
    sub.add_parser("inspect", help="read local inventory without network requests")
    status = sub.add_parser(
        "status",
        help="read task progress and audit status without taking the writer lock",
    )
    status.add_argument("--json", action="store_true")
    sub.add_parser("validate", help="full local data quality audit")
    sub.add_parser(
        "catalog", help="rebuild query views from local files without source refresh"
    )
    from ashare_alpha.data.pipeline.maintenance import COMMANDS
    from pathlib import Path

    build = sub.add_parser(
        "build", help="explicit initial import / local repair / rebuild"
    )
    build.add_argument("asset", choices=sorted(COMMANDS))
    build.add_argument("--limit", type=int)
    build.add_argument("--codes", nargs="+")
    build.add_argument("--force", action="store_true")
    build.add_argument("--data-cutoff")
    build.add_argument("--source-root", type=Path)
    build.add_argument("--workers", type=int, default=4)
    args = parser.parse_args(argv)
    import logging

    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s"
    )
    if args.command == "build":
        from ashare_alpha.data.pipeline.maintenance import run_maintenance

        return run_maintenance(args)
    from ashare_alpha.data.runtime import load_config
    from ashare_alpha.data.pipeline.tasks import TASKS

    config = load_config()
    if args.command == "status":
        from ashare_alpha.data.pipeline.status import read_status

        result = read_status()
        if args.json:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        else:
            run = result["latest_run"] or {}
            active = ",".join(run.get("current_tasks", [])) or run.get(
                "current_task", "-"
            )
            print(
                f"writer_active={result['writer_active']} run={run.get('status', 'never_run')} tasks={active} target={run.get('target', '-')}"
            )
            quality = result["quality"]
            print(
                f"quality={quality['status']} current={quality['current_for_latest_run']} bars_through={quality['bar_max_date']}"
            )
            for item in result["tasks"]:
                print(
                    f"{item['task']:20} {item['status']:12} checked_through={item['checked_through'] or '-'}"
                )
        return 0
    if args.command == "assets":
        for t in TASKS:
            print(
                f"{t.name:20} cadence={t.cadence_days}d network={t.network} resources={','.join(t.resources) or '-'} deps={','.join(t.dependencies)}"
            )
        return 0
    if args.command == "inspect":
        from ashare_alpha.data.pipeline.planner import task_paths

        for t in TASKS:
            outputs = task_paths(t)[1]
            print(
                f"{t.name:20} files={len(outputs):6} bytes={sum(p.stat().st_size for p in outputs if p.exists())}"
            )
        return 0
    if args.command == "catalog":
        from ashare_alpha.data.research.catalog import build_catalog
        from ashare_alpha.data.paths import STATE_ROOT
        from ashare_alpha.data.storage import writer_lock

        with writer_lock(STATE_ROOT / "data_update.lock"):
            build_catalog(config)
        return 0
    if args.command == "validate":
        from ashare_alpha.data.research.validation import (
            validate_dataset,
            build_manifest,
        )
        from ashare_alpha.data.storage import read_json
        from ashare_alpha.data.paths import DATA_ROOT, STATE_ROOT
        from ashare_alpha.data.storage import writer_lock

        with writer_lock(STATE_ROOT / "data_update.lock"):
            validate_dataset(config)
            build_manifest(config)
        result = read_json(DATA_ROOT / "quality_report.json")
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result["status"] == "pass" else 1
    config["end_date"] = str(args.target or config.get("end_date") or date.today())
    if args.refresh_from:
        if args.refresh_from > date.fromisoformat(config["end_date"]):
            parser.error("--refresh-from must not be later than --target")
        config["refresh_from"] = args.refresh_from.isoformat()
    from ashare_alpha.data.pipeline.tasks import select_tasks

    config["derive_qfq"] = any(t.name == "adjustment" for t in select_tasks(args.tasks))
    if args.command == "plan":
        from ashare_alpha.data.pipeline.planner import build_plan

        plan = build_plan(
            config["end_date"],
            names=args.tasks,
            config=config,
            codes=args.codes,
            force=args.force,
        )
        if args.json:
            print(json.dumps(plan, ensure_ascii=False, indent=2))
        else:
            for item in plan:
                print(f"{item['action']:8} {item['task']:20} {item['reason']}")
        return 0
    if args.command == "accept":
        if args.tasks or args.codes or args.force or args.refresh_from:
            parser.error(
                "accept verifies the standard full-market update; use update for scoped repairs"
            )
        from ashare_alpha.data.pipeline.acceptance import run_acceptance

        report = run_acceptance(
            config, workers=args.workers, parallel_tasks=args.parallel_tasks
        )
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0 if report["status"] == "pass" else 1
    from ashare_alpha.data.pipeline.runner import run_update

    report = run_update(
        config,
        names=args.tasks,
        codes=args.codes,
        workers=args.workers,
        force=args.force,
        parallel_tasks=args.parallel_tasks,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["status"] == "success" else 1


if __name__ == "__main__":
    raise SystemExit(main())
