"""Capacity must constrain real fills without using the execution day's turnover."""

import unittest
from ashare_alpha.backtest import (
    BacktestEngine,
    CapacityConfig,
    MarketData,
    OrderBatch,
    OrderRequest,
)
from tests.backtest.test_execution_edges import Orders
from tests.backtest.helpers import market, config


class CapacityTests(unittest.TestCase):
    def run_orders(self, data, schedule, **changes):
        return BacktestEngine(data).run(
            Orders, strategy_params=schedule, config=config(data, **changes)
        )

    def data(self, amounts=None):
        base = market(days=9)
        bars = base._bars.copy()
        bars["amount"] = amounts if amounts is not None else 1_000_000.0
        return MarketData(bars, calendar=base.calendar)

    def test_quantity_and_budget_orders_roll_forward_after_partial_fills(self):
        for sizing in ({"quantity": 300}, {"budget": 3000}):
            data = self.data()
            result = self.run_orders(
                data,
                {
                    1: OrderBatch(
                        (OrderRequest("000001", "BUY", valid_sessions=3, **sizing),)
                    )
                },
                capacity=CapacityConfig(0.001, 2),
            )
            self.assertEqual(result.trades.shares.tolist(), [100, 100, 100])
            self.assertEqual(result.trades.date.tolist(), list(data.calendar[2:5]))
            self.assertEqual(result.orders.iloc[0].status, "filled")
            self.assertEqual(result.daily.iloc[-1].cash, 7000)

    def test_orders_and_sides_share_gross_capacity(self):
        data = self.data()
        result = self.run_orders(
            data,
            {
                1: OrderBatch((OrderRequest("000001", "BUY", quantity=100),)),
                2: OrderBatch(
                    (
                        OrderRequest("000001", "BUY", quantity=100, valid_sessions=2),
                        OrderRequest("000001", "SELL", quantity=100),
                    )
                ),
            },
            capacity=CapacityConfig(0.001, 2),
        )
        self.assertEqual(result.trades.side.tolist(), ["BUY", "SELL", "BUY"])
        self.assertEqual(result.trades.date.tolist(), list(data.calendar[2:5]))
        self.assertIn("capacity_exhausted", result.attempts.reason.tolist())

    def test_today_and_future_amount_cannot_increase_today_fill(self):
        amounts = [1_000_000.0] * 9
        amounts[2:] = [1e12] * 7
        data = self.data(amounts)
        result = self.run_orders(
            data,
            {1: OrderBatch((OrderRequest("000001", "BUY", quantity=500),))},
            capacity=CapacityConfig(0.001, 2),
        )
        self.assertEqual(result.trades.shares.tolist(), [100])
        self.assertEqual(result.orders.iloc[0].status, "expired")

    def test_missing_or_incomplete_history_is_not_unlimited_capacity(self):
        for amounts in ([None, 1e6] + [1e12] * 7, [1e6] * 9):
            data = self.data(amounts)
            result = self.run_orders(
                data,
                {1: OrderBatch((OrderRequest("000001", "BUY", quantity=100),))},
                capacity=CapacityConfig(0.001, 3 if amounts[0] is not None else 2),
            )
            self.assertTrue(result.trades.empty)
            self.assertIn("missing_capacity_history", result.attempts.reason.tolist())
        data = self.data()
        bars = data._bars.loc[data._bars.date.ne(data.calendar[0])]
        data = MarketData(bars, calendar=data.calendar)
        result = self.run_orders(
            data,
            {1: OrderBatch((OrderRequest("000001", "BUY", quantity=100),))},
            capacity=CapacityConfig(0.001, 2),
        )
        self.assertTrue(result.trades.empty)

    def test_sells_are_partial_and_do_not_release_unfilled_cash(self):
        amounts = [10e6, 10e6] + [1e6] * 7
        data = self.data(amounts)
        result = self.run_orders(
            data,
            {
                1: OrderBatch((OrderRequest("000001", "BUY", quantity=500),)),
                3: OrderBatch(
                    (OrderRequest("000001", "SELL", quantity=500, valid_sessions=3),)
                ),
            },
            capacity=CapacityConfig(0.001, 2),
        )
        self.assertEqual(result.trades.shares.tolist(), [500, 100, 100, 100])
        self.assertEqual(result.daily.iloc[-1].market_value, 2000)
        self.assertEqual(result.daily.iloc[-1].cash, 8000)
        self.assertEqual(result.orders.iloc[-1].remaining_quantity, 200)

    def test_capacity_split_does_not_strand_below_minimum_star_board_buy(self):
        base = market(days=6, codes=("688001",))
        bars = base._bars.copy()
        bars["amount"] = 4_000_000.0
        data = MarketData(bars, calendar=base.calendar)
        result = self.run_orders(
            data,
            {
                0: OrderBatch(
                    (OrderRequest("688001", "BUY", quantity=500, valid_sessions=2),)
                )
            },
            capacity=CapacityConfig(0.001, 1),
        )
        self.assertEqual(result.trades.shares.tolist(), [300, 200])
        self.assertEqual(result.orders.iloc[0].status, "filled")
        bars["amount"] = [
            2_000_000.0,
            4_000_000.0,
            4_000_000.0,
            4_000_000.0,
            4_000_000.0,
            4_000_000.0,
        ]
        data = MarketData(bars, calendar=base.calendar)
        result = self.run_orders(
            data,
            {
                0: OrderBatch(
                    (OrderRequest("688001", "BUY", quantity=301, valid_sessions=2),)
                )
            },
            capacity=CapacityConfig(0.001, 1),
        )
        self.assertEqual(result.trades.shares.tolist(), [301])
        self.assertEqual(result.trades.date.tolist(), [data.calendar[2]])

    def test_capacity_configuration_rejects_invalid_values(self):
        for value in (0, -1, 2, True, float("nan")):
            with self.assertRaises(ValueError):
                CapacityConfig(value)
        with self.assertRaises(ValueError):
            CapacityConfig(0.001, 0)
        self.assertIsNone(CapacityConfig().participation_rate)
