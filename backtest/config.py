"""Run-level execution assumptions; independent of strategy parameters."""

from dataclasses import dataclass, field
from decimal import Decimal
from .models import finite, integer, timestamp


@dataclass(frozen=True)
class CostConfig:
    commission_bps: float = 2.5
    min_commission: float = 5.0
    transfer_fee_bps: float = 0.1
    stamp_tax_bps: float = 5.0
    historical: bool = True

    def __post_init__(self):
        for name in (
            "commission_bps",
            "min_commission",
            "transfer_fee_bps",
            "stamp_tax_bps",
        ):
            finite(getattr(self, name), name)
        if not isinstance(self.historical, bool):
            raise ValueError("historical must be boolean")


@dataclass(frozen=True)
class SlippageConfig:
    bps: float = 5.0

    def __post_init__(self):
        finite(self.bps, "slippage_bps")


@dataclass(frozen=True)
class BacktestConfig:
    start: str
    end: str
    initial_capital: float = 1_000_000
    costs: CostConfig = field(default_factory=CostConfig)
    slippage: SlippageConfig = field(default_factory=SlippageConfig)
    corporate_actions: str = "strict"
    seed: int = 0
    trading_days: int = 242
    cash_rate: float = 0.0
    dividend_tax_rate: float = 0.2
    cash_yield: str = "fixed"

    def __post_init__(self):
        start, end = timestamp(self.start).normalize(), timestamp(self.end).normalize()
        if start > end:
            raise ValueError("start must not follow end")
        if finite(self.initial_capital, "initial_capital") <= 0:
            raise ValueError("Capital must be positive")
        capital = Decimal(str(self.initial_capital))
        if capital < Decimal(".01") or capital != capital.quantize(Decimal(".01")):
            raise ValueError("Initial capital must be an exact positive cent amount")
        if self.corporate_actions not in ("strict", "ex_date_proxy"):
            raise ValueError("Unknown corporate-action policy")
        if not isinstance(self.costs, CostConfig) or not isinstance(
            self.slippage, SlippageConfig
        ):
            raise TypeError("Use typed cost and slippage configurations")
        integer(self.seed, "seed")
        integer(self.trading_days, "trading_days", minimum=1)
        if self.cash_yield not in ("fixed", "input"):
            raise ValueError("cash_yield must be fixed or input")
        if self.cash_yield == "input" and self.cash_rate != 0:
            raise ValueError("Dated cash yield and a fixed rate are exclusive")
        finite(self.cash_rate, "cash_rate")
        if finite(self.dividend_tax_rate, "dividend_tax_rate") > 1:
            raise ValueError("Tax rate must not exceed 1")
        object.__setattr__(self, "start", str(start.date()))
        object.__setattr__(self, "end", str(end.date()))
