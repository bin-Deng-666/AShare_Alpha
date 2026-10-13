"""Publication interruption, dates and service termination are observable contracts."""

import asyncio
from dataclasses import replace
import os
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

import pandas as pd
from ashare_alpha.plans import current_plan, read_index, report_dates
from cli.utils.sink import write_run_bundle
from cli.utils.calendar import next_trade_date
from webui.app.core import checkpoint as cp
from webui.app.core.config_loader import get_strategy, load_strategy, STRATEGIES_DIR
from webui.app.core.job_manager import JobManager
from webui.app.core.schema import StageConfig


class PlanPublicationTests(unittest.TestCase):
    def payload(self, asof="2024-01-31", execution="2024-02-01"):
        return {
            "strategy": "demo",
            "asof": asof,
            "exec_date": execution,
            "buy": [{"code": "000001", "budget": 12000}],
            "reduce": [],
            "positions": [{"code": "600000", "shares": 100}],
        }

    def test_failed_write_keeps_orders_positions_and_history_at_prior_commit(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            old = self.payload()
            write_run_bundle(root / "20240201", old)
            index = (root / "index.json").read_bytes()
            new = self.payload("2024-02-01", "2024-02-02")
            with patch.object(
                pd.DataFrame, "to_csv", side_effect=OSError("disk write interrupted")
            ):
                with self.assertRaises(OSError):
                    write_run_bundle(root / "20240202", new)
            self.assertEqual((root / "index.json").read_bytes(), index)
            self.assertEqual(current_plan(root), old)
            self.assertEqual(report_dates(root), ["20240201"])

    def test_failed_commit_hides_complete_uncommitted_revision(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            old = self.payload()
            write_run_bundle(root / "20240201", old)
            with patch(
                "ashare_alpha.plans.atomic_json",
                side_effect=OSError("commit interrupted"),
            ):
                with self.assertRaises(OSError):
                    write_run_bundle(
                        root / "20240202", self.payload("2024-02-01", "2024-02-02")
                    )
            self.assertEqual(current_plan(root), old)
            self.assertEqual(report_dates(root), ["20240201"])

    def test_invalid_execution_or_budget_never_publishes(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            for value in (
                self.payload(execution="2024-01-31"),
                {**self.payload(), "buy": [{"code": "000001", "budget": -1}]},
            ):
                with self.assertRaises(ValueError):
                    write_run_bundle(root / "20240201", value)
            self.assertFalse((root / "index.json").exists())

    def test_format_version_and_read_only_discovery(self):
        with TemporaryDirectory() as directory:
            root = Path(directory) / "absent"
            self.assertEqual(report_dates(root), [])
            self.assertIsNone(read_index(root))
            self.assertFalse(root.exists())
            root.mkdir()
            (root / "index.json").write_text('{"format_version": 999}')
            with self.assertRaises(ValueError):
                current_plan(root)


class CalendarAndConfigurationTests(unittest.TestCase):
    def test_uncovered_calendar_does_not_invent_national_day_execution(self):
        with (
            TemporaryDirectory() as directory,
            patch("cli.utils.calendar.PARQUET_ROOT", Path(directory)),
        ):
            self.assertIsNone(next_trade_date("2026-09-30"))
            path = Path(directory) / "reference/trade_calendar.parquet"
            path.parent.mkdir()
            dates = pd.date_range("2026-09-30", "2026-10-09")
            pd.DataFrame(
                {
                    "calendar_date": dates,
                    "is_trading_day": [True] + [False] * 8 + [True],
                }
            ).to_parquet(path)
            self.assertEqual(next_trade_date("2026-09-30"), "2026-10-09")

    def test_configuration_errors_identify_file_and_reject_unknown_fields(self):
        import yaml

        source = yaml.safe_load((STRATEGIES_DIR / "demo/strategy.yaml").read_text())
        with TemporaryDirectory() as directory:
            path = Path(directory) / "strategy.yaml"
            path.write_text(yaml.safe_dump({**source, "unknown_setting": True}))
            with self.assertRaisesRegex(ValueError, "unknown fields"):
                load_strategy(path)
            source["update_pipeline"][1]["id"] = source["update_pipeline"][0]["id"]
            path.write_text(yaml.safe_dump(source))
            with self.assertRaisesRegex(ValueError, "duplicate"):
                load_strategy(path)

    def test_corrupt_checkpoint_is_visible_and_reads_do_not_create_directories(self):
        with (
            TemporaryDirectory() as directory,
            patch.object(cp, "JOBS_BASE", Path(directory) / "jobs"),
        ):
            self.assertIsNone(cp.find_latest_job("demo"))
            self.assertFalse(cp.JOBS_BASE.exists())
            cp.write_checkpoint("demo", "test", {"data": "pending"}, None)
            (cp.JOBS_BASE / "demo/update_checkpoint.json").write_text('{"partial":')
            self.assertTrue(cp.read_checkpoint("demo")["corrupt"])
            self.assertTrue(cp.has_incomplete_update("demo"))


class ShutdownTests(unittest.IsolatedAsyncioTestCase):
    async def test_shutdown_terminates_process_and_persists_interrupted_state(self):
        with (
            TemporaryDirectory() as directory,
            patch.object(cp, "JOBS_BASE", Path(directory) / "jobs"),
            patch("webui.app.core.t_full._t_full", return_value=None),
        ):
            root = Path(directory)
            marker = root / "ready"
            script = f"from pathlib import Path;import time;Path({str(marker)!r}).write_text('ready');time.sleep(30)"
            cfg = replace(
                get_strategy("demo"),
                update_pipeline=[
                    StageConfig(
                        "data", "data", "", [sys.executable, "-c", script], ".", 60
                    )
                ],
                post_update_checks=[],
            )
            manager = JobManager()
            started = await manager.start_update(cfg)
            try:
                for _ in range(100):
                    if marker.exists():
                        break
                    await asyncio.sleep(0.02)
                self.assertTrue(marker.exists())
                process = manager.process
                await manager.shutdown()
                self.assertIsNotNone(process.returncode)
                with self.assertRaises(ProcessLookupError):
                    os.kill(process.pid, 0)
                self.assertEqual(manager.get_state()["status"], "interrupted")
                self.assertEqual(
                    cp.load_job(cfg.strategy_id, started["job_id"])["status"],
                    "interrupted",
                )
                self.assertTrue(cp.has_incomplete_update(cfg.strategy_id))
            finally:
                await manager.shutdown()

    async def test_shutdown_before_task_starts_is_also_persisted(self):
        with (
            TemporaryDirectory() as directory,
            patch.object(cp, "JOBS_BASE", Path(directory) / "jobs"),
            patch("webui.app.core.t_full._t_full", return_value=None),
        ):
            manager = JobManager()
            cfg = get_strategy("demo")
            job = await manager.start_update(cfg)
            await manager.shutdown()
            self.assertEqual(manager.get_state()["status"], "interrupted")
            self.assertEqual(
                cp.load_job(cfg.strategy_id, job["job_id"])["status"], "interrupted"
            )


if __name__ == "__main__":
    unittest.main()
