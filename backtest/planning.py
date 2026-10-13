"""Targets replace pending target orders and plan from actual close holdings."""

import math
import pandas as pd
from .models import OrderRequest, Side
from .rules import board_rule


def plan_target(target, account, broker, market, date, session):
    for order in list(broker.active()):
        if order.origin == "targets":
            broker.cancel(order.order_id, date, "target_replaced")
    snapshot = account.snapshot(session)
    codes = sorted(set(target.weights) | set(snapshot.positions))
    decisions = []
    for code in codes:
        current = account.quantity(code)
        weight = target.weights.get(code, 0)
        if weight == 0:
            desired = 0
        else:
            row = market.row(date, code)
            price = None if row is None else row.get("close")
            if (
                price is None
                or pd.isna(price)
                or not math.isfinite(float(price))
                or price <= 0
            ):
                decisions.append(
                    {
                        "date": date,
                        "code": code,
                        "target_weight": weight,
                        "outcome": "unplanned",
                        "reason": "missing_signal_price",
                    }
                )
                continue
            desired = int(snapshot.equity * weight / float(price))
        delta = desired - current
        rule = board_rule(code)
        quantity = rule.floor_buy(delta) if delta > 0 else -delta
        if delta < 0 and quantity != current:
            quantity = rule.floor_buy(quantity)
        decisions.append(
            {
                "date": date,
                "code": code,
                "target_weight": weight,
                "actual_quantity": current,
                "desired_quantity": desired,
                "planned_quantity": quantity,
                "reason": target.reason,
                "outcome": "planned" if quantity else "unchanged",
            }
        )
        if quantity:
            broker.submit(
                OrderRequest(
                    code,
                    Side.BUY if delta > 0 else Side.SELL,
                    quantity=quantity,
                    reason=target.reason,
                    valid_sessions=target.valid_sessions,
                ),
                date,
                session,
                origin="targets",
            )
    return decisions
