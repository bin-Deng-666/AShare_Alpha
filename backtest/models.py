"""Public, validated decisions and immutable execution snapshots."""

from __future__ import annotations
from dataclasses import dataclass, field
from enum import Enum
from math import isfinite
from numbers import Integral
from types import MappingProxyType
from typing import Mapping
import pandas as pd


def symbol(value) -> str:
    text = str(value).strip()
    if not text.isdigit() or len(text) > 6:
        raise ValueError(f"Invalid A-share symbol: {value!r}")
    return text.zfill(6)


def finite(value, name, *, minimum=0):
    if isinstance(value, bool):
        raise ValueError(f"{name} must be numeric")
    number = float(value)
    if not isfinite(number) or number < minimum:
        raise ValueError(f"Invalid {name}: {value!r}")
    return number


def integer(value, name, *, minimum=0):
    if isinstance(value, bool) or not isinstance(value, Integral) or value < minimum:
        raise ValueError(f"{name} must be an integer >= {minimum}")
    return int(value)


def timestamp(value):
    result = pd.Timestamp(value)
    if pd.isna(result):
        raise ValueError("Missing timestamp")
    return (
        result.tz_convert("Asia/Shanghai").tz_localize(None)
        if result.tzinfo
        else result
    )


class Side(str, Enum):
    BUY = "BUY"
    SELL = "SELL"


@dataclass(frozen=True)
class DataRequirements:
    mode: str = "targets"
    fields: tuple[str, ...] = ("close",)
    features: tuple[str, ...] = ()
    warmup_sessions: int = 0
    code_dependencies: tuple[str, ...] = ()

    def __post_init__(self):
        if self.mode not in ("targets", "orders"):
            raise ValueError("mode must be targets or orders")
        integer(self.warmup_sessions, "warmup_sessions")
        object.__setattr__(self, "fields", tuple(self.fields))
        object.__setattr__(self, "features", tuple(self.features))
        object.__setattr__(self, "code_dependencies", tuple(self.code_dependencies))


@dataclass(frozen=True)
class TargetPortfolio:
    weights: Mapping[str, float]
    reason: str = "rebalance"
    valid_sessions: int = 1

    def __post_init__(self):
        weights = {symbol(k): finite(v, "weight") for k, v in self.weights.items()}
        if len(weights) != len(self.weights):
            raise ValueError("Duplicate normalized symbols")
        if sum(weights.values()) > 1 + 1e-12:
            raise ValueError("Long-only weights must sum to <= 1")
        integer(self.valid_sessions, "valid_sessions", minimum=1)
        object.__setattr__(self, "weights", MappingProxyType(weights))


@dataclass(frozen=True)
class OrderRequest:
    code: str
    side: Side | str
    quantity: int | None = None
    budget: float | None = None
    reason: str = "strategy"
    valid_sessions: int = 1
    priority: int = 0
    client_id: str | None = None
    lot_ids: tuple[str, ...] = ()
    tags: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self):
        object.__setattr__(self, "code", symbol(self.code))
        object.__setattr__(self, "side", Side(self.side))
        if (self.quantity is None) == (self.budget is None):
            raise ValueError("Provide quantity or budget, exclusively")
        if self.quantity is not None:
            integer(self.quantity, "quantity", minimum=1)
        if self.budget is not None:
            if self.side != Side.BUY:
                raise ValueError("Only BUY supports a cash budget")
            if finite(self.budget, "budget") <= 0:
                raise ValueError("Budget must be positive")
        integer(self.valid_sessions, "valid_sessions", minimum=1)
        if isinstance(self.priority, bool) or not isinstance(self.priority, Integral):
            raise ValueError("priority must be an integer")
        if self.lot_ids and self.side != Side.SELL:
            raise ValueError("Only SELL can specify lots")
        lots = tuple(self.lot_ids)
        if len(lots) != len(set(lots)) or not all(
            isinstance(x, str) and x for x in lots
        ):
            raise ValueError("Invalid lot IDs")
        if self.client_id is not None and (
            not isinstance(self.client_id, str) or not self.client_id
        ):
            raise ValueError("Invalid client_id")
        tags = dict(self.tags)
        if not all(isinstance(k, str) and isinstance(v, str) for k, v in tags.items()):
            raise ValueError("Tags must contain strings")
        object.__setattr__(self, "lot_ids", lots)
        object.__setattr__(self, "tags", MappingProxyType(tags))


@dataclass(frozen=True)
class OrderBatch:
    orders: tuple[OrderRequest, ...] = ()
    cancel: tuple[str, ...] = ()

    def __post_init__(self):
        object.__setattr__(self, "orders", tuple(self.orders))
        object.__setattr__(self, "cancel", tuple(self.cancel))
        if not all(isinstance(x, OrderRequest) for x in self.orders):
            raise TypeError("Expected OrderRequest")
        if not all(isinstance(x, str) and x for x in self.cancel):
            raise ValueError("Invalid cancellation IDs")


@dataclass(frozen=True)
class ExecutionEvent:
    order_id: str | None
    date: pd.Timestamp
    code: str
    status: str
    reason: str
    quantity: int = 0
    price: float | None = None
    fee: float = 0
    lot_id: str | None = None
    cash_amount: float = 0.0


@dataclass(frozen=True)
class LotSnapshot:
    lot_id: str
    code: str
    quantity: int
    sellable_quantity: int
    cost_basis: float
    acquired_at: pd.Timestamp
    tags: Mapping[str, str]


@dataclass(frozen=True)
class PositionSnapshot:
    code: str
    quantity: int
    sellable_quantity: int
    cost_basis: float
    market_value: float


@dataclass(frozen=True)
class PortfolioSnapshot:
    cash: float
    equity: float
    market_value: float
    receivables: float
    positions: Mapping[str, PositionSnapshot]
    lots: tuple[LotSnapshot, ...]


@dataclass(frozen=True)
class PendingOrder:
    order_id: str
    request: OrderRequest
    remaining_quantity: int | None
    submitted_at: pd.Timestamp
    expires_at: pd.Timestamp
