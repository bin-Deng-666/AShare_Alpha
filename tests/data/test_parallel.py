"""Scheduling regressions: overlap, resource limits, dependency and publication barriers."""

from contextlib import ExitStack
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import threading
import time
import unittest
from unittest.mock import patch

from ashare_alpha.data.pipeline import runner
from ashare_alpha.data.pipeline.tasks import Task


def process_job(name, config, codes, workers, force):
    """An actual spawned process writes only inside its test directory."""
    root = Path(config["test_root"])
    started = time.monotonic()
    (root / f"{name}.started").touch()
    if name in ("a", "b"):
        deadline = time.monotonic() + 8
        while not all((root / f"{code}.started").exists() for code in ("a", "b")):
            if time.monotonic() > deadline:
                raise RuntimeError("ready tasks did not overlap")
            time.sleep(0.01)
        time.sleep(0.05)
    if name == "c" and not (root / "a.finished").exists():
        raise RuntimeError("dependent started before producer finished")
    (root / f"{name}.parquet").write_bytes(name.encode())
    ended = time.monotonic()
    (root / f"{name}.finished").write_text(
        json.dumps({"pid": os.getpid(), "start": started, "end": ended})
    )
    return {
        "status": "success",
        "detail": {"worker_pid": os.getpid()},
        "seconds": ended - started,
    }


def handoff_process_job(name, config, codes, workers, force):
    root = Path(config["test_root"])
    channel = config.get("_ready_queue")
    if name == "announcements":
        channel.put({"codes": ["000001"]})
        deadline = time.monotonic() + 8
        while not (root / "financials.started").exists():
            if time.monotonic() > deadline:
                return {
                    "status": "failed",
                    "error": "consumer did not overlap",
                    "seconds": 0,
                }
            time.sleep(0.01)
        (root / "announcements.finished").touch()
        channel.put({"end": True, "failed": []})
    elif name == "financials":
        assert channel.get(timeout=8)["codes"] == ["000001"]
        (root / "financials.started").touch()
        assert channel.get(timeout=8)["end"]
    (root / f"{name}.parquet").write_bytes(name.encode())
    return {"status": "success", "detail": {}, "seconds": 0}


class ParallelTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def run_graph(
        self, tasks, handler=None, *, parallel_tasks=2, skip=False, real_processes=False
    ):
        def execute(name, *args):
            detail = handler(name) if handler else {}
            (self.root / f"{name}.parquet").write_bytes(name.encode())
            return detail or {}

        with ExitStack() as stack:
            stack.enter_context(patch.object(runner, "STATE_ROOT", self.root / "state"))
            stack.enter_context(
                patch.object(runner, "select_tasks", return_value=tasks)
            )
            stack.enter_context(
                patch.object(
                    runner,
                    "build_plan",
                    side_effect=lambda *a, **kw: [
                        {
                            "task": kw["only_task"],
                            "action": "skip" if skip else "refresh",
                            "input": "sig",
                        }
                    ],
                )
            )
            stack.enter_context(
                patch.object(
                    runner,
                    "task_paths",
                    side_effect=lambda task: ([], [self.root / f"{task.name}.parquet"]),
                )
            )
            if real_processes:
                stack.enter_context(patch.object(runner, "_execute_task", process_job))
            else:
                stack.enter_context(
                    patch.object(
                        runner,
                        "ProcessPoolExecutor",
                        side_effect=lambda max_workers, **kw: ThreadPoolExecutor(
                            max_workers=max_workers
                        ),
                    )
                )
                stack.enter_context(
                    patch.object(runner, "execute", side_effect=execute)
                )
            return runner.run_update(
                {"end_date": "2026-10-10", "test_root": str(self.root)},
                parallel_tasks=parallel_tasks,
            )

    def test_ready_roots_overlap_and_join_waits_for_both(self):
        barrier = threading.Barrier(2, timeout=3)
        finished = set()
        lock = threading.Lock()
        tasks = [
            Task("a", (), (), ()),
            Task("b", (), (), ()),
            Task("c", ("a", "b"), (), ()),
        ]

        def execute(name):
            if name in ("a", "b"):
                barrier.wait()
                with lock:
                    finished.add(name)
            else:
                self.assertEqual(finished, {"a", "b"})

        result = self.run_graph(tasks, execute)
        self.assertEqual(result["status"], "success")

    def test_same_resource_is_serial_and_other_provider_can_overlap(self):
        tasks = [
            Task("a", (), (), (), resources=("vendor",)),
            Task("b", (), (), (), resources=("vendor",)),
            Task("c", (), (), ()),
        ]
        barrier = threading.Barrier(2, timeout=3)
        active = set()
        lock = threading.Lock()

        def execute(name):
            with lock:
                if name in ("a", "b"):
                    self.assertFalse({"a", "b"} & active)
                active.add(name)
            if name in ("a", "c"):
                barrier.wait()
            with lock:
                active.remove(name)

        result = self.run_graph(tasks, execute, parallel_tasks=3)
        self.assertEqual(result["status"], "success")

    def test_parallel_task_limit_is_enforced(self):
        active = 0
        peak = 0
        lock = threading.Lock()
        tasks = [Task(str(i), (), (), ()) for i in range(5)]

        def execute(name):
            nonlocal active, peak
            with lock:
                active += 1
                peak = max(active, peak)
            time.sleep(0.04)
            with lock:
                active -= 1

        result = self.run_graph(tasks, execute, parallel_tasks=2)
        self.assertEqual(result["status"], "success")
        self.assertEqual(peak, 2)

    def test_catalog_waits_for_optional_sources_too(self):
        tasks = [
            Task("a", (), (), ()),
            Task("catalog", ("a",), (), ()),
            Task("optional", (), (), ()),
        ]
        finished = set()

        def execute(name):
            if name == "optional":
                time.sleep(0.06)
            if name == "catalog":
                self.assertEqual(finished, {"a", "optional"})
            finished.add(name)

        result = self.run_graph(tasks, execute)
        self.assertEqual(result["status"], "success")
        self.assertEqual(result["results"][-1]["task"], "catalog")

    def test_catalog_skips_after_optional_noops_without_deadlock(self):
        tasks = [
            Task("a", (), (), ()),
            Task("catalog", ("a",), (), ()),
            Task("optional", (), (), ()),
        ]
        result = self.run_graph(
            tasks, handler=lambda name: self.fail("no source allowed"), skip=True
        )
        self.assertEqual(result["status"], "success")
        self.assertTrue(all(item["status"] == "skipped" for item in result["results"]))

    def test_throttle_preserves_minimum_spacing_without_extra_padding(self):
        from ashare_alpha.data import runtime

        clock = [0.0]
        starts = []

        def sleep(seconds):
            clock[0] += seconds

        with (
            patch.object(runtime.time, "monotonic", side_effect=lambda: clock[0]),
            patch.object(runtime.time, "sleep", side_effect=sleep),
        ):
            throttle = runtime.Throttle(0.25)
            for _ in range(3):
                throttle.wait()
                starts.append(clock[0])
                clock[0] += 0.02
        self.assertEqual(starts, [0.0, 0.25, 0.50])
        self.assertTrue(all(b - a >= 0.25 for a, b in zip(starts, starts[1:])))

    def test_scheduler_starts_financial_consumer_before_announcement_completion(self):
        tasks = [
            Task("securities", (), (), ()),
            Task("announcements", ("securities",), (), ()),
            Task("financials", ("securities", "announcements"), (), ()),
        ]

        def plan(*args, **kw):
            value = (
                "final" if (self.root / "announcements.finished").exists() else "moving"
            )
            return [{"task": kw["only_task"], "action": "refresh", "input": value}]

        with (
            patch.object(runner, "STATE_ROOT", self.root / "state"),
            patch.object(runner, "select_tasks", return_value=tasks),
            patch.object(runner, "build_plan", side_effect=plan),
            patch.object(
                runner,
                "task_paths",
                side_effect=lambda task: ([], [self.root / f"{task.name}.parquet"]),
            ),
            patch.object(runner, "_execute_task", handoff_process_job),
        ):
            result = runner.run_update(
                {"end_date": "2026-10-10", "test_root": str(self.root)},
                parallel_tasks=2,
            )
        self.assertEqual(result["status"], "success")
        receipt = json.loads((self.root / "state/tasks/financials.json").read_text())
        self.assertEqual(receipt["input"], "final")
        self.assertTrue((self.root / "financials.started").exists())

    def test_actual_spawned_processes_overlap_and_keep_sdk_state_isolated(self):
        tasks = [
            Task("a", (), (), ()),
            Task("b", (), (), ()),
            Task("c", ("a",), (), ()),
        ]
        result = self.run_graph(tasks, real_processes=True)
        self.assertEqual(result["status"], "success")
        records = {
            name: json.loads((self.root / f"{name}.finished").read_text())
            for name in ("a", "b", "c")
        }
        self.assertNotEqual(records["a"]["pid"], records["b"]["pid"])
        self.assertLess(
            max(records["a"]["start"], records["b"]["start"]),
            min(records["a"]["end"], records["b"]["end"]),
        )
        self.assertGreaterEqual(records["c"]["start"], records["a"]["end"])


if __name__ == "__main__":
    unittest.main()
