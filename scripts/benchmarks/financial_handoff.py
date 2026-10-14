"""Run two real issuers through the announcement/financial handoff in isolation.

Creates temporary assets and source receipts. Reads the real master, but never
writes canonical files. First financial processing must precede producer finish.
"""

from __future__ import annotations
from contextlib import ExitStack, redirect_stdout
import io
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from time import perf_counter
from unittest.mock import patch

import pandas as pd

from ashare_alpha.data.paths import PARQUET_ROOT
from ashare_alpha.data.runtime import load_config
from ashare_alpha.data.pipeline import runner, planner
from ashare_alpha.data.pipeline.tasks import Task


def live_job(name, config, codes, workers, force):
    from ashare_alpha.data.pipeline import handlers, source_receipts
    from ashare_alpha.data import incremental_files
    from ashare_alpha.data.sources import announcements, financial_statements

    root = Path(config["benchmark_root"])
    parquet = root / "parquet"
    state = root / "state"
    handlers.P = parquet
    handlers.S = state
    source_receipts.PARQUET_ROOT = parquet
    source_receipts.STATE_ROOT = state
    incremental_files.PARQUET = parquet
    for module in (announcements, financial_statements):
        module.PARQUET = parquet
        module.STATE = state
    original = financial_statements.update_financials_incremental

    def first_request(*args, **kwargs):
        marker = root / "financial_first.json"
        if not marker.exists():
            marker.write_text(json.dumps({"time": perf_counter()}))
        return original(*args, **kwargs)

    financial_statements.update_financials_incremental = first_request
    result = runner._execute_task(name, config, codes, workers, force)
    (root / f"{name}_finished.json").write_text(json.dumps({"time": perf_counter()}))
    return result


def main():
    codes = ["000001", "600519"]
    with TemporaryDirectory() as directory:
        root = Path(directory)
        parquet = root / "parquet"
        state = root / "state"
        master = pd.read_parquet(
            PARQUET_ROOT / "security_master/security_master.parquet"
        )
        (parquet / "security_master").mkdir(parents=True)
        master[master.code.isin(codes)].to_parquet(
            parquet / "security_master/security_master.parquet", index=False
        )
        config = {
            **load_config(),
            "end_date": "2026-10-10",
            "source_batch_size": 1,
            "benchmark_root": str(root),
        }
        tasks = (
            Task(
                "announcements",
                (),
                ("security_master/*.parquet",),
                ("announcements/*.parquet",),
                True,
            ),
            Task(
                "financials",
                ("announcements",),
                ("announcements/*.parquet",),
                ("financials/*.parquet",),
                True,
                7,
            ),
        )
        with ExitStack() as stack:
            stack.enter_context(patch.object(runner, "STATE_ROOT", state))
            stack.enter_context(patch.object(planner, "STATE_ROOT", state))
            stack.enter_context(patch.object(planner, "PARQUET_ROOT", parquet))
            stack.enter_context(
                patch.object(runner, "select_tasks", return_value=tasks)
            )
            stack.enter_context(
                patch.object(planner, "select_tasks", return_value=tasks)
            )
            stack.enter_context(patch.object(runner, "_execute_task", live_job))
            tick = perf_counter()
            with redirect_stdout(io.StringIO()):
                result = runner.run_update(config, workers=2, parallel_tasks=2)
            elapsed = perf_counter() - tick
        assert result["status"] == "success", result
        first = json.loads((root / "financial_first.json").read_text())["time"]
        producer_end = json.loads((root / "announcements_finished.json").read_text())[
            "time"
        ]
        assert first < producer_end, (first, producer_end)
        print(
            json.dumps(
                {
                    "issuers": codes,
                    "seconds": round(elapsed, 3),
                    "financial_started_before_all_announcements_finished": True,
                    "overlap_seconds": round(producer_end - first, 3),
                    "tasks": result["results"],
                    "canonical_files_modified": False,
                },
                ensure_ascii=False,
                indent=2,
            )
        )


if __name__ == "__main__":
    main()
