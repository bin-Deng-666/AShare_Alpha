"""Strategy parameters for crowding_price_volume (**v1.0**).

Layout (read this first):
- ``AlphaCore`` — universe / regime / patterns / portfolio / exits that define the book
- ``LiveOverlays`` — confirmed v1.0 overlays (SOTA, flow, risk-on, mainline, …)
- ``DormantAblation`` — default-OFF switches kept so archive ablations can ``replace()``
- ``Execution`` — capital, fees, slippage

``Params`` merges all layers. Prefer touching ``AlphaCore`` / ``LiveOverlays`` /
``Execution`` for live work; treat ``DormantAblation`` as frozen experiment surface.
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
    """Mainline alpha + book construction (always relevant)."""

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

    # portfolio
    top_n: int = 5
    satellite_n: int = 2  # only when enable_open_c
    satellite_weight_frac: float = 0.5
    equal_weight: bool = True
    max_entries_per_day: int = 2
    max_satellite_entries_per_day: int = 1
    min_position_mult_to_buy: float = 0.7
    enable_open_b: bool = False
    enable_open_c: bool = False
    allowed_attack_modes: tuple = ("full_attack",)
    open_c_attack_modes: tuple = ("full_attack", "structure_attack")
    allowed_valuation: tuple = ("cheap", "neutral")
    min_amt_vs_med: float = 1.3
    require_entry_confirm: bool = False
    entry_confirm_days: int = 1
    val_extreme_pct: float = 0.95

    # exits (live)
    stop_loss_pct: float = -0.12
    soft_stop_pct: float = -0.08
    enable_soft_stop: bool = True
    soft_stop_require_stress: bool = True
    take_profit_pct: float = 0.15
    min_hold_days_before_stop: int = 3
    avoid_mode: str = "ignore"
    reduce_frac: float = 0.5
    trail_activate_pct: float = 0.08
    trail_drawdown_pct: float = 0.07
    enable_hold_extend: bool = True
    extend_requires_profit: bool = True
    enable_crowded_soft_exit: bool = True
    enable_failure_exit: bool = True
    failure_exit_min_days: int = 3
    failure_exit_max_pnl: float = 0.05
    failure_hold_ok_streak: int = 99
    enable_stall_take: bool = True
    stall_take_min_days: int = 12
    stall_take_min_pnl: float = 0.08
    enable_stop_cooldown: bool = True
    stop_cooldown_days: int = 20
    enable_mfe_exit: bool = True
    mfe_min_r: float = 0.2
    mfe_check_days_default: int = 16
    mfe_check_days_full_attack: int = 22
    mfe_require_not_hold_ok: bool = True


@dataclass(frozen=True)
class LiveOverlays:
    """Overlays that ship ON in v1.0 (ablation-confirmed)."""

    # SOTA score (turn stability)
    enable_sota_factors: bool = True
    sota_factor_lookback: int = 20
    enable_turn_stab_score: bool = True
    sota_score_floor: float = 0.75
    max_ret_5_entry: float = 0.08
    max_day_ret_20: float = 0.095
    max_ret_vol_pctile: float = 0.85
    max_turn_cv_pctile: float = 0.85

    # panel-baked crowding / earnings / index tier
    enable_limit_up_crowding: bool = True
    enable_earnings_blackout: bool = True
    earnings_blackout_block_entry: bool = True
    earnings_blackout_block_extend: bool = True
    enable_index_tier_sizing: bool = True
    hs300_size_mult: float = 1.15
    zz500_size_mult: float = 1.0
    other_size_mult: float = 1.0
    hs300_max_weight: float = 0.30
    zz500_max_weight: float = 0.25
    other_max_weight: float = 0.25

    # northbound boost + mild margin stress
    enable_nb_flow_boost: bool = True
    nb_strong_size_mult: float = 1.15
    enable_margin_stress: bool = True
    mg_stress_size_mult: float = 0.95
    mg_stress_block_extend: bool = True
    mg_stress_as_stress: bool = False
    # kept for soft path wiring (default OFF)
    nb_weak_size_mult: float = 0.85
    nb_weak_block_extend: bool = True
    nb_weak_as_stress: bool = True

    # Risk-On
    enable_risk_on: bool = True
    risk_on_require_abs_mom: bool = True
    risk_on_abs_mom_days: int = 60
    risk_on_require_breadth: bool = True
    risk_on_breadth_min: float = 0.30
    risk_on_exit_policy: bool = True
    risk_on_take_profit_pct: float = 0.15
    risk_on_disable_tp: bool = False
    risk_on_skip_mfe: bool = True
    risk_on_extra_hold_days: int = 0
    risk_on_trail_activate_pct: float = 0.08
    risk_on_trail_drawdown_pct: float = 0.07
    risk_on_exposure: bool = True
    risk_on_exposure_mult: float = 1.25
    risk_on_exposure_cap: float = 1.40
    risk_on_extra_entries: int = 1
    risk_on_require_mkt_expand: bool = False

    # crowd traj + scarcity
    enable_crowd_traj: bool = True
    crowd_traj_lookback: int = 5
    crowd_traj_min_delta: float = 0.02
    crowd_traj_max_score: float = 0.65
    enable_scarcity_premium: bool = True
    scarcity_lookback: int = 60
    scarcity_low_pctile: float = 0.25
    scarcity_high_pctile: float = 0.75
    scarcity_boost_mult: float = 1.35
    scarcity_cut_mult: float = 0.85
    scarcity_boost_require_risk_on: bool = True

    # false-bull fuse
    enable_false_bull_fuse: bool = True
    false_bull_lookback: int = 5
    false_bull_block_both_shrink: bool = True
    false_bull_min_streak: int = 2
    false_bull_gate_exposure: bool = True
    false_bull_gate_risk_on: bool = False
    false_bull_block_opens: bool = False
    false_bull_size_cut: float = 1.0

    # mainline diffusion
    enable_mainline_diffusion: bool = True
    mainline_peak_lookback: int = 20
    mainline_peak_pct: float = 0.90
    mainline_still_pct: float = 0.80
    mainline_pullback_lookback: int = 5
    mainline_pullback_min: float = -0.15
    mainline_pullback_max: float = -0.01
    mainline_top_n: int = 1
    mainline_allow_structure: bool = True
    mainline_allow_mid_crowd: bool = False
    mainline_mid_max_score: float = 0.65
    mainline_score_boost: float = 1.0
    mainline_require_risk_on: bool = False
    mainline_max_extra_per_day: int = 1


@dataclass(frozen=True)
class DormantAblation:
    """Default-OFF experiment surface. Keep for archive ``replace()``; do not expand casually."""

    # unused SOTA score/filter variants
    enable_low_ivol_score: bool = False
    enable_path_eff_score: bool = False
    enable_anti_chase_score: bool = False
    anti_chase_score_coef: float = 1.5
    enable_turn_stab_filter: bool = False
    enable_low_ivol_filter: bool = False
    enable_anti_chase_filter: bool = False

    # industry crowding soft domain (hurt in ablation)
    enable_ind_crowd_domain: bool = False
    ind_crowd_high_pct: float = 0.90
    ind_crowd_score_penalty: float = 0.92
    ind_crowd_hold_cut_days: int = 0
    ind_crowd_block_extend: bool = True
    ind_crowd_tighter_trail: bool = False
    earnings_blackout_take_profit: bool = False
    earnings_use_event_blackout: bool = False

    # nb soft cut (hurt)
    enable_nb_flow_soft: bool = False

    # optional exits / sizing
    enable_sector_shrink_exit: bool = False
    enable_stale_cut: bool = False
    stale_min_days: int = 10
    stale_max_pnl: float = 0.0
    enable_atr_stop: bool = False
    atr_period: int = 14
    atr_mult: float = 3.0
    enable_structure_stop: bool = False
    structure_stop_buffer: float = 0.005
    enable_progress_exit: bool = False
    progress_check_days: int = 14
    progress_min_pnl: float = 0.0
    enable_smart_max_hold: bool = False
    smart_hold_trim_pnl: float = 0.03
    smart_hold_keep_pnl: float = 0.05
    enable_pyramid: bool = False
    pyramid_min_pnl: float = 0.08
    pyramid_notional_frac: float = 0.4
    pyramid_requires_hold_ok: bool = True
    enable_r_scale_out: bool = False
    r_scale_1: float = 1.0
    r_scale_1_frac: float = 1.0 / 3.0
    r_scale_2: float = 2.0
    r_scale_2_frac: float = 0.5
    r_scale_full: float = 3.0
    enable_breakeven_after_r1: bool = False

    # v2.4 bull overlays (hurt on sparse alpha)
    enable_regime_exposure: bool = False
    bull_ma_fast: int = 20
    bull_ma_slow: int = 60
    bull_exposure_mult: float = 1.35
    bull_exposure_cap: float = 1.5
    bull_extra_entries: int = 1
    enable_vol_target: bool = False
    vol_target_ann: float = 0.16
    vol_lookback: int = 20
    vol_scale_floor: float = 0.75
    vol_scale_cap: float = 1.4
    enable_bull_hold: bool = False
    bull_extra_hold_days: int = 12
    bull_skip_mfe: bool = True
    bull_take_profit_pct: float = 0.22
    bull_trail_activate_pct: float = 0.08
    bull_trail_drawdown_pct: float = 0.07
    enable_bull_pyramid: bool = False

    # winner convexity / v3.4 sizing
    enable_winner_convexity: bool = False
    winner_convex_min_pnl: float = 0.08
    winner_convex_frac: float = 0.40
    winner_convex_extra_hold: int = 10
    enable_inv_vol_sizing: bool = False
    inv_vol_floor: float = 0.015
    inv_vol_cap: float = 0.080
    inv_vol_blend: float = 1.0
    inv_vol_cut_only: bool = False
    enable_conviction_sizing: bool = False
    conv_cut_only: bool = False
    conv_low_max: int = 2
    conv_mid_max: int = 4
    conv_low_mult: float = 0.75
    conv_mid_mult: float = 1.00
    conv_high_mult: float = 1.25
    conv_count_full_attack: bool = True
    conv_count_low_crowd: bool = True
    conv_count_nb_strong: bool = True
    conv_count_risk_on: bool = True
    conv_count_mainline: bool = True
    conv_count_hs300: bool = True
    conv_count_strong_amt: bool = True
    conv_strong_amt_vs_med: float = 1.5


@dataclass(frozen=True)
class Execution:
    """Capital ledger + A-share realistic costs (v1.0 default ON)."""

    initial_capital: float = 1_000_000.0
    lot_size: int = 100
    cost_bps: float = 15.0  # legacy flat one-way when realistic OFF
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
class Params(AlphaCore, LiveOverlays, DormantAblation, Execution):
    """Full parameter surface (live + dormant). Prefer editing layer classes above."""

    version: str = "v1.0"


DEFAULT_PARAMS = Params()

# Field-name sets for docs / tooling (not exhaustive of nested meaning).
LIVE_LAYER_TYPES = (AlphaCore, LiveOverlays, Execution)
DORMANT_LAYER_TYPES = (DormantAblation,)


def live_field_names() -> frozenset[str]:
    names: set[str] = set()
    for cls in LIVE_LAYER_TYPES:
        names.update(f.name for f in fields(cls))
    names.add("version")
    return frozenset(names)


def dormant_field_names() -> frozenset[str]:
    return frozenset(f.name for f in fields(DormantAblation))


def safe_replace(p: Params | None = None, **kwargs) -> Params:
    """``replace`` that ignores unknown keys (archive presets / old notebooks)."""
    base = p or DEFAULT_PARAMS
    valid = {f.name for f in fields(base)}
    return replace(base, **{k: v for k, v in kwargs.items() if k in valid})


def params_dict(p: Params | None = None) -> dict:
    return asdict(p or DEFAULT_PARAMS)


def params_summary(p: Params | None = None) -> dict:
    """Compact view: counts + which dormant flags are non-default."""
    p = p or DEFAULT_PARAMS
    dormant_default = DormantAblation()
    flipped = []
    for f in fields(DormantAblation):
        if getattr(p, f.name) != getattr(dormant_default, f.name):
            flipped.append(f.name)
    return {
        "version": p.version,
        "n_fields": len(fields(Params)),
        "n_live_fields": len(live_field_names()),
        "n_dormant_fields": len(dormant_field_names()),
        "dormant_flipped": flipped,
    }
