"""Offline correctness tests for incremental updates and publication."""

from __future__ import annotations
import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch
from concurrent.futures import ThreadPoolExecutor

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from ashare_alpha.data.storage import atomic_parquet, writer_lock
from ashare_alpha.data.parquet_metadata import latest_date
from ashare_alpha.data.assets.publish import publish_by_security
from ashare_alpha.data.assets import incremental
from ashare_alpha.data.pipeline import planner, runner
from ashare_alpha.data.pipeline.tasks import Task
from ashare_alpha.data.sources import corporate_actions, equity_structure


class UpdateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        pool = patch.object(
            runner,
            "ProcessPoolExecutor",
            side_effect=lambda max_workers, **kw: ThreadPoolExecutor(
                max_workers=max_workers
            ),
        )
        pool.start()
        self.addCleanup(pool.stop)

    def tearDown(self):
        self.tmp.cleanup()

    def test_footer_max_all_rowgroups_and_missing_statistics(self):
        data = pd.DataFrame(
            {"date": pd.to_datetime(["2024-01-09", "2024-01-02", "2024-01-05"])}
        )
        for stats in (True, False):
            path = self.root / f"{stats}.parquet"
            pq.write_table(
                pa.Table.from_pandas(data),
                path,
                row_group_size=1,
                write_statistics=stats,
            )
            self.assertEqual(latest_date(path), pd.Timestamp("2024-01-09"))
            data2 = pd.DataFrame({"date": pd.to_datetime(["2025-02-03"])})
            atomic_parquet(data2, path)
            self.assertEqual(latest_date(path), pd.Timestamp("2025-02-03"))

    def test_no_change_keeps_mtime_and_empty_market_rejected(self):
        data = pd.DataFrame({"code": ["000001"], "value": [1.0]})
        publish_by_security(data, ["000001", "600000"], self.root)
        path = self.root / "000001.parquet"
        before = path.stat().st_mtime_ns
        result = publish_by_security(data, ["000001", "600000"], self.root)
        self.assertEqual(result["changed_codes"], [])
        self.assertEqual(before, path.stat().st_mtime_ns)
        with self.assertRaises(RuntimeError):
            publish_by_security(data.iloc[:0], ["000001"], self.root)
        self.assertEqual(before, path.stat().st_mtime_ns)

    def test_writer_lock_rejects_overlap(self):
        path = self.root / "writer.lock"
        with writer_lock(path):
            with self.assertRaises(RuntimeError):
                with writer_lock(path):
                    pass
        with writer_lock(path):
            pass

    def test_incremental_only_rebuilds_changed_security(self):
        for code in ("000001", "000002"):
            atomic_parquet(
                pd.DataFrame({"value": [1]}), self.root / f"in/{code}.parquet"
            )
        calls = []

        def builder(config, codes, force, on_code_done):
            calls.append(codes)
            for code in codes:
                atomic_parquet(
                    pd.read_parquet(self.root / f"in/{code}.parquet"),
                    self.root / f"out/{code}.parquet",
                )
                on_code_done(code)

        with (
            patch.object(incremental, "PARQUET", self.root),
            patch.object(incremental, "STATE", self.root / "state"),
        ):
            args = (
                "test",
                builder,
                {},
                ["000001", "000002"],
                ["in/{code}.parquet"],
                "out/{code}.parquet",
            )
            incremental.rebuild_changed(*args)
            self.assertEqual(incremental.rebuild_changed(*args)["processed"], 0)
            atomic_parquet(
                pd.DataFrame({"value": [2]}), self.root / "in/000002.parquet"
            )
            self.assertEqual(
                incremental.rebuild_changed(*args)["changed_codes"], ["000002"]
            )
        self.assertEqual(calls, [["000001", "000002"], ["000002"]])

    def test_failed_dependency_blocks_child_but_not_independent_task(self):
        tasks = (
            Task("a", (), (), ()),
            Task("b", ("a",), (), ()),
            Task("c", (), (), ()),
        )
        calls = []

        def execute(name, *args):
            calls.append(name)
            if name == "a":
                raise RuntimeError("source failed")
            return {}

        def plan(*args, **kwargs):
            return [
                {"task": n, "action": "refresh", "input": "v"} for n in ("a", "b", "c")
            ]

        out = self.root / "output"
        out.touch()
        with (
            patch.object(runner, "STATE_ROOT", self.root),
            patch.object(runner, "select_tasks", return_value=tasks),
            patch.object(runner, "build_plan", side_effect=plan),
            patch.object(runner, "execute", side_effect=execute),
            patch.object(runner, "task_paths", return_value=([], [out])),
        ):
            result = runner.run_update({"end_date": "2024-01-09"})
        self.assertCountEqual(calls, ["a", "c"])
        self.assertEqual(
            {r["task"]: r["status"] for r in result["results"]},
            {"a": "failed", "b": "blocked", "c": "success"},
        )
        import json

        state = json.loads((self.root / "tasks/a.json").read_text())
        self.assertEqual(state["status"], "failed")
        self.assertNotIn("checked_through", state)

    def test_incomplete_market_pagination_is_failure(self):
        from types import SimpleNamespace

        payload = {
            "success": True,
            "result": {"count": 2, "pages": 1, "data": [{"SECURITY_CODE": "000001"}]},
        }
        response = SimpleNamespace(raise_for_status=lambda: None, json=lambda: payload)
        session = SimpleNamespace(get=lambda *a, **k: response)
        throttle = SimpleNamespace(wait=lambda: None)
        with self.assertRaisesRegex(RuntimeError, "Incomplete"):
            corporate_actions.fetch_corporate_actions(session, None, throttle)
        with self.assertRaisesRegex(RuntimeError, "Incomplete"):
            equity_structure.fetch_equity_structure(session, None, throttle)

    def test_core_coverage_rejects_missing_security(self):
        from ashare_alpha.data.research.coverage import validate_core_coverage

        atomic_parquet(
            pd.DataFrame({"code": ["000001"]}),
            self.root / "security_master/security_master.parquet",
        )
        with self.assertRaisesRegex(RuntimeError, "missing 1 securities"):
            validate_core_coverage(self.root)

    def test_second_successful_run_is_noop(self):
        task = Task("sample", (), (), ("sample/*.parquet",), True)

        def build(*args):
            atomic_parquet(pd.DataFrame({"value": [1]}), self.root / "sample/a.parquet")
            return {}

        config = {"end_date": "2024-01-09"}
        with (
            patch.object(runner, "STATE_ROOT", self.root / "state"),
            patch.object(planner, "STATE_ROOT", self.root / "state"),
            patch.object(planner, "PARQUET_ROOT", self.root),
            patch.object(runner, "select_tasks", return_value=(task,)),
            patch.object(planner, "select_tasks", return_value=(task,)),
            patch.object(runner, "execute", side_effect=build) as execute,
        ):
            runner.run_update(config)
            second = runner.run_update(config)
        self.assertEqual(execute.call_count, 1)
        self.assertEqual(second["results"][0]["status"], "skipped")

    def test_source_resume_only_retries_failure(self):
        from ashare_alpha.data.pipeline import source_receipts

        calls = []

        def fetch(config, codes, max_workers, on_code_done):
            calls.append(codes)
            for code in codes:
                if code == "000002" and len(calls) == 1:
                    continue
                atomic_parquet(
                    pd.DataFrame({"v": [1]}), self.root / f"financials/{code}.parquet"
                )
                on_code_done(code)

        universe = pd.DataFrame({"code": ["000001", "000002"]})
        cfg = {"end_date": "2024-01-09"}
        with (
            patch.object(source_receipts, "STATE_ROOT", self.root / "state"),
            patch.object(source_receipts, "PARQUET_ROOT", self.root),
            patch(
                "ashare_alpha.data.incremental_files._listed_universe",
                return_value=universe,
            ),
        ):
            with self.assertRaises(RuntimeError):
                source_receipts.refresh_securities(
                    "financials", fetch, cfg, None, workers=1
                )
            source_receipts.refresh_securities(
                "financials", fetch, cfg, None, workers=1
            )
        self.assertEqual(calls, [["000001", "000002"], ["000002"]])

    def test_catalog_failure_preserves_previous_database(self):
        from ashare_alpha.data.research import catalog

        path = self.root / "quant.duckdb"
        original = b"prior catalog"
        path.write_bytes(original)
        with (
            patch.object(catalog, "CATALOG", path),
            patch.object(
                catalog,
                "_catalog_daily_view_sql",
                side_effect=RuntimeError("bad source schema"),
            ),
        ):
            with self.assertRaises(RuntimeError):
                catalog.build_catalog({})
        self.assertEqual(path.read_bytes(), original)
        self.assertFalse(list(self.root.glob("*.tmp")))

    def test_stale_calendar_is_not_silently_latest(self):
        from ashare_alpha.data import calendar

        atomic_parquet(
            pd.DataFrame(
                {"calendar_date": pd.to_datetime(["2024-01-05"]), "is_trading_day": [1]}
            ),
            self.root / "reference/trade_calendar.parquet",
        )
        with patch.object(calendar, "PARQUET", self.root):
            with self.assertRaises(RuntimeError):
                calendar._target_asof({"end_date": "2024-01-10"})

    def test_corporate_distribution_batch_request_and_output_parity(self):
        master = self.root / "security_master/security_master.parquet"
        atomic_parquet(pd.DataFrame({"code": ["000001", "600000"]}), master)
        frame = pd.DataFrame(
            {
                "code": ["000001", "600000"],
                "ex_date": pd.to_datetime(["2024-01-02", "2024-01-03"]),
                "value": [1.0, 2.0],
            }
        )
        cfg = {
            "eastmoney_min_interval_seconds": 0,
            "retries": 1,
            "retry_base_seconds": 0,
        }
        with (
            patch.object(corporate_actions, "PARQUET", self.root),
            patch.object(corporate_actions, "STATE", self.root / "state"),
            patch.object(
                corporate_actions, "fetch_corporate_actions", return_value=frame
            ) as fetch,
        ):
            corporate_actions.build_corporate_actions(cfg, force=True)
        self.assertEqual(fetch.call_count, 1)
        self.assertIsNone(fetch.call_args.args[1])
        for code in master.parent.parent.joinpath("corporate_actions").glob(
            "*.parquet"
        ):
            pd.testing.assert_frame_equal(
                pd.read_parquet(code),
                frame[frame.code == code.stem].reset_index(drop=True),
            )


if __name__ == "__main__":
    unittest.main()
