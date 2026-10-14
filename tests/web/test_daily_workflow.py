"""Calendar cutoffs and the update → checks → selected-strategy plan workflow."""

import asyncio
from dataclasses import replace
from datetime import datetime
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from unittest.mock import patch
import unittest
import pandas as pd

from ashare_alpha.web.core.sessions import market_clock, closed_session
from ashare_alpha.web.core.config_loader import get_strategy, _discover, list_strategies
from ashare_alpha.web.core.schema import StageConfig
from ashare_alpha.web.core.job_manager import JobManager
from ashare_alpha.web.core import checkpoint as cp


class SessionTests(unittest.TestCase):
    def test_shanghai_clock_does_not_use_local_timezone_or_unclosed_prices(self):
        from datetime import timezone

        self.assertEqual(
            market_clock(datetime(2026, 10, 13, 6, 59, tzinfo=timezone.utc))["cutoff"],
            "2026-10-12",
        )
        self.assertEqual(
            market_clock(datetime(2026, 10, 13, 7, 0, tzinfo=timezone.utc))["cutoff"],
            "2026-10-13",
        )

    def test_weekends_holidays_and_calendar_gaps_are_not_guessed(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "calendar.parquet"
            pd.DataFrame(
                {
                    "calendar_date": pd.date_range("2026-10-08", "2026-10-13"),
                    "is_trading_day": [True, True, False, False, True, True],
                }
            ).to_parquet(path, index=False)
            self.assertEqual(
                closed_session(path, now=datetime(2026, 10, 11, 17))["session"],
                "2026-10-09",
            )
            self.assertEqual(
                closed_session(path, now=datetime(2026, 10, 13, 14))["session"],
                "2026-10-12",
            )
            self.assertIsNone(
                closed_session(path, now=datetime(2026, 10, 14, 16))["session"]
            )

    def test_only_calendar_step_looks_ahead_while_prices_stop_at_closed_date(self):
        manager = JobManager()
        manager._target_date = "2026-10-12"
        cfg = get_strategy("demo")
        stages = {
            stage.id: manager._resolve_command(stage, cfg)
            for stage in cfg.update_pipeline
        }
        calendar = stages["trading_calendar"]
        self.assertEqual(calendar[calendar.index("--tasks") + 1], "calendar")
        self.assertEqual(calendar[calendar.index("--target") + 1], "2026-11-16")
        for name in ("daily_update",):
            command = stages[name]
            self.assertEqual(command[command.index("--target") + 1], "2026-10-12")

    def test_mock_strategy_requires_explicit_test_opt_in(self):
        with patch.dict("os.environ", {"ASHARE_WEBUI_TEST_STRATEGIES": "0"}):
            _discover()
            self.assertEqual(list_strategies(), ["demo"])
        with patch.dict("os.environ", {"ASHARE_WEBUI_TEST_STRATEGIES": "1"}):
            _discover()
            self.assertIn("mock_alpha", list_strategies())
        _discover()


class UpdateCommandTests(unittest.TestCase):
    def test_source_review_completes_workflow_without_claiming_quality_pass(self):
        from ashare_alpha.cli import daily_update
        from contextlib import redirect_stdout
        from io import StringIO

        with TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "data/state/acceptance.json"
            path.parent.mkdir(parents=True)
            record = {
                "status": "review",
                "target": "2026-10-12",
                "phase": "finished",
                "runs": [{"status": "success"}],
                "repeat": {"all_skipped": True},
                "issues": ["confirmed source gap"],
            }
            path.write_text(json.dumps(record))
            output = StringIO()
            with (
                patch.object(daily_update, "DATA_ROOT", root / "data"),
                patch.object(daily_update.subprocess, "call", return_value=1),
                redirect_stdout(output),
            ):
                rc = daily_update.main(["--data-only", "--target", "2026-10-12"])
                wrong_target = daily_update.main(
                    ["--data-only", "--target", "2026-10-13"]
                )
            self.assertEqual(rc, 0)
            self.assertEqual(wrong_target, 1)
            self.assertIn('"quality": "review"', output.getvalue())
            self.assertNotIn('"quality": "pass"', output.getvalue())


class WorkflowTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.jobs = patch.object(cp, "JOBS_BASE", self.root / "jobs")
        self.jobs.start()
        self.addCleanup(self.jobs.stop)
        self.manager = JobManager()
        self.target = patch(
            "ashare_alpha.web.core.job_manager.market_clock",
            return_value={"cutoff": "2026-10-12"},
        )
        self.target.start()
        self.addCleanup(self.target.stop)
        self.before = patch(
            "ashare_alpha.web.core.t_full._t_full", return_value="2026-10-12"
        )
        self.before.start()
        self.addCleanup(self.before.stop)

    def config(self, *, fail=False, slow=False):
        marker = str(self.root / "steps")
        code = f"from pathlib import Path; import sys,time; p=Path({marker!r}); p.write_text('update\\n'); "
        if fail:
            code += "sys.exit(2)"
        if slow:
            code += "time.sleep(30)"
        check = f"from pathlib import Path; p=Path({marker!r}); assert p.read_text()=='update\\n'; p.write_text(p.read_text()+'check\\n')"
        daily = f"from pathlib import Path; import sys; p=Path({marker!r}); assert p.read_text()=='update\\ncheck\\n'; assert sys.argv[1:]==['--date','2026-10-12']; p.write_text(p.read_text()+'plan\\n')"
        return replace(
            get_strategy("demo"),
            update_pipeline=[
                StageConfig(
                    "data",
                    "更新数据",
                    "",
                    [sys.executable, "-c", code, "--target", "{target_date}"],
                    ".",
                    60,
                )
            ],
            post_update_checks=[
                StageConfig(
                    "check", "策略核验", "", [sys.executable, "-c", check], ".", 20
                )
            ],
            daily_plan_command=[sys.executable, "-c", daily],
            daily_asof_argument="--date",
        )

    async def test_combined_workflow_orders_steps_and_pins_the_signal_date(self):
        with patch.object(self.manager, "_signal_date", return_value="2026-10-12"):
            response = await self.manager.start_update(
                self.config(), generate_plan=True
            )
            await asyncio.wait_for(self.manager._task, timeout=10)
        self.assertEqual(response["target_date"], "2026-10-12")
        self.assertEqual((self.root / "steps").read_text(), "update\ncheck\nplan\n")
        state = self.manager.get_state()
        self.assertEqual(state["status"], "done")
        self.assertEqual(state["completed_steps"], 3)
        self.assertEqual(
            cp.load_job("demo", response["job_id"])["strategy_id"],
            "demo",
        )

    async def test_preparation_date_is_resolved_after_update_not_before(self):
        observed = {"ready": False}

        def signal_date(cfg):
            # Update subprocess has already published its marker by the time we inspect coverage.
            self.assertEqual((self.root / "steps").read_text(), "update\ncheck\n")
            observed["ready"] = True
            return "2026-10-12"

        with patch.object(self.manager, "_signal_date", side_effect=signal_date):
            await self.manager.start_update(self.config(), generate_plan=True)
            await asyncio.wait_for(self.manager._task, timeout=10)
        self.assertTrue(observed["ready"])
        self.assertEqual(self.manager.get_state()["status"], "done")

    async def test_failed_update_never_runs_checks_or_plan(self):
        await self.manager.start_update(self.config(fail=True), generate_plan=True)
        await asyncio.wait_for(self.manager._task, timeout=10)
        self.assertEqual(self.manager.get_state()["status"], "failed")
        self.assertEqual((self.root / "steps").read_text(), "update\n")
        checkpoint = cp.read_checkpoint("demo")
        self.assertEqual(checkpoint["target_date"], "2026-10-12")
        self.assertEqual(
            {s["name"]: s["status"] for s in checkpoint["steps"]}["daily_plan"],
            "pending",
        )

    async def test_failed_verification_never_runs_plan(self):
        cfg = self.config()
        cfg.post_update_checks = [
            StageConfig(
                "check",
                "策略核验",
                "",
                [sys.executable, "-c", "import sys; sys.exit(2)"],
                ".",
                20,
            )
        ]
        await self.manager.start_update(cfg, generate_plan=True)
        await asyncio.wait_for(self.manager._task, timeout=10)
        self.assertEqual(self.manager.get_state()["status"], "failed")
        self.assertEqual((self.root / "steps").read_text(), "update\n")
        self.assertEqual(
            self.manager.get_state()["steps_status"]["daily_plan"], "pending"
        )

    async def test_reusing_data_still_runs_verification_before_plan(self):
        (self.root / "steps").write_text("update\n")
        with patch.object(self.manager, "_signal_date", return_value="2026-10-12"):
            await self.manager.start_daily(self.config())
            await asyncio.wait_for(self.manager._task, timeout=10)
        self.assertEqual((self.root / "steps").read_text(), "update\ncheck\nplan\n")
        self.assertEqual(self.manager.get_state()["status"], "done")
        self.assertEqual(self.manager.get_state()["completed_steps"], 2)

    async def test_retry_rechecks_verification_after_same_day_data_update(self):
        cp.write_checkpoint(
            "demo",
            "earlier",
            {"data": "completed", "check": "completed", "daily_plan": "failed"},
            "2026-10-12",
            target_date="2026-10-12",
        )
        with patch.object(self.manager, "_signal_date", return_value="2026-10-12"):
            await self.manager.start_update(
                self.config(), resume=True, generate_plan=True
            )
            await asyncio.wait_for(self.manager._task, timeout=10)
        self.assertEqual((self.root / "steps").read_text(), "update\ncheck\nplan\n")
        self.assertEqual(self.manager.get_state()["status"], "done")

    async def test_stale_checkpoint_rechecks_market_data(self):
        cp.write_checkpoint(
            "demo",
            "yesterday",
            {"data": "completed", "check": "completed"},
            "2026-10-09",
            target_date="2026-10-09",
        )
        with patch.object(self.manager, "_signal_date", return_value="2026-10-12"):
            await self.manager.start_update(
                self.config(), resume=True, generate_plan=True
            )
            await asyncio.wait_for(self.manager._task, timeout=10)
        self.assertEqual((self.root / "steps").read_text(), "update\ncheck\nplan\n")

    async def test_insufficient_updated_coverage_blocks_plan(self):
        with patch.object(
            self.manager, "_signal_date", side_effect=ValueError("行情尚未覆盖")
        ):
            await self.manager.start_update(self.config(), generate_plan=True)
            await asyncio.wait_for(self.manager._task, timeout=10)
        self.assertEqual(self.manager.get_state()["status"], "failed")
        self.assertEqual((self.root / "steps").read_text(), "update\ncheck\n")

    async def test_concurrent_start_is_rejected_and_cancel_is_never_success(self):
        await self.manager.start_update(self.config(slow=True), generate_plan=True)
        for _ in range(100):
            if (self.root / "steps").exists():
                break
            await asyncio.sleep(0.02)
        rejection = await self.manager.start_update(self.config(), generate_plan=True)
        self.assertIn("error", rejection)
        self.assertTrue(await self.manager.cancel())
        await asyncio.wait_for(self.manager._task, timeout=10)
        self.assertEqual(self.manager.get_state()["status"], "cancelled")
        self.assertEqual((self.root / "steps").read_text(), "update\n")

    async def test_plan_guard_uses_exchange_calendar_and_audited_coverage(self):
        calendar = self.root / "calendar.parquet"
        pd.DataFrame(
            {
                "calendar_date": pd.date_range("2026-10-09", "2026-10-13"),
                "is_trading_day": [True, False, False, True, True],
            }
        ).to_parquet(calendar, index=False)
        self.manager._target_date = "2026-10-12"
        with patch(
            "ashare_alpha.web.core.job_manager.get_strategy_paths",
            return_value={"calendar": calendar},
        ):
            self.assertEqual(
                self.manager._signal_date(get_strategy("demo")),
                "2026-10-12",
            )
            with (
                patch(
                    "ashare_alpha.web.core.t_full._t_full", return_value="2026-10-09"
                ),
                self.assertRaises(ValueError),
            ):
                self.manager._signal_date(get_strategy("demo"))


if __name__ == "__main__":
    unittest.main()
