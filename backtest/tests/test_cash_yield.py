"""Dated cash rates are immutable inputs and cannot accrue before availability."""

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
import pandas as pd
from backtest import MarketData, BacktestEngine, Strategy
from backtest.tests.helpers import market, config


class Idle(Strategy):
    def on_close(self, ctx):
        return None


class CashYieldTests(unittest.TestCase):
    def dataset(self, available="2024-01-05 15:00"):
        base = market(days=5)
        dates = pd.to_datetime(["2024-01-04", "2024-01-05", "2024-01-08"])
        bars = base._bars.loc[base._bars.date.isin(dates)].copy()
        rates = pd.DataFrame(
            {
                "date": pd.to_datetime(["2024-01-04", "2024-01-05", "2024-01-08"]),
                "rate": [0.0, 0.365, 100.0],
                "available_at": pd.to_datetime(
                    ["2024-01-04 15:00", available, "2024-01-08 15:00"]
                ),
            }
        )
        return MarketData(bars, calendar=dates, cash_yield=rates)

    def test_friday_rate_accrues_three_natural_days_and_ignores_monday_rate(self):
        data = self.dataset()
        result = BacktestEngine(data).run(Idle, config=config(data, cash_yield="input"))
        self.assertAlmostEqual(result.daily.equity.iloc[-1], 10030.0)
        self.assertEqual(result.daily.cash_rate.iloc[-1], 0.365)

    def test_late_or_missing_rate_is_visible_and_cannot_accrue(self):
        data = self.dataset("2024-01-05 16:00")
        result = BacktestEngine(data).run(Idle, config=config(data, cash_yield="input"))
        self.assertTrue(result.daily.cash_rate_missing.iloc[-1])
        self.assertAlmostEqual(result.daily.equity.iloc[-1], 10000)

    def test_rates_participate_in_fingerprint_and_snapshot_replay(self):
        data = self.dataset()
        self.assertNotEqual(
            data.fingerprint, self.dataset("2024-01-05 16:00").fingerprint
        )
        with TemporaryDirectory() as directory:
            path = Path(directory) / "inputs"
            data.save_snapshot(path)
            replay = MarketData.load_snapshot(path)
            self.assertEqual(data.fingerprint, replay.fingerprint)
            pd.testing.assert_frame_equal(data._cash_yield, replay._cash_yield)

    def test_boolean_categories_survive_parquet_without_fingerprint_drift(self):
        base = market(days=3)
        flags = pd.DataFrame(
            {
                "code": ["000001"] * 3,
                "date": base.calendar,
                "available_at": base.calendar + pd.Timedelta(hours=15),
                "flag": pd.Categorical(
                    [True, None, False], categories=[False, True], ordered=True
                ),
            }
        )
        data = MarketData(base._bars, calendar=base.calendar, features={"flags": flags})
        with TemporaryDirectory() as directory:
            path = Path(directory) / "inputs"
            data.save_snapshot(path)
            reloaded = MarketData.load_snapshot(path)
            self.assertEqual(data.fingerprint, reloaded.fingerprint)
            pd.testing.assert_frame_equal(
                data._features["flags"], reloaded._features["flags"]
            )

    def test_input_rate_requires_observations_and_excludes_fixed_rate(self):
        base = market()
        with self.assertRaises(ValueError):
            BacktestEngine(base).run(Idle, config=config(base, cash_yield="input"))
        with self.assertRaises(ValueError):
            config(base, cash_yield="input", cash_rate=0.02)


if __name__ == "__main__":
    unittest.main()
