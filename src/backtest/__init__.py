"""Strategy-class-driven A-share daily backtesting."""

from .config import BacktestConfig, CapacityConfig, CostConfig, SlippageConfig
from .data import MarketData
from .engine import BacktestEngine
from .models import DataRequirements, OrderBatch, OrderRequest, Side, TargetPortfolio
from .results import BacktestError, BacktestResult
from .strategy import Strategy, StrategyContext
from .experiments import RunSpec, compare, parameter_grid

__all__ = [
    "BacktestConfig",
    "CapacityConfig",
    "CostConfig",
    "SlippageConfig",
    "MarketData",
    "BacktestEngine",
    "DataRequirements",
    "OrderBatch",
    "OrderRequest",
    "Side",
    "TargetPortfolio",
    "BacktestError",
    "BacktestResult",
    "Strategy",
    "StrategyContext",
    "RunSpec",
    "compare",
    "parameter_grid",
]
