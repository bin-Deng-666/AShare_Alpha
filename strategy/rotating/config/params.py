"""Frozen defaults for the rotating daily strategy."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Params:
    sleeves: int = 5
    holding_sessions: int = 5
    names_per_sleeve: int = 5
    enter_quantile: float = 0.10
    exit_quantile: float = 0.25
    max_stock_weight: float = 0.08
    max_industry_weight: float = 0.20
    max_adv_fraction: float = 0.02
    lot_size: int = 100

    enable_realistic_costs: bool = True
    cost_bps: float = 10.0
    commission_bps: float = 2.5
    min_commission: float = 5.0
    transfer_fee_bps: float = 0.1
    stamp_tax_bps: float = 5.0
    historical_costs: bool = True

    enable_open_slippage: bool = True
    slippage_bps_large: float = 3.0
    slippage_bps_mid: float = 6.0
    slippage_bps_small: float = 10.0
    slippage_mcap_large: float = 50e9
    slippage_mcap_mid: float = 10e9
    slippage_amt_large: float = 500e6
    slippage_amt_mid: float = 100e6


DEFAULT_PARAMS = Params()
