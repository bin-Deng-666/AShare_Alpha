"""Account, order, state and decision-clock invariants."""

import unittest
import pandas as pd
from ashare_alpha.backtest.engine import BacktestEngine
from ashare_alpha.backtest.strategy import Strategy
from ashare_alpha.backtest.models import (
    DataRequirements,
    OrderRequest,
    OrderBatch,
    TargetPortfolio,
)
from ashare_alpha.backtest.results import BacktestError
from tests.backtest.example_strategies import (
    EqualWeightStrategy,
    SleeveStrategy,
    ModelScoreStrategy,
    TrendExitStrategy,
)
from ashare_alpha.backtest.experiments import compare, RunSpec, parameter_grid
from tests.backtest.helpers import market, config


class Script(Strategy):
    def requirements(self):
        return DataRequirements(mode="orders")

    def on_close(self, ctx):
        return self.params.get(ctx.session_index)


class BuyHold(Strategy):
    def on_close(self, ctx):
        return TargetPortfolio({"000001": 0.5}) if ctx.session_index == 0 else None


class EngineTests(unittest.TestCase):
    def run_script(self, data, schedule, **cfg):
        return BacktestEngine(data).run(
            Script, strategy_params=schedule, config=config(data, **cfg)
        )

    def test_next_open_and_fifo_profit_reconcile(self):
        data = market()
        d = data.calendar
        changes = {
            ("000001", d[1]): {"close": 11.0},
            ("000001", d[2]): {"open": 12.0, "close": 12.0},
        }
        data = market(changes=changes)
        result = self.run_script(
            data,
            {
                0: OrderBatch((OrderRequest("000001", "BUY", quantity=100),)),
                1: OrderBatch((OrderRequest("000001", "SELL", quantity=100),)),
            },
        )
        self.assertEqual(result.trades.date.tolist(), [d[1], d[2]])
        self.assertEqual(result.metrics["capital"]["ending_equity"], 10_200)
        self.assertEqual(result.metrics["capital"]["realized_pnl"], 200)
        self.assertTrue(
            (
                result.daily.equity
                == result.daily.cash
                + result.daily.market_value
                + result.daily.receivables
            ).all()
        )
        self.assertTrue(result.trades.iloc[1].matched_lots[0]["closed"])

    def test_unknown_status_never_means_tradable(self):
        base = market()
        data = market(changes={("000001", base.calendar[1]): {"trade_status": pd.NA}})
        result = self.run_script(
            data, {0: OrderBatch((OrderRequest("000001", "BUY", quantity=100),))}
        )
        self.assertTrue(result.trades.empty)
        self.assertIn("unknown_trade_status", result.attempts.reason.tolist())
        self.assertEqual(result.orders.iloc[0].status, "expired")

    def test_open_price_not_close_limit_flag_controls_execution(self):
        base = market()
        data = market(
            changes={
                ("000001", base.calendar[1]): {
                    "is_limit_up": True,
                    "open": 10.0,
                    "limit_up": 11.0,
                }
            }
        )
        result = self.run_script(
            data, {0: OrderBatch((OrderRequest("000001", "BUY", quantity=100),))}
        )
        self.assertEqual(len(result.trades), 1)
        data = market(
            changes={("000001", base.calendar[1]): {"open": 11.0, "limit_up": 11.0}}
        )
        result = self.run_script(
            data, {0: OrderBatch((OrderRequest("000001", "BUY", quantity=100),))}
        )
        self.assertTrue(result.trades.empty)
        self.assertIn("adverse_limit_boundary", result.attempts.reason.tolist())

    def test_t_plus_one_and_cash_limit_under_multiple_same_open_orders(self):
        data = market()
        result = self.run_script(
            data,
            {
                0: OrderBatch(
                    (
                        OrderRequest("000001", "BUY", quantity=2000),
                        OrderRequest("000001", "BUY", quantity=1000),
                    )
                )
            },
        )
        self.assertEqual(int(result.trades.shares.sum()), 1000)
        self.assertGreaterEqual(result.daily.cash.min(), 0)
        self.assertIn("partially_filled", result.attempts.status.tolist())
        lots = result.positions[result.positions.date.eq(data.calendar[1])]
        self.assertEqual(int(lots.sellable_quantity.sum()), 0)
        self.assertEqual(
            int(
                result.positions[
                    result.positions.date.eq(data.calendar[2])
                ].sellable_quantity.sum()
            ),
            1000,
        )

    def test_target_none_keeps_positions_empty_target_liquidates(self):
        class Targets(Strategy):
            def on_close(self, ctx):
                if ctx.session_index == 0:
                    return TargetPortfolio({"000001": 0.5})
                if ctx.session_index == 3:
                    return TargetPortfolio({})

        data = market()
        r = BacktestEngine(data).run(Targets, config=config(data))
        self.assertEqual(r.trades.side.tolist(), ["BUY", "SELL"])
        self.assertEqual(r.trades.date.tolist(), [data.calendar[1], data.calendar[4]])

    def test_waiting_target_is_replaced_without_duplicate_fill(self):
        class Targets(Strategy):
            def on_close(self, ctx):
                if ctx.session_index == 0:
                    return TargetPortfolio({"000001": 0.5}, valid_sessions=3)
                if ctx.session_index == 1:
                    return TargetPortfolio({})

        base = market()
        data = market(changes={("000001", base.calendar[1]): {"trade_status": False}})
        r = BacktestEngine(data).run(Targets, config=config(data))
        self.assertTrue(r.trades.empty)
        self.assertIn("target_replaced", r.attempts.reason.tolist())

    def test_independent_tagged_lots_sell_only_selected_lot(self):
        class Lots(Strategy):
            def requirements(self):
                return DataRequirements(mode="orders")

            def on_close(self, ctx):
                if ctx.session_index == 0:
                    return OrderBatch(
                        (
                            OrderRequest(
                                "000001", "BUY", quantity=100, tags={"sleeve": "A"}
                            ),
                            OrderRequest(
                                "000001", "BUY", quantity=200, tags={"sleeve": "B"}
                            ),
                        )
                    )
                if ctx.session_index == 1:
                    lot = next(
                        item
                        for item in ctx.portfolio.lots
                        if item.tags["sleeve"] == "A"
                    )
                    return OrderBatch(
                        (
                            OrderRequest(
                                "000001",
                                "SELL",
                                quantity=lot.quantity,
                                lot_ids=(lot.lot_id,),
                            ),
                        )
                    )

        data = market()
        r = BacktestEngine(data).run(Lots, config=config(data))
        last = r.positions[r.positions.date.eq(data.calendar[-1])]
        self.assertEqual(last.quantity.tolist(), [200])
        self.assertEqual(last.iloc[0].tags, {"sleeve": "B"})

    def test_explicit_cancel_duplicate_id_and_final_signal(self):
        class Orders(Strategy):
            def requirements(self):
                return DataRequirements(mode="orders")

            def on_close(self, ctx):
                if ctx.session_index == 0:
                    return OrderBatch(
                        (
                            OrderRequest(
                                "000001",
                                "BUY",
                                quantity=100,
                                client_id="entry",
                                valid_sessions=4,
                            ),
                        )
                    )
                if ctx.session_index == 1:
                    return OrderBatch(
                        (
                            OrderRequest(
                                "000001", "BUY", quantity=100, client_id="entry"
                            ),
                        ),
                        cancel=(ctx.pending_orders[0].order_id,),
                    )
                if ctx.session_index == 6:
                    return OrderBatch((OrderRequest("000001", "BUY", quantity=100),))

        base = market()
        data = market(changes={("000001", base.calendar[1]): {"trade_status": False}})
        r = BacktestEngine(data).run(Orders, config=config(data))
        self.assertTrue(r.trades.empty)
        self.assertEqual(
            r.orders.status.tolist(), ["cancelled", "rejected", "open_at_end"]
        )

    def test_strategy_views_cannot_write_account_or_future_rows(self):
        class Views(Strategy):
            def on_start(self, ctx):
                assert ctx.data.history().empty

            def on_close(self, ctx):
                h = ctx.data.history()
                assert h.date.max() <= ctx.asof.normalize()
                h["close"] = 999
                with self.assertRaises(TypeError):
                    ctx.portfolio.positions["000001"] = None
                return None

        case = self
        Views.assertRaises = case.assertRaises
        data = market()
        r = BacktestEngine(data).run(Views, config=config(data))
        self.assertTrue(r.daily.equity.eq(10_000).all())
        self.assertEqual(data.row(data.calendar[-1], "000001").close, 10.0)

    def test_wrong_mode_failure_has_partial_auditable_result(self):
        class Bad(Strategy):
            def on_close(self, ctx):
                return OrderBatch()

        data = market()
        with self.assertRaises(BacktestError) as caught:
            BacktestEngine(data).run(Bad, config=config(data))
        self.assertEqual(caught.exception.result.status, "failed")
        self.assertEqual(caught.exception.result.metrics, {})
        self.assertEqual(len(caught.exception.result.daily), 1)

    def test_four_representative_strategies_and_instance_isolation(self):
        base = market(days=12, codes=("000001", "600000"))
        changes = {
            (code, date): {"open": 10 + i / 10, "close": 10 + i / 10}
            for i, date in enumerate(base.calendar)
            for code in ["000001", "600000"]
        }
        scores = pd.DataFrame(
            [
                {
                    "code": c,
                    "date": d,
                    "available_at": d + pd.Timedelta(hours=14),
                    "score": i,
                }
                for d in base.calendar
                for i, c in enumerate(["000001", "600000"])
            ]
        )
        data = market(
            days=12,
            codes=("000001", "600000"),
            changes=changes,
            features={"scores": scores},
        )
        cfg = config(data, start=str(data.calendar[3].date()))
        specs = [
            RunSpec("equal", EqualWeightStrategy, {"rebalance_sessions": 3}),
            RunSpec("trend", TrendExitStrategy, {"buy_budget": 2000}),
            RunSpec("sleeves", SleeveStrategy, {"buy_budget": 1000}),
            RunSpec("model", ModelScoreStrategy, {"top_n": 1}),
        ]
        batch = compare(data, cfg, specs)
        self.assertTrue(
            batch.summary.status.eq("success").all(), batch.summary.to_string()
        )
        for spec in specs:
            a = batch.results[spec.name]
            b = BacktestEngine(data).run(
                spec.strategy_class, strategy_params=spec.params, config=cfg
            )
            pd.testing.assert_frame_equal(a.daily, b.daily)
            pd.testing.assert_frame_equal(a.trades, b.trades)
            self.assertEqual(a.metadata["run_id"], b.metadata["run_id"])
        self.assertEqual(
            len(
                parameter_grid(
                    EqualWeightStrategy, {"top_n": [1, 2], "exposure": [0.5, 0.8]}
                )
            ),
            4,
        )


if __name__ == "__main__":
    unittest.main()
