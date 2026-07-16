"""Strategy parameters for regime_sparse (**v0.2**).

Layers:
- ``AlphaCore`` — universe / regime / patterns / book / exits
- ``Overlays`` — Risk-On, SOTA turn-stab, crowd-traj, MACD/KDJ rank
- ``Execution`` — capital, fees, slippage
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, fields, replace
from pathlib import Path

STRATEGY_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = STRATEGY_ROOT.parents[1]
DATA_ROOT = REPO_ROOT / "data"
CATALOG_PATH = DATA_ROOT / "catalog" / "quant.duckdb"
CACHE_DIR = STRATEGY_ROOT / "cache"
REPORT_DIR = STRATEGY_ROOT / "reports"

BENCHMARK = "沪深300"
BENCHMARK_CODE = "000300"
SAMPLE_START = "2020-01-01"

TECH_INDUSTRY_SUBSTR = (
    "计算机",
    "通信",
    "电子",
    "软件",
    "互联网",
    "电信",
    "信息",
)


@dataclass(frozen=True)
class AlphaCore:
    """Mainline alpha + book construction."""

    # windows
    amt_lookback: int = 60
    ma_short: int = 5
    ma_long: int = 20
    ret_short: int = 5
    ret_crowd: int = 20
    dry_up_lookback: int = 10
    max_hold_days: int = 18
    hold_extend_days: int = 10

    # universe
    min_amount: float = 5.0e7
    min_float_mcap: float = 2.0e9
    min_list_days: int = 60
    exclude_bj: bool = True

    # volume regime
    expand_vs_med: float = 1.15
    expand_ma_ratio: float = 1.05
    shrink_vs_med: float = 0.85
    shrink_ma_ratio: float = 0.95
    climax_vs_med: float = 2.0

    # patterns
    thrust_amt_vs_med: float = 1.2
    dry_amt_vs_med: float = 0.75
    climax_amt_vs_med: float = 2.2
    break_ret: float = -0.03
    stall_dist_high: float = -0.03

    # portfolio (open_A only)
    top_n: int = 5
    equal_weight: bool = True
    max_entries_per_day: int = 2
    min_position_mult_to_buy: float = 0.7
    allowed_attack_modes: tuple = ("full_attack",)
    allowed_valuation: tuple = ("cheap", "neutral")
    min_amt_vs_med: float = 1.3
    require_entry_confirm: bool = False
    entry_confirm_days: int = 1
    val_extreme_pct: float = 0.95

    # exits
    stop_loss_pct: float = -0.12
    soft_stop_pct: float = -0.08
    enable_soft_stop: bool = True
    soft_stop_require_stress: bool = True
    take_profit_pct: float = 0.15
    min_hold_days_before_stop: int = 3
    avoid_mode: str = "ignore"  # ignore | full | reduce_half | soft
    reduce_frac: float = 0.5
    trail_activate_pct: float = 0.08
    trail_drawdown_pct: float = 0.07
    enable_hold_extend: bool = True
    extend_requires_profit: bool = True
    enable_failure_exit: bool = True
    failure_exit_min_days: int = 3
    failure_exit_max_pnl: float = 0.05
    failure_hold_ok_streak: int = 99
    enable_stall_take: bool = True
    stall_take_min_days: int = 12
    stall_take_min_pnl: float = 0.08
    enable_stop_cooldown: bool = True
    stop_cooldown_days: int = 20


@dataclass(frozen=True)
class Overlays:
    """Live overlays kept in the slim engine."""

    # SOTA turn stability
    enable_sota_factors: bool = True
    sota_factor_lookback: int = 20
    enable_turn_stab_score: bool = True
    sota_score_floor: float = 0.75

    # MACD / KDJ ranking only (never hard-filter open_A)
    enable_macd_rank: bool = True
    macd_rank_floor: float = 0.85
    enable_kdj_rank: bool = True
    kdj_rank_floor: float = 0.85

    # mid-crowd rising trajectory entry (needs crowding_score on panel)
    enable_crowd_traj: bool = True
    crowd_traj_lookback: int = 5
    crowd_traj_min_delta: float = 0.02
    crowd_traj_max_score: float = 0.65

    # Risk-On: amplify exposure on good days (close[T] → next open)
    enable_risk_on: bool = True
    risk_on_ma_fast: int = 20
    risk_on_ma_slow: int = 60
    risk_on_require_abs_mom: bool = True
    risk_on_abs_mom_days: int = 60
    risk_on_require_breadth: bool = True
    risk_on_breadth_min: float = 0.30
    risk_on_exit_policy: bool = True
    risk_on_take_profit_pct: float = 0.15
    risk_on_disable_tp: bool = False
    risk_on_extra_hold_days: int = 0
    risk_on_trail_activate_pct: float = 0.08
    risk_on_trail_drawdown_pct: float = 0.07
    risk_on_exposure: bool = True
    risk_on_exposure_mult: float = 1.25
    risk_on_exposure_cap: float = 1.40
    risk_on_extra_entries: int = 1


@dataclass(frozen=True)
class Execution:
    """Capital ledger + A-share realistic costs."""

    initial_capital: float = 1_000_000.0
    lot_size: int = 100
    cost_bps: float = 15.0
    enable_realistic_costs: bool = True
    commission_bps: float = 2.5
    min_commission: float = 5.0
    stamp_tax_bps: float = 5.0
    transfer_fee_bps: float = 0.1
    enable_open_slippage: bool = True
    slippage_bps_large: float = 3.0
    slippage_bps_mid: float = 8.0
    slippage_bps_small: float = 20.0
    slippage_mcap_large: float = 50_000_000_000.0
    slippage_mcap_mid: float = 10_000_000_000.0
    slippage_amt_large: float = 500_000_000.0
    slippage_amt_mid: float = 100_000_000.0
    max_single_weight: float = 0.25
    enable_cash_refill: bool = True
    cash_refill_ratio: float = 0.55
    cash_refill_extra_entries: int = 1
    cash_refill_require_full_attack: bool = False


@dataclass(frozen=True)
class Params(AlphaCore, Overlays, Execution):
    version: str = "v0.2"


DEFAULT_PARAMS = Params()


def safe_replace(p: Params | None = None, **kwargs) -> Params:
    base = p or DEFAULT_PARAMS
    valid = {f.name for f in fields(base)}
    return replace(base, **{k: v for k, v in kwargs.items() if k in valid})


def params_dict(p: Params | None = None) -> dict:
    return asdict(p or DEFAULT_PARAMS)
