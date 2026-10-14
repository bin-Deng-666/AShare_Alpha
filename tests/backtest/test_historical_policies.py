"""Explicit conservative policies must not invent cash or erase entitlement dates."""

import unittest
import pandas as pd
from ashare_alpha.backtest import BacktestEngine, MarketData
from tests.backtest.helpers import market, config
from tests.backtest.test_actions import Holder
from ashare_alpha.backtest.results import BacktestError


class HistoricalPolicyTests(unittest.TestCase):
    def test_rights_forfeiture_does_not_credit_shares_or_cash(self):
        base = market()
        changes = {
            ("000001", d): {"open": 8.0, "close": 8.0} for d in base.calendar[2:]
        }
        changes[("000001", base.calendar[2])].update(
            corporate_action_formula_status="rights_issue_price_formula_only",
            rights_issue_ratio=0.5,
            rights_issue_price=4.0,
        )
        data = market(changes=changes)
        result = BacktestEngine(data).run(
            Holder, config=config(data, rights_policy="forfeit")
        )
        self.assertEqual(result.daily.iloc[-1].cash, 9000.0)
        self.assertEqual(result.daily.iloc[-1].equity, 9800.0)
        self.assertEqual(
            result.positions.loc[
                result.positions.date.eq(base.calendar[-1]), "quantity"
            ].sum(),
            100,
        )
        self.assertEqual(result.actions.iloc[0].forfeited_rights, 50)
        self.assertEqual(result.actions.iloc[0].policy, "forfeit_rights")

    def test_suspended_dividend_is_accounted_on_ex_date_not_resumption(self):
        base = market()
        date = base.calendar[2]
        bars = base._bars.loc[~base._bars.date.isin(base.calendar[2:4])].copy()
        bars.loc[bars.date.ge(base.calendar[4]), ["open", "close"]] = 9.5
        actions = pd.DataFrame(
            [
                {
                    "code": "000001",
                    "date": date,
                    "available_at": base.calendar[1],
                    "corporate_action_formula_status": "supported_standard",
                    "cash_dividend_per_share": 0.5,
                    "share_distribution_ratio": 0.0,
                    "rights_issue_ratio": 0.0,
                }
            ]
        )
        data = MarketData(
            bars, calendar=base.calendar, features={"holder_actions": actions}
        )
        result = BacktestEngine(data).run(
            Holder,
            config=config(data, corporate_actions="ex_date_proxy", dividend_tax_rate=0),
        )
        self.assertEqual(result.daily.iloc[2].cash, 9050.0)
        self.assertEqual(result.daily.iloc[2].equity, 10000.0)
        self.assertEqual(result.daily.iloc[3].equity, 10000.0)
        self.assertEqual(result.daily.iloc[2].stale_codes, ["000001"])
        self.assertEqual(result.metrics["capital"]["income_pnl"], 50.0)
        self.assertEqual(
            len(result.ledger.loc[result.ledger.kind.eq("dividend_settlement")]), 1
        )

    def test_future_entitlement_cannot_override_current_unknown_event(self):
        base = market()
        bars = base._bars.copy()
        bars.loc[bars.date.eq(base.calendar[2]), "corporate_action_formula_status"] = (
            "unsupported_unclassified_share_change"
        )
        actions = pd.DataFrame(
            [
                {
                    "code": "000001",
                    "date": base.calendar[2],
                    "available_at": base.calendar[3],
                    "corporate_action_formula_status": "supported_standard",
                    "cash_dividend_per_share": 0.5,
                    "share_distribution_ratio": 0.0,
                    "rights_issue_ratio": 0.0,
                }
            ]
        )
        data = MarketData(
            bars, calendar=base.calendar, features={"holder_actions": actions}
        )
        with self.assertRaisesRegex(BacktestError, "unknown holder entitlements"):
            BacktestEngine(data).run(
                Holder, config=config(data, corporate_actions="ex_date_proxy")
            )

    def test_delisted_inventory_is_zero_valued_without_fictitious_sale(self):
        base = market()
        master = pd.DataFrame({"code": ["000001"], "delist_date": [base.calendar[4]]})
        data = MarketData(
            base._bars.loc[base._bars.date.lt(base.calendar[4])],
            calendar=base.calendar,
            master=master,
        )
        result = BacktestEngine(data).run(
            Holder, config=config(data, delisting_policy="zero_recovery")
        )
        self.assertEqual(result.daily.iloc[3].equity, 10000.0)
        self.assertTrue(result.daily.iloc[4:].equity.eq(9000.0).all())
        self.assertTrue(result.daily.cash.eq(9000.0).iloc[1:].all())
        self.assertEqual(result.trades.side.tolist(), ["BUY"])
        self.assertEqual(result.metrics["capital"]["unrealized_pnl"], -1000.0)
        self.assertEqual(
            len(
                result.ledger.loc[result.ledger.kind.eq("delisting_valuation_writeoff")]
            ),
            1,
        )
