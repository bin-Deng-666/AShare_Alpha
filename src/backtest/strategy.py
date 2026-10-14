"""Strategy lifecycle. Decisions request trades; callbacks never own the account."""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Mapping, Any
import pandas as pd
from .models import DataRequirements, PortfolioSnapshot, PendingOrder
from .data import DataView


@dataclass(frozen=True)
class StrategyContext:
    asof: pd.Timestamp
    session_index: int
    data: DataView
    portfolio: PortfolioSnapshot
    pending_orders: tuple[PendingOrder, ...]
    rng: Any
    _returns: tuple[float, ...] = ()
    calendar: tuple[pd.Timestamp, ...] = ()

    @property
    def returns(self):
        return pd.Series(self._returns, dtype=float)


class Strategy(ABC):
    def __init__(self, params=None):
        self.params = params

    def requirements(self):
        return DataRequirements()

    def on_start(self, ctx):
        pass

    @abstractmethod
    def on_close(self, ctx):
        """Return None, TargetPortfolio, or OrderBatch in the declared mode."""

    def on_execution(self, ctx, events):
        pass

    def on_end(self, result_view) -> Mapping[str, Any]:
        return {}
