"""Offline tests for the only published strategy example."""

import pandas as pd
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
import unittest
from ashare_alpha.backtest import BacktestConfig, BacktestEngine
from tests.backtest.helpers import market
from strategy.demo import BuyAndHoldStrategy
from strategy.demo.params import Params


class BuyAndHoldTests(unittest.TestCase):
    def market(self):
        dates = pd.bdate_range("2024-01-02", periods=6)
        changes = {
            (code, day): {"open": price, "close": price, "high": price, "low": price}
            for day, price in zip(dates, [10, 11, 8, 5, 12, 6])
            for code in ("000001", "600000")
        }
        return market(days=6, codes=("000001", "600000"), changes=changes)

    def test_only_selected_security_is_bought_and_never_sold(self):
        data = self.market()
        result = BacktestEngine(data).run(
            BuyAndHoldStrategy,
            strategy_params={"code": "000001"},
            config=BacktestConfig(
                start="2024-01-02", end="2024-01-09", initial_capital=10000
            ),
        )
        self.assertEqual(result.status, "success")
        self.assertFalse(result.trades.empty)
        self.assertEqual(set(result.trades.code), {"000001"})
        self.assertEqual(set(result.trades.side), {"BUY"})
        self.assertEqual(len(result.trades), 1)
        self.assertGreater(result.positions.iloc[-1].quantity, 0)
        self.assertEqual(result.diagnostics["plan"]["positions"][0]["code"], "000001")

    def test_parameter_selects_one_different_security(self):
        data = self.market()
        result = BacktestEngine(data).run(
            BuyAndHoldStrategy,
            strategy_params={"code": "600000"},
            config=BacktestConfig(
                start="2024-01-02", end="2024-01-09", initial_capital=10000
            ),
        )
        self.assertEqual(set(result.trades.code), {"600000"})
        self.assertEqual(set(result.trades.side), {"BUY"})

    def test_unknown_or_invalid_parameters_are_rejected(self):
        for value in ({"code": "invalid"}, {"code": 1}, {"stop_loss": 0.1}):
            with self.assertRaises(ValueError):
                Params.parse(value)

    def test_real_daily_plan_buys_initially_and_only_holds_after_fill(self):
        from strategy.demo.run import daily

        data = self.market()
        data.metadata.update(
            research_start="2024-01-02",
            research_end="2024-01-09",
            research_params={"code": "000001"},
        )
        with TemporaryDirectory() as directory:
            root = Path(directory)
            snapshot = root / "inputs"
            data.save_snapshot(snapshot)
            with patch("ashare_alpha.research.runner.ROOT", root / "artifacts"):
                initial = daily("2024-01-02", snapshot=snapshot, capital=10000)
                held = daily("2024-01-09", snapshot=snapshot, capital=10000)
            self.assertEqual([row["code"] for row in initial["buy"]], ["000001"])
            self.assertEqual(initial["exec_date"], "2024-01-03")
            self.assertFalse(initial["positions"])
            self.assertFalse(held["buy"])
            self.assertFalse(held["reduce"])
            self.assertEqual([row["code"] for row in held["positions"]], ["000001"])

    def test_suspension_does_not_create_a_trade(self):
        dates = pd.bdate_range("2024-01-02", periods=3)
        data = market(
            days=3, changes={("000001", day): {"trade_status": False} for day in dates}
        )
        result = BacktestEngine(data).run(
            BuyAndHoldStrategy,
            config=BacktestConfig(start="2024-01-02", end="2024-01-04"),
        )
        self.assertTrue(result.trades.empty)
        self.assertFalse(result.diagnostics["plan"]["positions"])


if __name__ == "__main__":
    unittest.main()
