"""Holder accounting is separate from price-reference validation."""

from math import floor, isfinite
import pandas as pd
from .account import Entitlement
from .models import ExecutionEvent, timestamp
from .rules import cents, price_cents


class UnsupportedAction(RuntimeError):
    pass


def apply_actions(account, date, session, market, config):
    from ashare_alpha.data.rules.corporate_actions import (
        capabilities_for,
        formula_status_value,
    )

    events = []
    for code in sorted({lot.code for lot in account.lots}):
        row = market.holder_action(date, code)
        if row is None:
            continue
        status = row.get("corporate_action_formula_status")
        capabilities = capabilities_for(status)
        if not capabilities.holder_entitlements_supported:
            raise UnsupportedAction(
                f"{code} {date.date()}: unknown holder entitlements ({status})"
            )
        if formula_status_value(status).value == "none":
            continue

        def term(name):
            value = row.get(name)
            if pd.isna(value):
                raise UnsupportedAction(f"{code}: missing {name}")
            value = float(value)
            if not isfinite(value) or value < 0:
                raise UnsupportedAction(f"{code}: invalid {name}")
            return value

        cash = term("cash_dividend_per_share")
        ratio = term("share_distribution_ratio")
        rights = term("rights_issue_ratio")
        if rights:
            if config.rights_policy == "fail":
                raise UnsupportedAction(
                    f"{code}: rights subscription accounting is not supported"
                )
            # Explicit nonparticipation: no new shares, no imaginary rights-sale proceeds.
            account.actions.append(
                {
                    "date": date,
                    "code": code,
                    "formula_status": str(status),
                    "policy": "forfeit_rights",
                    "rights_ratio": rights,
                    "forfeited_rights": floor(account.quantity(code) * rights),
                    "cash_receivable": 0.0,
                    "share_receivable": 0,
                }
            )
            events.append(
                ExecutionEvent(None, date, code, "forfeited", "rights_not_subscribed")
            )
        quote = market.row(date, code)
        if quote is None and (cash or ratio or rights):
            # Ex-date mark during suspension. It is a formula estimate, still flagged
            # stale by Account.mark; actual resumed quotes replace it later.
            if config.corporate_actions != "ex_date_proxy":
                raise UnsupportedAction(
                    f"{code}: suspended event requires an explicit valuation policy"
                )
            rights_price = term("rights_issue_price") if rights else 0.0
            reference = (
                account.last_prices[code] / 100 - cash + rights_price * rights
            ) / (1 + ratio + rights)
            account.last_prices[code] = price_cents(reference)
            account.actions.append(
                {
                    "date": date,
                    "code": code,
                    "policy": "suspended_ex_date_valuation_proxy",
                    "reference_price": reference,
                    "cash_receivable": 0.0,
                    "share_receivable": 0,
                }
            )
        if not cash and not ratio:
            continue
        if config.corporate_actions == "strict":

            def settlement(field, required):
                value = row.get(field)
                if required and pd.isna(value):
                    raise UnsupportedAction(
                        f"{code}: {field} is required by strict settlement policy"
                    )
                pay = timestamp(value).normalize() if required else date
                if pay < date:
                    raise UnsupportedAction(f"{code}: settlement precedes the ex date")
                return pay

            cash_date = settlement("cash_pay_date", cash > 0)
            share_date = settlement("share_available_date", ratio > 0)
        else:
            cash_date = share_date = date
        lots = [lot for lot in account.lots if lot.code == code]
        quantities = {lot.lot_id: floor(lot.quantity * ratio + 1e-10) for lot in lots}
        extra = floor(sum(lot.quantity for lot in lots) * ratio + 1e-10) - sum(
            quantities.values()
        )
        ranking = sorted(
            lots,
            key=lambda lot: (
                -(lot.quantity * ratio - quantities[lot.lot_id]),
                lot.lot_id,
            ),
        )
        for lot in ranking[:extra]:
            quantities[lot.lot_id] += 1
        credited_cash = credited_shares = 0
        for lot in lots:
            dividend = cents(lot.quantity * cash * (1 - config.dividend_tax_rate))
            added = quantities[lot.lot_id]
            moved = lot.cost * added // (lot.quantity + added) if added else 0
            if dividend:
                account.entitlements.append(Entitlement(code, cash_date, cash=dividend))
            if added:
                lot.cost -= moved
                account.entitlements.append(
                    Entitlement(
                        code,
                        share_date,
                        quantity=added,
                        cost=moved,
                        acquired_at=lot.acquired_at,
                        tags=dict(lot.tags),
                    )
                )
            credited_cash += dividend
            credited_shares += added
        account.journal(
            date,
            "holder_entitlement",
            code,
            entitlement_cash=credited_cash / 100,
            entitlement_shares=credited_shares,
        )
        account.actions.append(
            {
                "date": date,
                "code": code,
                "formula_status": str(status),
                "policy": config.corporate_actions,
                "cash_receivable": credited_cash / 100,
                "share_receivable": credited_shares,
                "cash_pay_date": cash_date,
                "share_available_date": share_date,
                "dividend_tax_rate": config.dividend_tax_rate,
            }
        )
        events.append(
            ExecutionEvent(
                None,
                date,
                code,
                "entitled",
                "corporate_action",
                quantity=credited_shares,
            )
        )
    events.extend(account.settle(date, session))
    return events
