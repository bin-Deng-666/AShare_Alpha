"""Input availability, board rules, fee changes and statistical invariants."""

import unittest
from tempfile import TemporaryDirectory
from pathlib import Path
from types import SimpleNamespace
import pandas as pd
from backtest.models import DataRequirements, OrderRequest, TargetPortfolio
from backtest.data import DataView, MarketData
from backtest.metrics import compute_metrics
from backtest.rules import board_rule
from backtest.costs import fee_cents, trade_fee, slippage_bps_for_row, apply_slippage
from backtest.config import CostConfig
from backtest.engine import BacktestEngine
from backtest.strategy import Strategy
from backtest.tests.helpers import market, config


class DataCostMetricTests(unittest.TestCase):
    def test_features_visible_only_at_declared_time(self):
        base = market()
        date = base.calendar[1]
        features = pd.DataFrame(
            [
                {
                    "code": "000001",
                    "date": date,
                    "available_at": date + pd.Timedelta(hours=16),
                    "score": 100,
                }
            ]
        )
        data = market(features={"scores": features})
        needs = DataRequirements(features=("scores",))
        self.assertTrue(
            DataView(data, date + pd.Timedelta(hours=15), needs)
            .cross_section("scores")
            .empty
        )
        self.assertEqual(
            len(
                DataView(data, date + pd.Timedelta(hours=16), needs).cross_section(
                    "scores"
                )
            ),
            1,
        )
        with self.assertRaises(ValueError):
            DataView(data, date, needs).history(fields=["open"])
        with self.assertRaises(ValueError):
            MarketData(base._bars.assign(date=base.calendar[0]))
        with self.assertRaises(ValueError):
            market(features={"scores": features.drop(columns="available_at")})

    def test_new_listing_and_delisting_are_historical_universe_facts(self):
        base = market()
        master = pd.DataFrame(
            {
                "code": ["000001"],
                "list_date": [base.calendar[2]],
                "delist_date": [base.calendar[4]],
                "status": ["delisted"],
            }
        )
        data = MarketData(base._bars, master=master)
        self.assertEqual(data.universe(base.calendar[0]), ())
        self.assertEqual(data.universe(base.calendar[3]), ("000001",))
        self.assertEqual(data.universe(base.calendar[6]), ())

    def test_board_minimums_are_not_all_hundred_share_steps(self):
        self.assertEqual(board_rule("688001").floor_buy(201), 201)
        self.assertEqual(board_rule("688001").floor_buy(199), 0)
        self.assertEqual(board_rule("920001").floor_buy(101), 101)
        self.assertEqual(board_rule("000001").floor_buy(201), 200)
        self.assertTrue(board_rule("000001").valid_sell(37, 37))
        self.assertFalse(board_rule("000001").valid_sell(37, 137))

    def test_historical_fee_boundaries_and_side_validation(self):
        cfg = CostConfig(0, 0, 0.1, 5, True)
        self.assertEqual(fee_cents(100_000_00, "BUY", cfg, "2022-04-28"), 200)
        self.assertEqual(fee_cents(100_000_00, "BUY", cfg, "2022-04-29"), 100)
        self.assertEqual(fee_cents(100_000_00, "SELL", cfg, "2023-08-25"), 10_100)
        self.assertEqual(fee_cents(100_000_00, "SELL", cfg, "2023-08-28"), 5100)
        with self.assertRaises(ValueError):
            fee_cents(100, "BUY", cfg, "2010-01-01")
        with self.assertRaises(ValueError):
            apply_slippage(10, "INVALID", 5)
        with self.assertRaises(ValueError):
            OrderRequest("000001", "BUY", quantity=1.5)
        with self.assertRaises(ValueError):
            TargetPortfolio({"000001": 1.1})
        self.assertAlmostEqual(trade_fee(100_000, "SELL", cfg, "2023-08-28"), 51)

    def test_missing_index_labels_do_not_trigger_boolean_na(self):
        cfg = SimpleNamespace(
            enable_open_slippage=True,
            slippage_bps_large=1,
            slippage_bps_mid=2,
            slippage_bps_small=3,
            slippage_mcap_large=1e10,
            slippage_mcap_mid=1e9,
            slippage_amt_large=1e8,
            slippage_amt_mid=1e7,
        )
        self.assertEqual(
            slippage_bps_for_row({"is_hs300": pd.NA, "index_tier": pd.NA}, cfg), 2
        )

    def curve(self, values):
        return pd.DataFrame(
            {
                "date": pd.bdate_range("2024-01-02", periods=len(values)),
                "equity": values,
                "cash": values,
                "market_value": 0.0,
            }
        )

    def test_benchmark_never_truncates_account_and_retains_first_return(self):
        daily = self.curve([100, 110, 125])
        b = pd.DataFrame({"date": daily.date, "ret": [0.1, 0.2, 0.0]})
        full = compute_metrics(daily, None, b, 100)
        self.assertAlmostEqual(full["returns"]["bench_total_return"], 0.32)
        partial = compute_metrics(daily, None, b.iloc[:2], 100)
        self.assertEqual(partial["capital"]["ending_equity"], 125)
        self.assertFalse(partial["meta"]["benchmark_complete"])
        self.assertEqual(len(partial["merged_daily"]), 3)
        self.assertTrue(pd.isna(partial["returns"]["bench_total_return"]))

    def test_initial_capital_is_drawdown_base_and_rising_curve_has_zero_duration(self):
        r = compute_metrics(self.curve([100, 110, 120]), initial_capital=100)
        self.assertEqual(r["risk"]["max_dd_duration_days"], 0)
        self.assertEqual(r["risk"]["max_drawdown"], 0)
        r = compute_metrics(self.curve([90, 95, 100]), initial_capital=100)
        self.assertAlmostEqual(r["risk"]["max_drawdown"], -0.1)
        self.assertEqual(r["risk"]["max_dd_duration_days"], 2)
        r = compute_metrics(self.curve([100]), initial_capital=100)
        self.assertEqual(r["capital"]["total_return"], 0)
        self.assertTrue(pd.isna(r["risk"]["sharpe"]))

    def test_duplicate_dates_and_inconsistent_account_are_rejected(self):
        d = self.curve([100, 110])
        d.loc[1, "date"] = d.loc[0, "date"]
        with self.assertRaises(ValueError):
            compute_metrics(d, initial_capital=100)
        d = self.curve([100, 110])
        d["cash"] = 0
        with self.assertRaises(ValueError):
            compute_metrics(d, initial_capital=100)

    def test_input_snapshot_replays_and_detects_corruption(self):
        from backtest.tests.example_strategies import EqualWeightStrategy

        data = market(codes=("000001", "600000"))
        cfg = config(data)
        original = BacktestEngine(data).run(EqualWeightStrategy, config=cfg)
        with TemporaryDirectory() as directory:
            saved = original.save(Path(directory) / "run", inputs=data)
            loaded = MarketData.load_snapshot(saved / "inputs")
            replay = BacktestEngine(loaded).run(EqualWeightStrategy, config=cfg)
            self.assertEqual(original.metadata["run_id"], replay.metadata["run_id"])
            pd.testing.assert_frame_equal(original.daily, replay.daily)
            pd.testing.assert_frame_equal(original.trades, replay.trades)
            bars = pd.read_parquet(saved / "inputs/bars.parquet")
            bars.loc[0, "close"] = 99
            bars.to_parquet(saved / "inputs/bars.parquet", index=False)
            with self.assertRaisesRegex(ValueError, "fingerprint"):
                MarketData.load_snapshot(saved / "inputs")

    def test_inputs_cannot_be_saved_under_an_unrelated_run(self):
        class Cash(Strategy):
            def on_close(self, ctx):
                return None

        data = market()
        other = market(changes={("000001", data.calendar[0]): {"close": 9.0}})
        result = BacktestEngine(data).run(Cash, config=config(data))
        with TemporaryDirectory() as directory:
            output = Path(directory) / "run"
            with self.assertRaisesRegex(ValueError, "fingerprints differ"):
                result.save(output, inputs=other)
            self.assertFalse(output.exists())

    def test_result_publication_is_reproducible_and_does_not_overwrite(self):
        class Cash(Strategy):
            def on_close(self, ctx):
                return None

            def on_end(self, view):
                view.daily["equity"] = 0
                return {"note": "cash baseline"}

        data = market()
        r = BacktestEngine(data).run(Cash, config=config(data))
        self.assertTrue(r.daily.equity.eq(10_000).all())
        with TemporaryDirectory() as directory:
            path = r.save(Path(directory) / "run")
            self.assertTrue((path / "summary.json").exists())
            pd.testing.assert_frame_equal(
                pd.read_parquet(path / "daily.parquet").drop(columns="stale_codes"),
                r.daily.drop(columns="stale_codes"),
            )
            with self.assertRaises(FileExistsError):
                r.save(path)


if __name__ == "__main__":
    unittest.main()
