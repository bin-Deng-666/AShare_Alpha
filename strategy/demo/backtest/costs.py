"""Execution costs, slippage, helpers."""

from __future__ import annotations

import numpy as np
import pandas as pd

from config.params import Params

def _fee(notional: float, cost_bps: float) -> float:
    """Legacy flat one-way fee."""
    return abs(notional) * cost_bps / 10000.0


def _trade_fee(notional: float, side: str, params: Params) -> float:
    """Buy/sell fee. Realistic mode: commission (+ stamp on sell) + transfer."""
    n = abs(float(notional))
    if n <= 0:
        return 0.0
    if not getattr(params, "enable_realistic_costs", False):
        return _fee(n, params.cost_bps)
    commission = max(float(params.min_commission), n * float(params.commission_bps) / 10000.0)
    fee = commission + n * float(params.transfer_fee_bps) / 10000.0
    if str(side).upper() == "SELL":
        fee += n * float(params.stamp_tax_bps) / 10000.0
    return float(fee)


def _slippage_bps_for_row(row: pd.Series | dict | None, params: Params) -> float:
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

    # Prefer float mcap tiers; fall back to amount; HS300 treated as large
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


def _apply_slip(price: float, side: str, slip_bps: float) -> float:
    if not np.isfinite(price) or price <= 0:
        return price
    bps = float(slip_bps or 0.0)
    if bps <= 0:
        return float(price)
    if str(side).upper() == "BUY":
        return float(price) * (1.0 + bps / 10000.0)
    return float(price) * (1.0 - bps / 10000.0)


def _lot_floor(shares: int, lot: int) -> int:
    return (shares // lot) * lot



