"""Corporate entitlements, settlement and unsupported holdings are explicit."""

import unittest
from backtest.strategy import Strategy
from backtest.models import DataRequirements, OrderBatch, OrderRequest
from backtest.engine import BacktestEngine
from backtest.results import BacktestError
from backtest.tests.helpers import market, config


class Holder(Strategy):
    def requirements(self):
        return DataRequirements(mode="orders")

    def on_close(self, ctx):
        if ctx.session_index == 0:
            return OrderBatch((OrderRequest("000001", "BUY", quantity=100),))


class CorporateTests(unittest.TestCase):
    def action_market(self, **terms):
        base = market()
        date = base.calendar[2]
        changes = {
            ("000001", date): {
                "corporate_action_formula_status": "supported_standard",
                "cash_dividend_per_share": 0.0,
                "share_distribution_ratio": 0.0,
                "rights_issue_ratio": 0.0,
                **terms,
            }
        }
        return market(changes=changes)

    def test_strict_missing_settlement_date_stops_without_valid_performance(self):
        data = self.action_market(cash_dividend_per_share=0.5)
        with self.assertRaises(BacktestError) as caught:
            BacktestEngine(data).run(Holder, config=config(data))
        self.assertIn("cash_pay_date", str(caught.exception))
        self.assertEqual(caught.exception.result.metrics, {})
        self.assertEqual(len(caught.exception.result.daily), 2)

    def test_receivable_value_does_not_become_spendable_cash_early(self):
        base = market()
        changes = {}
        for date in base.calendar[2:]:
            changes[("000001", date)] = {"open": 9.5, "close": 9.5}
        changes[("000001", base.calendar[2])].update(
            corporate_action_formula_status="supported_standard",
            cash_dividend_per_share=0.5,
            share_distribution_ratio=0.0,
            rights_issue_ratio=0.0,
            cash_pay_date=base.calendar[4],
        )
        data = market(changes=changes)
        r = BacktestEngine(data).run(Holder, config=config(data, dividend_tax_rate=0))
        day = r.daily.iloc[2]
        self.assertEqual(day.cash, 9000)
        self.assertEqual(day.receivables, 50)
        self.assertEqual(day.equity, 10_000)
        self.assertEqual(r.daily.iloc[4].cash, 9050)
        self.assertEqual(r.daily.iloc[4].receivables, 0)
        self.assertEqual(r.metrics["capital"]["income_pnl"], 50)
        self.assertEqual(r.metrics["capital"]["unrealized_pnl"], -50)

    def test_proxy_is_recorded_and_ex_date_buys_do_not_receive_dividend(self):
        class LateBuyer(Strategy):
            def requirements(self):
                return DataRequirements(mode="orders")

            def on_close(self, ctx):
                if ctx.session_index == 1:
                    return OrderBatch((OrderRequest("000001", "BUY", quantity=100),))

        data = self.action_market(cash_dividend_per_share=0.5)
        r = BacktestEngine(data).run(
            Holder,
            config=config(data, corporate_actions="ex_date_proxy", dividend_tax_rate=0),
        )
        self.assertEqual(r.actions.iloc[0].policy, "ex_date_proxy")
        self.assertEqual(r.daily.iloc[2].cash, 9050)
        late = BacktestEngine(data).run(
            LateBuyer, config=config(data, corporate_actions="ex_date_proxy")
        )
        self.assertTrue(late.actions.empty)

    def test_bonus_shares_are_valued_but_unavailable_until_declared_date(self):
        base = market()
        data = self.action_market(
            share_distribution_ratio=0.1, share_available_date=base.calendar[4]
        )
        r = BacktestEngine(data).run(Holder, config=config(data))
        self.assertEqual(r.daily.iloc[2].receivables, 100)
        self.assertEqual(
            int(r.positions[r.positions.date.eq(base.calendar[2])].quantity.sum()), 100
        )
        self.assertEqual(
            int(r.positions[r.positions.date.eq(base.calendar[4])].quantity.sum()), 110
        )
        self.assertEqual(
            r.positions[r.positions.date.eq(base.calendar[4])].cost_basis.sum(), 1000
        )

    def test_rights_and_unknown_holder_entitlements_fail_closed(self):
        data = self.action_market(rights_issue_ratio=0.1)
        with self.assertRaisesRegex(BacktestError, "rights subscription"):
            BacktestEngine(data).run(
                Holder, config=config(data, corporate_actions="ex_date_proxy")
            )
        base = market()
        data = market(
            changes={
                ("000001", base.calendar[2]): {
                    "corporate_action_formula_status": "unsupported_unclassified_share_change"
                }
            }
        )
        with self.assertRaisesRegex(BacktestError, "unknown holder entitlements"):
            BacktestEngine(data).run(
                Holder, config=config(data, corporate_actions="ex_date_proxy")
            )


if __name__ == "__main__":
    unittest.main()
