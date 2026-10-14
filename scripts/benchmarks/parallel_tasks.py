"""Compare serial and parallel scheduling with identical synthetic I/O waits.

This benchmark runs actual spawned processes and writes only temporary markers.
It measures scheduling and process startup, not market-provider throughput.
"""

from __future__ import annotations
import argparse
import contextlib
import io
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from time import perf_counter, sleep
from unittest.mock import patch

from ashare_alpha.data.pipeline import runner
from ashare_alpha.data.pipeline.tasks import Task


def offline_task(name, config, codes, workers, force):
    start = perf_counter()
    delay = config["delay"] if name.startswith("branch_") else 0.02
    sleep(delay)
    (Path(config["marker_root"]) / f"{name}.json").write_text(
        json.dumps({"task": name})
    )
    return {"status": "success", "detail": {}, "seconds": perf_counter() - start}


def measure(parallel_tasks, delay):
    branches = tuple(f"branch_{i}" for i in range(4))
    tasks = (
        Task("seed", (), (), ()),
        *(Task(name, ("seed",), (), ()) for name in branches),
        Task("catalog", branches, (), ()),
    )
    with TemporaryDirectory() as directory:
        root = Path(directory)
        config = {"end_date": "2026-10-10", "marker_root": str(root), "delay": delay}
        with (
            patch.object(runner, "STATE_ROOT", root / "state"),
            patch.object(runner, "select_tasks", return_value=tasks),
            patch.object(runner, "_execute_task", offline_task),
            patch.object(
                runner,
                "task_paths",
                side_effect=lambda task: ([], [root / f"{task.name}.json"]),
            ),
            patch.object(
                runner,
                "build_plan",
                side_effect=lambda *a, **kw: [
                    {"task": kw["only_task"], "action": "refresh", "input": "benchmark"}
                ],
            ),
        ):
            with contextlib.redirect_stdout(io.StringIO()):
                result = runner.run_update(config, parallel_tasks=parallel_tasks)
        assert result["status"] == "success", result
        return result["seconds"]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--delay", type=float, default=2.0, help="synthetic wait per independent branch"
    )
    args = parser.parse_args()
    serial = measure(1, args.delay)
    parallel = measure(4, args.delay)
    print(
        json.dumps(
            {
                "independent_branches": 4,
                "wait_seconds_per_branch": args.delay,
                "serial_seconds": serial,
                "parallel_seconds": parallel,
                "speedup": round(serial / parallel, 2),
                "source_calls": 0,
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
