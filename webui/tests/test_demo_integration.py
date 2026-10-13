"""The renamed strategy, model orders and read-only console presentation agree."""

import json
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
import unittest

from webui.app.core.config_loader import get_strategy, get_strategy_paths
from webui.app.core.job_manager import JobManager, PYTHON
from webui.app.api import routes
from cli.utils.api import predict_next


class DemoIntegrationTests(unittest.TestCase):
    def test_config_commands_use_the_server_interpreter_and_new_name(self):
        cfg = get_strategy("demo")
        manager = JobManager()
        self.assertEqual(PYTHON, sys.executable)
        self.assertEqual(
            manager._resolve_daily_command(cfg),
            [sys.executable, "-m", "cli.run", "demo"],
        )
        for stage in cfg.all_stages:
            command = manager._resolve_command(stage, cfg)
            self.assertEqual(command[0], sys.executable)
            self.assertNotIn("/usr/bin/python3", command)
        self.assertTrue(get_strategy_paths(cfg)["strat_root"].is_dir())
        self.assertEqual(routes.list_strategies(), ["demo"])

    def test_cli_keeps_class_orders_and_passes_initial_capital(self):
        captured = {}
        orders = {
            "source": "strategy_class_model_account",
            "buy": [{"code": "000001", "budget": 1234}],
            "sell": [],
        }

        def predictor(asof=None, *, rebuild=False, panel=None, capital=None):
            captured["capital"] = capital
            return {
                "strategy": "demo",
                "asof": "2024-01-31",
                "exec_date": "2024-02-01",
                "orders": orders,
            }

        with patch("cli.utils.api.get_predictor", return_value=predictor):
            result = predict_next("demo", capital=30000, write=False)
        self.assertEqual(captured["capital"], 30000)
        self.assertIs(result["orders"], orders)
        self.assertNotIn("config", sys.modules)
        self.assertNotIn("engine", sys.modules)

    def test_positions_are_saved_model_snapshots_without_catalog_revaluation(self):
        cfg = get_strategy("demo")
        adapter = routes._load_adapter(cfg.strategy_id)
        with TemporaryDirectory() as directory:
            path = Path(directory) / "plan.json"
            position = {
                "code": "000001",
                "shares": 123,
                "avg_cost": 10.1,
                "current_price": 10.9,
            }
            path.write_text(
                json.dumps(
                    {
                        "positions": [position],
                        "asof": "2024-01-31",
                        "run_id": "run1",
                        "note": "模型账户",
                    }
                )
            )
            with patch.object(
                adapter, "get_strategy_paths", return_value={"ledger": path}
            ):
                parsed = adapter.parse_positions(cfg)
            self.assertEqual(parsed["positions"], [position])
            self.assertEqual(parsed["run_id"], "run1")
            self.assertEqual(parsed["t_full"], "2024-01-31")

    def test_history_preserves_actual_orders_and_input_identity(self):
        cfg = get_strategy("demo")
        adapter = routes._load_adapter(cfg.strategy_id)
        with TemporaryDirectory() as directory:
            root = Path(directory)
            report = root / "20240201"
            report.mkdir()
            payload = {
                "strategy": "demo",
                "asof": "2024-01-31",
                "exec_date": "2024-02-01",
                "input_fingerprint": "content1",
                "buy": [{"code": "000001", "budget": 1234}],
                "reduce": [{"code": "600000", "quantity": 200}],
                "meta": {"mkt_regime": "expand"},
            }
            (report / "result.json").write_text(json.dumps(payload))
            (report / "report.md").write_text("model plan")
            with patch.object(
                adapter, "get_strategy_paths", return_value={"output": root}
            ):
                result = adapter.parse_daily_latest(cfg)
                unsafe = adapter.parse_history_detail(cfg, "../outside")
            self.assertEqual(result["result"], payload)
            self.assertIsNone(unsafe["result"])

    def test_unknown_next_session_is_preserved_in_cli_reports(self):
        from cli.utils.sink import write_run_bundle

        payload = {
            "strategy": "demo",
            "asof": "2026-10-09",
            "exec_date": None,
            "exec_hint": "next_session_unconfirmed",
            "buy": [],
            "reduce": [],
            "positions": [{"code": "000001", "shares": 123}],
            "warnings": ["交易日历尚未覆盖下一交易日"],
            "input_fingerprint": "input1",
        }

        def predictor(asof=None, *, rebuild=False, panel=None):
            return dict(payload)

        with patch("cli.utils.api.get_predictor", return_value=predictor):
            result = predict_next("demo", write=False)
        self.assertIsNone(result["exec_date"])
        with TemporaryDirectory() as directory:
            files = write_run_bundle(Path(directory), result)
            saved = json.loads(files["result_json"].read_text())
            self.assertEqual(saved["positions"], payload["positions"])
            self.assertIsNone(saved["exec_date"])
            self.assertIn("待日历确认", files["report_md"].read_text())

    def test_combined_workflow_endpoint_is_strategy_scoped(self):
        from fastapi.testclient import TestClient
        from unittest.mock import AsyncMock
        from webui.server import app

        with TestClient(app) as client:
            with patch.object(
                routes.jm,
                "start_update",
                new=AsyncMock(return_value={"job_id": "prepare-test"}),
            ) as start:
                response = client.post(
                    "/api/strategies/demo/prepare", json={"resume": True}
                )
                self.assertEqual(response.status_code, 200)
                cfg = start.call_args.args[0]
                self.assertEqual(cfg.strategy_id, "demo")
                self.assertEqual(
                    start.call_args.kwargs, {"resume": True, "generate_plan": True}
                )
            self.assertEqual(
                client.post("/api/strategies/missing/prepare", json={}).status_code, 404
            )
            self.assertEqual(
                client.post("/api/strategies/demo/prepare", json=[]).status_code,
                422,
            )
            with patch.object(
                routes.jm,
                "start_update",
                new=AsyncMock(return_value={"error": "已有任务", "job_id": "old"}),
            ):
                self.assertEqual(
                    client.post("/api/strategies/demo/prepare", json={}).status_code,
                    409,
                )

    def test_future_saved_plan_is_not_displayed_as_current(self):
        cfg = get_strategy("demo")
        adapter = routes._load_adapter(cfg.strategy_id)
        with (
            patch.object(
                adapter,
                "parse_history",
                return_value={
                    "reports": [
                        {"date": "20990101", "asof": "2098-12-31"},
                        {"date": "20240201", "asof": "2024-01-31"},
                    ]
                },
            ),
            patch.object(
                adapter,
                "parse_history_detail",
                return_value={"result": {"asof": "2024-01-31"}},
            ) as detail,
        ):
            result = adapter.parse_daily_latest(cfg)
            self.assertEqual(result["result"]["asof"], "2024-01-31")
            detail.assert_called_once_with(cfg, "20240201")

    def test_requests_reject_ambiguous_booleans_and_unknown_fields(self):
        from fastapi.testclient import TestClient
        from webui.server import create_app

        with TestClient(create_app()) as client:
            for body in ({"resume": "false"}, {"resume": 1}, {"unexpected": True}):
                self.assertEqual(
                    client.post("/api/strategies/demo/prepare", json=body).status_code,
                    422,
                )
            self.assertEqual(
                client.post(
                    "/api/strategies/demo/daily", json={"override": True}
                ).status_code,
                422,
            )
            schema = client.get("/openapi.json").json()["components"]["schemas"][
                "PrepareRequest"
            ]
            self.assertFalse(schema["additionalProperties"])

    def test_live_http_configuration_and_positions_endpoints(self):
        from fastapi.testclient import TestClient
        from webui.server import app

        with TestClient(app) as client:
            listed = client.get("/api/strategies").json()["strategies"]
            self.assertIn("demo", listed)
            self.assertEqual(listed, ["demo"])
            config = client.get("/api/strategies/demo/config")
            self.assertEqual(config.status_code, 200)
            self.assertIn(
                "budget", [col["key"] for col in config.json()["plan_columns"]["buy"]]
            )
            positions = client.get("/api/strategies/demo/positions")
            self.assertEqual(positions.status_code, 200)
            self.assertIn("positions", positions.json())
            monitor = client.get("/api/strategies/demo/monitor")
            self.assertEqual(monitor.status_code, 200)
            self.assertNotEqual(monitor.json()["golden"]["status"], "pass")


if __name__ == "__main__":
    unittest.main()
