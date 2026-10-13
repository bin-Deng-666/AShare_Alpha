"""Validated A-share fees and adverse execution prices.

Rates are bps, notional and capitalization thresholds are RMB. Existing public
helpers remain callable; the engine uses the same fee calculation.
"""

from __future__ import annotations
from math import isfinite
from typing import Any
import pandas as pd
from .config import CostConfig
from .models import Side, finite, integer, timestamp
from .rules import cents, price_cents


def fee_cents(notional_cents: int, side: Side | str, config: CostConfig, trade_date):
    integer(notional_cents, "notional_cents")
    side = Side(side)
    if notional_cents == 0:
        return 0
    date = timestamp(trade_date).normalize()
    transfer, stamp = config.transfer_fee_bps, config.stamp_tax_bps
    if config.historical:
        if date < pd.Timestamp("2015-08-01"):
            raise ValueError(
                "Historical fees before 2015-08-01 require an explicit static cost configuration"
            )
        transfer = 0.2 if date < pd.Timestamp("2022-04-29") else 0.1
        stamp = 10 if date < pd.Timestamp("2023-08-28") else 5
    notional = notional_cents / 100
    commission = max(config.min_commission, notional * config.commission_bps / 10000)
    return cents(
        commission
        + notional * transfer / 10000
        + (notional * stamp / 10000 if side == Side.SELL else 0)
    )


def trade_fee(notional: float, side: str, params: Any, trade_date=None) -> float:
    side = side if isinstance(side, Side) else Side(str(side).upper())
    n = finite(abs(float(notional)), "notional")
    if n == 0:
        return 0.0
    if isinstance(params, CostConfig):
        if trade_date is None and params.historical:
            raise ValueError("Historical fees require trade_date")
        return fee_cents(cents(n), side, params, trade_date or "2026-01-01") / 100
    if not getattr(params, "enable_realistic_costs", False):
        return n * finite(params.cost_bps, "cost_bps") / 10000
    config = CostConfig(
        commission_bps=params.commission_bps,
        min_commission=params.min_commission,
        transfer_fee_bps=params.transfer_fee_bps,
        stamp_tax_bps=params.stamp_tax_bps,
        historical=getattr(params, "historical_costs", False),
    )
    return trade_fee(n, side, config, trade_date)


def apply_slippage(price: float, side: str, slip_bps: float) -> float:
    price = finite(price, "price")
    if price <= 0:
        raise ValueError("Price must be positive")
    side = side if isinstance(side, Side) else Side(str(side).upper())
    bps = finite(slip_bps, "slip_bps")
    value = price * (1 + bps / 10000 if side == Side.BUY else 1 - bps / 10000)
    if value <= 0:
        raise ValueError("Slippage produced a nonpositive price")
    return value


def fill_price_cents(price, side, bps):
    return price_cents(apply_slippage(price, side, bps))


def slippage_bps_for_row(row, params):
    if not getattr(params, "enable_open_slippage", False):
        return 0.0
    if row is None:
        return finite(params.slippage_bps_mid, "slippage_bps_mid")

    def number(key):
        value = row.get(key)
        try:
            result = float(value)
        except (ValueError, TypeError):
            return float("nan")
        return result

    hs = row.get("is_hs300")
    tier = row.get("index_tier")
    is_hs = (not pd.isna(hs) and bool(hs)) or (not pd.isna(tier) and tier == "hs300")
    mcap, amount = number("float_market_cap"), number("amount")
    if is_hs or (isfinite(mcap) and mcap >= params.slippage_mcap_large):
        key = "large"
    elif isfinite(mcap) and mcap >= params.slippage_mcap_mid:
        key = "mid"
    elif isfinite(amount) and amount >= params.slippage_amt_large:
        key = "large"
    elif isfinite(amount) and amount >= params.slippage_amt_mid:
        key = "mid"
    elif isfinite(mcap) or isfinite(amount):
        key = "small"
    else:
        key = "mid"
    return finite(getattr(params, "slippage_bps_" + key), "slippage_bps_" + key)


def lot_floor(shares, lot):
    finite(shares, "shares")
    integer(lot, "lot", minimum=1)
    return int(shares) // lot * lot
