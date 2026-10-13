"""Cent-based cash, FIFO lots and dated corporate entitlements."""

from __future__ import annotations
from dataclasses import dataclass, field
from types import MappingProxyType
import pandas as pd
from .models import (
    ExecutionEvent,
    LotSnapshot,
    PositionSnapshot,
    PortfolioSnapshot,
    timestamp,
)
from .rules import cents, price_cents


@dataclass
class Lot:
    lot_id: str
    code: str
    quantity: int
    cost: int
    acquired_at: pd.Timestamp
    available_session: int
    tags: dict[str, str] = field(default_factory=dict)


@dataclass
class Entitlement:
    code: str
    pay_date: pd.Timestamp
    cash: int = 0
    quantity: int = 0
    cost: int = 0
    acquired_at: pd.Timestamp | None = None
    tags: dict[str, str] = field(default_factory=dict)


class Account:
    def __init__(self, capital):
        self.cash = cents(capital)
        self.lots = []
        self.entitlements = []
        self.ledger = []
        self.trades = []
        self.actions = []
        self.last_prices = {}
        self._next_lot = 1

    def _lot(self, code, quantity, cost, date, available_session, tags):
        lot = Lot(
            f"L{self._next_lot:06d}",
            code,
            quantity,
            cost,
            date,
            available_session,
            dict(tags),
        )
        self._next_lot += 1
        self.lots.append(lot)
        return lot

    def journal(
        self,
        date,
        kind,
        code="",
        cash=0,
        quantity=0,
        order_id=None,
        lot_id=None,
        **extra,
    ):
        self.ledger.append(
            {
                "date": date,
                "kind": kind,
                "code": code,
                "cash_delta": cash / 100,
                "quantity_delta": quantity,
                "cash_after": self.cash / 100,
                "order_id": order_id,
                "lot_id": lot_id,
                **extra,
            }
        )

    def quantity(self, code):
        return sum(l.quantity for l in self.lots if l.code == code)

    def sellable(self, code, session, lot_ids=()):
        return sum(
            l.quantity
            for l in self.lots
            if l.code == code
            and l.available_session <= session
            and (not lot_ids or l.lot_id in lot_ids)
        )

    def buy(self, date, session, code, quantity, price, fee, order_id, reason, tags):
        total = quantity * price + fee
        if total > self.cash:
            raise RuntimeError("Account cannot overdraw")
        self.cash -= total
        lot = self._lot(code, quantity, total, date, session + 1, tags)
        self.last_prices.setdefault(code, price)
        self.journal(
            date, "buy", code, -total, quantity, order_id, lot.lot_id, fee=fee / 100
        )
        self.trades.append(
            {
                "date": date,
                "order_id": order_id,
                "code": code,
                "side": "BUY",
                "shares": quantity,
                "price": price / 100,
                "amount": quantity * price / 100,
                "fee": fee / 100,
                "realized_pnl": 0.0,
                "hold_days": 0,
                "lot_id": lot.lot_id,
                "reason": reason,
                "tags": dict(tags),
            }
        )
        return lot.lot_id

    def sell(
        self, date, session, code, quantity, price, fee, order_id, reason, lot_ids=()
    ):
        if quantity > self.sellable(code, session, lot_ids):
            raise RuntimeError("Cannot sell unavailable shares")
        remaining = quantity
        cost = 0
        weighted_hold = 0.0
        matched = []
        for lot in list(self.lots):
            if (
                lot.code != code
                or lot.available_session > session
                or (lot_ids and lot.lot_id not in lot_ids)
            ):
                continue
            part = min(remaining, lot.quantity)
            cut = lot.cost if part == lot.quantity else lot.cost * part // lot.quantity
            cost += cut
            weighted_hold += part * (timestamp(date) - lot.acquired_at).days
            matched.append(
                {
                    "lot_id": lot.lot_id,
                    "quantity": part,
                    "cost_basis": cut / 100,
                    "closed": part == lot.quantity,
                    "tags": dict(lot.tags),
                }
            )
            lot.quantity -= part
            lot.cost -= cut
            remaining -= part
            self.journal(
                date,
                "lot_disposal",
                code,
                0,
                -part,
                order_id,
                lot.lot_id,
                cost_basis=cut / 100,
            )
            if lot.quantity == 0:
                self.lots.remove(lot)
            if remaining == 0:
                break
        proceeds = quantity * price - fee
        self.cash += proceeds
        self.journal(
            date,
            "sell",
            code,
            proceeds,
            0,
            order_id,
            fee=fee / 100,
            realized_pnl=(proceeds - cost) / 100,
        )
        self.trades.append(
            {
                "date": date,
                "order_id": order_id,
                "code": code,
                "side": "SELL",
                "shares": quantity,
                "price": price / 100,
                "amount": quantity * price / 100,
                "fee": fee / 100,
                "realized_pnl": (proceeds - cost) / 100,
                "hold_days": weighted_hold / quantity,
                "lot_id": None,
                "matched_lots": matched,
                "reason": reason,
            }
        )

    def settle(self, date, session):
        settled = []
        for item in list(self.entitlements):
            if item.pay_date > date:
                continue
            self.entitlements.remove(item)
            if item.cash:
                self.cash += item.cash
                self.journal(date, "dividend_settlement", item.code, item.cash)
                settled.append(
                    ExecutionEvent(
                        None,
                        date,
                        item.code,
                        "settled",
                        "dividend_settlement",
                        cash_amount=item.cash / 100,
                    )
                )
            if item.quantity:
                lot = self._lot(
                    item.code,
                    item.quantity,
                    item.cost,
                    item.acquired_at,
                    session,
                    item.tags,
                )
                self.journal(
                    date,
                    "share_settlement",
                    item.code,
                    0,
                    item.quantity,
                    lot_id=lot.lot_id,
                )
                settled.append(
                    ExecutionEvent(
                        None,
                        date,
                        item.code,
                        "settled",
                        "share_settlement",
                        quantity=item.quantity,
                        lot_id=lot.lot_id,
                    )
                )
        return settled

    def accrue(self, date, days, rate, opening_cash):
        interest = cents(opening_cash / 100 * rate * days / 365)
        if interest:
            self.cash += interest
            self.journal(date, "cash_interest", cash=interest)

    def mark(self, date, market, session):
        stale = []
        codes = set(l.code for l in self.lots) | {
            e.code for e in self.entitlements if e.quantity
        }
        for code in sorted(codes):
            row = market.row(date, code)
            if (
                row is not None
                and pd.notna(row.get("close"))
                and float(row["close"]) > 0
            ):
                self.last_prices[code] = price_cents(row["close"])
            else:
                stale.append(code)
            if code not in self.last_prices:
                raise RuntimeError(f"No valuation price for {code}")
        return self.snapshot(session), stale

    def snapshot(self, session):
        codes = sorted({l.code for l in self.lots})
        positions = {
            code: PositionSnapshot(
                code,
                self.quantity(code),
                self.sellable(code, session),
                sum(l.cost for l in self.lots if l.code == code) / 100,
                self.quantity(code) * self.last_prices.get(code, 0) / 100,
            )
            for code in codes
        }
        mv_cents = sum(
            self.quantity(code) * self.last_prices.get(code, 0) for code in codes
        )
        receivable_cents = sum(
            e.cash + e.quantity * self.last_prices.get(e.code, 0)
            for e in self.entitlements
        )
        mv = mv_cents / 100
        receivable = receivable_cents / 100
        lots = tuple(
            LotSnapshot(
                l.lot_id,
                l.code,
                l.quantity,
                l.quantity if l.available_session <= session else 0,
                l.cost / 100,
                l.acquired_at,
                MappingProxyType(dict(l.tags)),
            )
            for l in self.lots
        )
        return PortfolioSnapshot(
            self.cash / 100,
            (self.cash + mv_cents + receivable_cents) / 100,
            mv,
            receivable,
            MappingProxyType(positions),
            lots,
        )
