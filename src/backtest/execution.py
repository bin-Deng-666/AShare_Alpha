"""One deterministic order lifecycle and next-open execution pipeline."""

from dataclasses import dataclass
import math
import pandas as pd
from .models import OrderRequest, Side, ExecutionEvent, PendingOrder
from .rules import board_rule, cents, price_cents
from .costs import fee_cents, fill_price_cents


@dataclass
class Order:
    order_id: str
    request: OrderRequest
    submitted_at: pd.Timestamp
    first_session: int
    last_session: int
    origin: str
    remaining: int | None
    budget: int | None
    status: str = "accepted"


class Broker:
    def __init__(self, account, market, config):
        self.account = account
        self.market = market
        self.config = config
        self.orders = []
        self.attempts = []
        self.events = []
        self._next = 1
        self._clients = set()
        from .capacity import ParticipationBudget

        self.capacity = (
            ParticipationBudget(market, config.capacity)
            if config.capacity.participation_rate is not None
            else None
        )

    def event(
        self, order, date, status, reason, quantity=0, price=None, fee=0, lot_id=None
    ):
        item = ExecutionEvent(
            order.order_id,
            date,
            order.request.code,
            status,
            reason,
            quantity,
            None if price is None else price / 100,
            fee / 100,
            lot_id,
        )
        self.events.append(item)
        self.attempts.append(
            {
                "order_id": order.order_id,
                "date": date,
                "code": order.request.code,
                "status": status,
                "reason": reason,
                "quantity": quantity,
                "price": item.price,
                "fee": item.fee,
            }
        )
        return item

    def active(self):
        return [
            o
            for o in self.orders
            if o.status in ("accepted", "deferred", "partially_filled")
        ]

    def submit(self, request, date, session, origin="orders"):
        if not isinstance(request, OrderRequest):
            raise TypeError("Expected OrderRequest")
        oid = f"O{self._next:06d}"
        self._next += 1
        order = Order(
            oid,
            request,
            date,
            session + 1,
            session + request.valid_sessions,
            origin,
            request.quantity,
            None if request.budget is None else cents(request.budget),
        )
        self.orders.append(order)
        if request.client_id is not None and request.client_id in self._clients:
            order.status = "rejected"
            self.event(order, date, "rejected", "duplicate_client_id")
            return
        if request.client_id:
            self._clients.add(request.client_id)
        if request.code not in self.market.universe(date):
            order.status = "rejected"
            self.event(order, date, "rejected", "outside_universe")
            return
        self.event(order, date, "accepted", "submitted")

    def cancel(self, order_id, date, reason="strategy_cancel"):
        matches = [o for o in self.active() if o.order_id == order_id]
        if not matches:
            raise ValueError(f"No active order: {order_id}")
        order = matches[0]
        order.status = "cancelled"
        self.event(order, date, "cancelled", reason)

    def pending(self):
        calendar = self.market.calendar
        return tuple(
            PendingOrder(
                o.order_id,
                o.request,
                o.remaining,
                o.submitted_at,
                (
                    calendar[o.last_session]
                    if o.last_session < len(calendar)
                    else pd.Timestamp.max.normalize()
                ),
            )
            for o in self.active()
        )

    def _defer(self, order, date, reason):
        order.status = "deferred"
        self.event(order, date, "deferred", reason)

    def _affordable(self, price, maximum, cash, code, date):
        rule = board_rule(code)
        maximum = rule.floor_buy(maximum)
        if maximum == 0:
            return 0
        lo = 0
        hi = (maximum - rule.minimum) // rule.step + 1
        while lo < hi:
            mid = (lo + hi + 1) // 2
            q = rule.minimum + (mid - 1) * rule.step
            if (
                q * price + fee_cents(q * price, Side.BUY, self.config.costs, date)
                <= cash
            ):
                lo = mid
            else:
                hi = mid - 1
        return 0 if lo == 0 else rule.minimum + (lo - 1) * rule.step

    def run_day(self, date, session):
        start = len(self.events)
        if self.capacity is not None:
            self.capacity.reset()
        for order in sorted(
            self.active(),
            key=lambda o: (
                o.request.side != Side.SELL,
                -o.request.priority,
                o.request.code,
                o.order_id,
            ),
        ):
            if session < order.first_session:
                continue
            if session > order.last_session:
                order.status = "expired"
                self.event(order, date, "expired", "validity_ended")
                continue
            self._execute(order, date, session)
            if order in self.active() and session >= order.last_session:
                order.status = "expired"
                self.event(order, date, "expired", "validity_ended")
        return tuple(self.events[start:])

    def _execute(self, order, date, session):
        request = order.request
        code = request.code
        if code not in self.market.universe(date):
            order.status = "rejected"
            self.event(order, date, "rejected", "outside_universe")
            return
        row = self.market.row(date, code)
        if row is None:
            self._defer(order, date, "missing_bar")
            return
        trade = row.get("trade_status")
        if pd.isna(trade):
            self._defer(order, date, "unknown_trade_status")
            return
        if trade not in (True, False, 0, 1):
            self._defer(order, date, "invalid_trade_status")
            return
        if not bool(trade):
            self._defer(order, date, "suspended")
            return
        raw = row.get("open")
        if pd.isna(raw) or not math.isfinite(float(raw)) or float(raw) <= 0:
            self._defer(order, date, "invalid_open")
            return
        from ashare_alpha.data.rules.corporate_actions import capabilities_for

        if not capabilities_for(
            row.get("corporate_action_formula_status")
        ).execution_supported:
            self._defer(order, date, "unsupported_corporate_action")
            return
        status = row.get("limit_validation_status")
        if pd.isna(status) or status not in ("valid", "not_applicable"):
            self._defer(order, date, "unavailable_limit_boundary")
            return
        price = fill_price_cents(raw, request.side, self.config.slippage.bps)
        if status == "valid":
            up, down = row.get("limit_up"), row.get("limit_down")
            if pd.isna(up) or pd.isna(down):
                self._defer(order, date, "unavailable_limit_boundary")
                return
            upper, lower = price_cents(up), price_cents(down)
            if lower > upper:
                raise ValueError("Inverted execution boundaries")
            if (request.side == Side.BUY and price >= upper) or (
                request.side == Side.SELL and price <= lower
            ):
                self._defer(order, date, "adverse_limit_boundary")
                return
            if not lower <= price <= upper:
                self._defer(order, date, "price_outside_boundary")
                return
        capacity_quantity = None
        if self.capacity is not None:
            remaining_capacity = self.capacity.remaining(code, session)
            if remaining_capacity is None:
                self._defer(order, date, "missing_capacity_history")
                return
            capacity_quantity = remaining_capacity // price
        if request.side == Side.BUY:
            if (
                order.budget is None
                and board_rule(code).floor_buy(order.remaining) != order.remaining
            ):
                order.status = "rejected"
                self.event(order, date, "rejected", "invalid_buy_quantity")
                return
            maximum = (
                order.budget // price if order.budget is not None else order.remaining
            )
            cash = (
                min(self.account.cash, order.budget)
                if order.budget is not None
                else self.account.cash
            )
            unconstrained = self._affordable(price, maximum, cash, code, date)
            quantity = (
                min(unconstrained, board_rule(code).floor_buy(capacity_quantity))
                if capacity_quantity is not None
                else unconstrained
            )
            if (
                order.budget is None
                and capacity_quantity is not None
                and 0 < quantity < order.remaining
            ):
                rule = board_rule(code)
                remainder = order.remaining - quantity
                if rule.floor_buy(remainder) != remainder:
                    # A capacity split must not strand an illegal residual order on
                    # boards whose minimum purchase exceeds the quantity step.
                    quantity = min(
                        quantity, rule.floor_buy(order.remaining - rule.minimum)
                    )
            if quantity == 0:
                self._defer(
                    order,
                    date,
                    "capacity_exhausted"
                    if unconstrained
                    else "below_board_minimum_or_cash",
                )
                return
            fee = fee_cents(quantity * price, Side.BUY, self.config.costs, date)
            lot_id = self.account.buy(
                date,
                session,
                code,
                quantity,
                price,
                fee,
                order.order_id,
                request.reason,
                request.tags,
            )
            if order.budget is not None:
                # Capacity-limited budgets retain the unspent amount for later sessions.
                order.budget -= quantity * price + fee
                order.status = (
                    "partially_filled" if quantity < unconstrained else "filled"
                )
            else:
                order.remaining -= quantity
                order.status = "filled" if order.remaining == 0 else "partially_filled"
            if self.capacity is not None:
                self.capacity.consume(code, quantity * price)
            self.event(
                order, date, order.status, "executed", quantity, price, fee, lot_id
            )
        else:
            total = self.account.quantity(code)
            if not total:
                order.status = "cancelled"
                self.event(order, date, "cancelled", "position_already_closed")
                return
            if request.lot_ids and any(
                not any(
                    lot.lot_id == lid and lot.code == code for lot in self.account.lots
                )
                for lid in request.lot_ids
            ):
                order.status = "rejected"
                self.event(order, date, "rejected", "unknown_lot")
                return
            selected_total = sum(
                lot.quantity
                for lot in self.account.lots
                if lot.code == code
                and (not request.lot_ids or lot.lot_id in request.lot_ids)
            )
            if order.remaining > selected_total:
                order.status = "rejected"
                self.event(order, date, "rejected", "quantity_exceeds_position")
                return
            sellable = self.account.sellable(code, session, request.lot_ids)
            quantity = min(order.remaining, sellable)
            if not quantity:
                self._defer(order, date, "t_plus_one_unavailable")
                return
            rule = board_rule(code)
            if not rule.valid_sell(quantity, total):
                order.status = "rejected"
                self.event(order, date, "rejected", "invalid_sell_quantity")
                return
            if capacity_quantity is not None and quantity > capacity_quantity:
                quantity = rule.floor_buy(capacity_quantity)
                if not quantity:
                    self._defer(order, date, "capacity_exhausted")
                    return
            fee = fee_cents(quantity * price, Side.SELL, self.config.costs, date)
            if self.account.cash + quantity * price < fee:
                self._defer(order, date, "fee_exceeds_available_cash")
                return
            self.account.sell(
                date,
                session,
                code,
                quantity,
                price,
                fee,
                order.order_id,
                request.reason,
                request.lot_ids,
            )
            order.remaining -= quantity
            order.status = "filled" if order.remaining == 0 else "partially_filled"
            if self.capacity is not None:
                self.capacity.consume(code, quantity * price)
            self.event(order, date, order.status, "executed", quantity, price, fee)

    def finish(self, date):
        for order in self.active():
            order.status = "open_at_end"
            self.event(order, date, "open_at_end", "simulation_ended")
