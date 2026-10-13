"""Execution boundaries that cannot be inferred from strategy returns."""

import unittest
import pandas as pd
from tempfile import TemporaryDirectory
from pathlib import Path
from backtest import (
    Strategy,
    BacktestEngine,
    DataRequirements,
    OrderBatch,
    OrderRequest,
)
from backtest.account import Account
from backtest.data import DataView
from backtest.results import BacktestError
from backtest.tests.helpers import market, config


class Orders(Strategy):
    def requirements(self):
        return DataRequirements(mode="orders")

    def on_close(self, ctx):
        return self.params.get(ctx.session_index)


class EdgeTests(unittest.TestCase):
    def run_orders(self, data, schedule, **changes):
        return BacktestEngine(data).run(
            Orders, strategy_params=schedule, config=config(data, **changes)
        )

    def test_fractional_board_quantity_is_rejected_not_silently_rounded(self):
        data = market()
        r = self.run_orders(
            data, {0: OrderBatch((OrderRequest("000001", "BUY", quantity=150),))}
        )
        self.assertTrue(r.trades.empty)
        self.assertEqual(r.orders.iloc[0].status, "rejected")
        self.assertIn("invalid_buy_quantity", r.attempts.reason.tolist())
        for code, qty in [("688001", 201), ("920001", 101)]:
            data = market(codes=(code,))
            r = self.run_orders(
                data, {0: OrderBatch((OrderRequest(code, "BUY", quantity=qty),))}
            )
            self.assertEqual(r.trades.shares.tolist(), [qty])

    def test_cash_budget_includes_minimum_commission(self):
        from backtest.config import CostConfig

        data = market()
        fee = CostConfig(0, 5, 0, 0, False)
        r = self.run_orders(
            data,
            {0: OrderBatch((OrderRequest("000001", "BUY", budget=1000),))},
            costs=fee,
        )
        self.assertTrue(r.trades.empty)
        r = self.run_orders(
            data,
            {0: OrderBatch((OrderRequest("000001", "BUY", budget=1005),))},
            costs=fee,
        )
        self.assertEqual(r.trades.shares.tolist(), [100])
        self.assertEqual(r.trades.fee.tolist(), [5])
        self.assertEqual(r.daily.iloc[-1].cash, 8995)

    def test_t_plus_one_is_enforced_by_account_not_only_signal_delay(self):
        account = Account(10_000)
        date = pd.Timestamp("2024-01-02")
        account.buy(date, 0, "000001", 100, 1000, 0, "O1", "entry", {})
        with self.assertRaisesRegex(RuntimeError, "unavailable"):
            account.sell(date, 0, "000001", 100, 1000, 0, "O2", "exit")
        self.assertEqual(account.quantity("000001"), 100)
        self.assertEqual(account.cash, 900_000)
        account.sell(
            date + pd.Timedelta(days=1), 1, "000001", 100, 1000, 0, "O2", "exit"
        )
        self.assertEqual(account.cash, 1_000_000)

    def test_oversell_request_does_not_sell_a_smaller_position(self):
        data = market()
        r = self.run_orders(
            data,
            {
                0: OrderBatch((OrderRequest("000001", "BUY", quantity=100),)),
                1: OrderBatch((OrderRequest("000001", "SELL", quantity=200),)),
            },
        )
        self.assertEqual(r.trades.side.tolist(), ["BUY"])
        self.assertIn("quantity_exceeds_position", r.attempts.reason.tolist())

    def test_missing_row_defers_order_but_does_not_skip_account_date(self):
        from backtest.data import MarketData

        base = market()
        dropped = base._bars[base._bars.date.ne(base.calendar[1])]
        data = MarketData(dropped, calendar=base.calendar)
        r = self.run_orders(
            data,
            {
                0: OrderBatch(
                    (OrderRequest("000001", "BUY", quantity=100, valid_sessions=3),)
                )
            },
        )
        self.assertEqual(len(r.daily), 7)
        self.assertEqual(r.trades.date.tolist(), [base.calendar[2]])
        self.assertIn("missing_bar", r.attempts.reason.tolist())

    def test_missing_held_price_is_stale_and_never_a_zero_valuation(self):
        base = market()
        data = market(changes={("000001", base.calendar[2]): {"close": float("nan")}})
        r = self.run_orders(
            data, {0: OrderBatch((OrderRequest("000001", "BUY", quantity=100),))}
        )
        self.assertEqual(r.daily.iloc[2].market_value, 1000)
        self.assertEqual(r.daily.iloc[2].stale_codes, ["000001"])

    def test_empty_boundary_is_unknown_even_with_non_limit_flags(self):
        base = market()
        data = market(
            changes={
                ("000001", base.calendar[1]): {
                    "limit_validation_status": pd.NA,
                    "is_limit_up": False,
                }
            }
        )
        r = self.run_orders(
            data, {0: OrderBatch((OrderRequest("000001", "BUY", quantity=100),))}
        )
        self.assertTrue(r.trades.empty)
        self.assertIn("unavailable_limit_boundary", r.attempts.reason.tolist())

    def test_price_formula_capability_does_not_imply_execution(self):
        base = market()
        data = market(
            changes={
                ("000001", base.calendar[1]): {
                    "corporate_action_formula_status": "rights_issue_price_formula_only"
                }
            }
        )
        r = self.run_orders(
            data, {0: OrderBatch((OrderRequest("000001", "BUY", quantity=100),))}
        )
        self.assertTrue(r.trades.empty)
        self.assertIn("unsupported_corporate_action", r.attempts.reason.tolist())

    def test_preopen_view_is_read_only_and_does_not_show_today_close(self):
        data = market()
        view = DataView(
            data, data.calendar[1] + pd.Timedelta(hours=9), DataRequirements()
        )
        self.assertEqual(view.history().date.max(), data.calendar[0])
        self.assertTrue(view.cross_section().empty)
        with self.assertRaises(AttributeError):
            view.asof = data.calendar[-1]
        view = DataView(
            data, data.calendar[3] + pd.Timedelta(hours=15), DataRequirements()
        )
        expected = (
            data._bars[data._bars.date.le(data.calendar[3])].groupby("code").tail(2)
        )
        pd.testing.assert_frame_equal(
            view.history(lookback=2).reset_index(drop=True),
            expected[["code", "date", "close"]].reset_index(drop=True),
        )

    def test_no_same_day_callback_can_submit_more_trades(self):
        class Invalid(Orders):
            def on_execution(self, ctx, events):
                return OrderBatch()

        data = market()
        with self.assertRaises(BacktestError) as caught:
            BacktestEngine(data).run(
                Invalid,
                strategy_params={
                    0: OrderBatch((OrderRequest("000001", "BUY", quantity=100),))
                },
                config=config(data),
            )
        self.assertEqual(caught.exception.result.metrics, {})
        self.assertEqual(len(caught.exception.result.trades), 1)
        with TemporaryDirectory() as root:
            caught.exception.result.save(Path(root) / "failed")
            self.assertTrue((Path(root) / "failed/summary.json").exists())

    def test_final_unexecuted_orders_are_delivered_to_strategy(self):
        class Final(Orders):
            def on_start(self, ctx):
                self.ends = 0

            def on_execution(self, ctx, events):
                self.ends += sum(e.status == "open_at_end" for e in events)

            def on_end(self, view):
                return {"ends": self.ends}

        data = market()
        result = BacktestEngine(data).run(
            Final,
            strategy_params={
                6: OrderBatch((OrderRequest("000001", "BUY", quantity=100),))
            },
            config=config(data),
        )
        self.assertEqual(result.diagnostics["ends"], 1)
        self.assertTrue(result.trades.empty)

    def test_model_metadata_does_not_let_unavailable_training_pass(self):
        base = market()
        date = base.calendar[1]
        features = pd.DataFrame(
            [
                {
                    "code": "000001",
                    "date": date,
                    "available_at": date,
                    "model_available_at": date + pd.Timedelta(days=1),
                    "score": 1.0,
                }
            ]
        )
        data = market(features={"scores": features})
        view = DataView(
            data, date + pd.Timedelta(hours=15), DataRequirements(features=("scores",))
        )
        self.assertTrue(view.cross_section("scores").empty)

    def test_warmup_does_not_shift_requested_test_start(self):
        class Needs(Strategy):
            def requirements(self):
                return DataRequirements(warmup_sessions=10)

            def on_close(self, ctx):
                return None

        data = market()
        with self.assertRaisesRegex(ValueError, "warmup"):
            BacktestEngine(data).run(Needs, config=config(data))


if __name__ == "__main__":
    unittest.main()
