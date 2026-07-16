"""Typed order-attempt audit records for rotating-strategy execution."""

from __future__ import annotations

from enum import Enum
from typing import Any

import pandas as pd


class OrderOutcome(str, Enum):
    CANCELLED = "cancelled"
    DEFERRED = "deferred"
    FILLED = "filled"
    OPEN_AT_END = "open_at_end"


class OrderReason(str, Enum):
    BELOW_BOARD_LOT_OR_CASH = "below_board_lot_or_cash"
    ENTRY_GATE_CLOSED = "entry_gate_closed"
    EXECUTED = "executed"
    INVALID_OPEN = "invalid_open"
    MISSING_BAR = "missing_bar"
    ONE_PRICE_LIMIT = "one_price_limit"
    POSITION_ALREADY_CLOSED = "position_already_closed"
    SIMULATION_ENDED = "simulation_ended"
    SLEEVE_FULL = "sleeve_full"
    SUSPENDED = "suspended"
    UNSUPPORTED_CORPORATE_ACTION = "unsupported_corporate_action"


class OrderAuditLedger:
    """Own order identifiers and append schema-stable attempt records."""

    def __init__(self) -> None:
        self._next_order_id = 1
        self._rows: list[dict[str, Any]] = []

    def new_order(self, values: dict[str, Any]) -> dict[str, Any]:
        order = dict(values)
        order["order_id"] = self._next_order_id
        self._next_order_id += 1
        return order

    def record(
        self,
        order: dict[str, Any],
        execution_date: pd.Timestamp,
        *,
        outcome: OrderOutcome,
        reason: OrderReason,
        requested_amount: float | None,
        filled_amount: float = 0.0,
    ) -> None:
        self._rows.append(
            {
                "order_id": int(order["order_id"]),
                "signal_date": order["signal_date"],
                "attempt_date": execution_date,
                "code": order["code"],
                "side": str(order.get("side", "BUY")).upper(),
                "sleeve": order.get("sleeve"),
                "outcome": outcome.value,
                "reason": reason.value,
                "requested_amount": requested_amount,
                "filled_amount": float(filled_amount),
            }
        )

    def to_frame(self) -> pd.DataFrame:
        return pd.DataFrame(self._rows)
