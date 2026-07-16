"""A-share execution costs, slippage, lot helpers.

Strategy-independent: accepts any params-like object via duck typing.
Required attributes on params (when respective flags are enabled):
  - enable_realistic_costs, cost_bps, commission_bps, min_commission,
    transfer_fee_bps, stamp_tax_bps
  - enable_open_slippage, slippage_bps_large/mid/small,
    slippage_mcap_large/mid, slippage_amt_large/mid
"""

from __future__ import annotations

from typing import Any, Protocol

import numpy as np
import pandas as pd


class CostParams(Protocol):
    """Structural type for cost-related parameters (duck-typed)."""
    enable_realistic_costs: bool
    cost_bps: float
    commission_bps: float
    min_commission: float
    transfer_fee_bps: float
    stamp_tax_bps: float
    enable_open_slippage: bool
    slippage_bps_large: float
    slippage_bps_mid: float
    slippage_bps_small: float
    slippage_mcap_large: float
    slippage_mcap_mid: float
    slippage_amt_large: float
    slippage_amt_mid: float


def flat_fee(notional: float, cost_bps: float) -> float:
    """Legacy flat one-way fee."""
    return abs(notional) * cost_bps / 10000.0


def trade_fee(
    notional: float,
    side: str,
    params: Any,
    trade_date: pd.Timestamp | str | None = None,
) -> float:
    """Buy/sell fee, optionally switching statutory rates by execution date."""
    n = abs(float(notional))
    if n <= 0:
        return 0.0
    if not getattr(params, "enable_realistic_costs", False):
        return flat_fee(n, params.cost_bps)
    commission = max(float(params.min_commission), n * float(params.commission_bps) / 10000.0)
    transfer_fee_bps = float(params.transfer_fee_bps)
    if getattr(params, "historical_costs", False) and trade_date is not None:
        transfer_fee_bps = (
            0.2 if pd.Timestamp(trade_date) < pd.Timestamp("2022-04-29") else 0.1
        )
    fee = commission + n * transfer_fee_bps / 10000.0
    if str(side).upper() == "SELL":
        stamp_tax_bps = float(params.stamp_tax_bps)
        if getattr(params, "historical_costs", False) and trade_date is not None:
            stamp_tax_bps = (
                10.0 if pd.Timestamp(trade_date) < pd.Timestamp("2023-08-28") else 5.0
            )
        fee += n * stamp_tax_bps / 10000.0
    return float(fee)


def slippage_bps_for_row(row: pd.Series | dict | None, params: Any) -> float:
    """Adverse open slippage bps from signal-day liquidity (no look-ahead)."""
    if not getattr(params, "enable_open_slippage", False):
        return 0.0
    if row is None:
        return float(params.slippage_bps_mid)

    def _get(k, default=np.nan):
        if hasattr(row, "get"):
            return row.get(k, default)
        if k in getattr(row, "index", []):
            return row[k]
        return default

    try:
        mcap = float(_get("float_market_cap", np.nan))
    except (TypeError, ValueError):
        mcap = np.nan
    try:
        amt = float(_get("amount", np.nan))
    except (TypeError, ValueError):
        amt = np.nan

    is_hs = bool(_get("is_hs300", False)) or str(_get("index_tier", "") or "") == "hs300"
    if is_hs or (np.isfinite(mcap) and mcap >= float(params.slippage_mcap_large)):
        bps = float(params.slippage_bps_large)
    elif np.isfinite(mcap) and mcap >= float(params.slippage_mcap_mid):
        bps = float(params.slippage_bps_mid)
    elif np.isfinite(amt) and amt >= float(params.slippage_amt_large):
        bps = float(params.slippage_bps_large)
    elif np.isfinite(amt) and amt >= float(params.slippage_amt_mid):
        bps = float(params.slippage_bps_mid)
    elif np.isfinite(mcap) or np.isfinite(amt):
        bps = float(params.slippage_bps_small)
    else:
        bps = float(params.slippage_bps_mid)
    return bps


def apply_slippage(price: float, side: str, slip_bps: float) -> float:
    if not np.isfinite(price) or price <= 0:
        return price
    bps = float(slip_bps or 0.0)
    if bps <= 0:
        return float(price)
    if str(side).upper() == "BUY":
        return float(price) * (1.0 + bps / 10000.0)
    return float(price) * (1.0 - bps / 10000.0)


def lot_floor(shares: int, lot: int) -> int:
    return (shares // lot) * lot
