"""LEGACY presets v2.0–v3.3 (flat 15bp, no open slippage).

Live lineage: `config/versions.py` (v1.0+).
Kept only so archived ablation scripts remain reproducible.
"""

from __future__ import annotations

from collections.abc import Callable

from config.params import DEFAULT_PARAMS, Params, safe_replace

_OVERLAY_OFF = dict(
    enable_regime_exposure=False,
    enable_vol_target=False,
    enable_bull_hold=False,
    enable_bull_pyramid=False,
)

_SOTA_OFF = dict(
    enable_sota_factors=False,
    enable_turn_stab_score=False,
    enable_low_ivol_score=False,
    enable_path_eff_score=False,
    enable_anti_chase_score=False,
    enable_turn_stab_filter=False,
    enable_low_ivol_filter=False,
    enable_anti_chase_filter=False,
)

_V26_OFF = dict(
    enable_ind_crowd_domain=False,
    enable_limit_up_crowding=False,
    enable_earnings_blackout=False,
    enable_index_tier_sizing=False,
    earnings_use_event_blackout=False,
)

_V28_OFF = dict(
    enable_nb_flow_soft=False,
    enable_nb_flow_boost=False,
    enable_margin_stress=False,
)

_V30_OFF = dict(
    enable_risk_on=False,
)

_V31_OFF = dict(
    enable_crowd_traj=False,
    enable_scarcity_premium=False,
    enable_winner_convexity=False,
    risk_on_require_mkt_expand=False,
)

_V32_OFF = dict(
    enable_false_bull_fuse=False,
)

_V33_OFF = dict(
    enable_mainline_diffusion=False,
)

# Archived headlines used flat cost_bps=15 with no open slippage.
_LEGACY_COSTS = dict(
    enable_realistic_costs=False,
    enable_open_slippage=False,
)


def preset_v20() -> Params:
    return safe_replace(
        DEFAULT_PARAMS,
        version="v2.0",
        soft_stop_pct=-0.07,
        soft_stop_require_stress=False,
        enable_stale_cut=True,
        enable_failure_exit=False,
        enable_smart_max_hold=False,
        enable_stop_cooldown=False,
        enable_progress_exit=False,
        enable_atr_stop=False,
        enable_structure_stop=False,
        enable_pyramid=False,
        enable_r_scale_out=False,
        enable_mfe_exit=False,
        enable_breakeven_after_r1=False,
        trail_activate_pct=0.06,
        trail_drawdown_pct=0.05,
        **_OVERLAY_OFF,
        **_SOTA_OFF,
        **_V26_OFF,
        **_V28_OFF,
        **_V30_OFF,
        **_V31_OFF,
        **_V32_OFF,
        **_V33_OFF,
        **_LEGACY_COSTS,
    )


def preset_v21() -> Params:
    return safe_replace(
        DEFAULT_PARAMS,
        version="v2.1",
        enable_smart_max_hold=False,
        enable_stop_cooldown=False,
        enable_r_scale_out=False,
        enable_mfe_exit=False,
        enable_breakeven_after_r1=False,
        trail_activate_pct=0.06,
        trail_drawdown_pct=0.05,
        **_OVERLAY_OFF,
        **_SOTA_OFF,
        **_V26_OFF,
        **_V28_OFF,
        **_V30_OFF,
        **_V31_OFF,
        **_V32_OFF,
        **_V33_OFF,
        **_LEGACY_COSTS,
    )


def preset_v22() -> Params:
    return safe_replace(
        DEFAULT_PARAMS,
        version="v2.2",
        enable_smart_max_hold=True,
        enable_stop_cooldown=True,
        enable_r_scale_out=False,
        enable_mfe_exit=False,
        enable_breakeven_after_r1=False,
        enable_stall_take=True,
        trail_activate_pct=0.06,
        trail_drawdown_pct=0.05,
        **_OVERLAY_OFF,
        **_SOTA_OFF,
        **_V26_OFF,
        **_V28_OFF,
        **_V30_OFF,
        **_V31_OFF,
        **_V32_OFF,
        **_V33_OFF,
        **_LEGACY_COSTS,
    )


def preset_v23() -> Params:
    return safe_replace(
        DEFAULT_PARAMS,
        version="v2.3",
        enable_smart_max_hold=False,
        enable_stop_cooldown=True,
        enable_r_scale_out=False,
        enable_mfe_exit=True,
        enable_breakeven_after_r1=False,
        mfe_min_r=0.2,
        mfe_check_days_default=16,
        mfe_check_days_full_attack=22,
        trail_activate_pct=0.06,
        trail_drawdown_pct=0.05,
        **_OVERLAY_OFF,
        **_SOTA_OFF,
        **_V26_OFF,
        **_V28_OFF,
        **_V30_OFF,
        **_V31_OFF,
        **_V32_OFF,
        **_V33_OFF,
        **_LEGACY_COSTS,
    )


def preset_v24() -> Params:
    return safe_replace(
        DEFAULT_PARAMS,
        version="v2.4",
        trail_activate_pct=0.08,
        trail_drawdown_pct=0.07,
        **_OVERLAY_OFF,
        **_SOTA_OFF,
        **_V26_OFF,
        **_V28_OFF,
        **_V30_OFF,
        **_V31_OFF,
        **_V32_OFF,
        **_V33_OFF,
        **_LEGACY_COSTS,
    )


def preset_v25() -> Params:
    return safe_replace(
        DEFAULT_PARAMS,
        version="v2.5",
        **_V26_OFF,
        **_V28_OFF,
        **_V30_OFF,
        **_V31_OFF,
        **_V32_OFF,
        **_V33_OFF,
        **_LEGACY_COSTS,
    )


def preset_v26() -> Params:
    return safe_replace(
        DEFAULT_PARAMS,
        version="v2.6",
        enable_limit_up_crowding=True,
        enable_earnings_blackout=False,
        enable_index_tier_sizing=False,
        earnings_use_event_blackout=False,
        **_V28_OFF,
        **_V30_OFF,
        **_V31_OFF,
        **_V32_OFF,
        **_V33_OFF,
        **_LEGACY_COSTS,
    )


def preset_v27() -> Params:
    return safe_replace(
        DEFAULT_PARAMS,
        version="v2.7",
        **_V28_OFF,
        **_V30_OFF,
        **_V31_OFF,
        **_V32_OFF,
        **_V33_OFF,
        **_LEGACY_COSTS,
    )


def preset_v28() -> Params:
    return safe_replace(
        DEFAULT_PARAMS,
        version="v2.8",
        **_V30_OFF,
        **_V31_OFF,
        **_V32_OFF,
        **_V33_OFF,
        **_LEGACY_COSTS,
    )


def preset_v30() -> Params:
    return safe_replace(
        DEFAULT_PARAMS,
        version="v3.0",
        **_V31_OFF,
        **_V32_OFF,
        **_V33_OFF,
        **_LEGACY_COSTS,
    )


def preset_v31() -> Params:
    return safe_replace(
        DEFAULT_PARAMS,
        version="v3.1",
        **_V32_OFF,
        **_V33_OFF,
        **_LEGACY_COSTS,
    )


def preset_v32() -> Params:
    return safe_replace(
        DEFAULT_PARAMS,
        version="v3.2",
        **_V33_OFF,
        **_LEGACY_COSTS,
    )


def preset_v33() -> Params:
    return safe_replace(DEFAULT_PARAMS, version="v3.3", **_LEGACY_COSTS)


VERSION_PRESETS: dict[str, Callable[[], Params]] = {
    "v2.0": preset_v20,
    "v2.1": preset_v21,
    "v2.2": preset_v22,
    "v2.3": preset_v23,
    "v2.4": preset_v24,
    "v2.5": preset_v25,
    "v2.6": preset_v26,
    "v2.7": preset_v27,
    "v2.8": preset_v28,
    "v3.0": preset_v30,
    "v3.1": preset_v31,
    "v3.2": preset_v32,
    "v3.3": preset_v33,
}

ARCHIVABLE = (
    "v2.0",
    "v2.1",
    "v2.2",
    "v2.3",
    "v2.4",
    "v2.5",
    "v2.6",
    "v2.7",
    "v2.8",
    "v3.0",
    "v3.1",
    "v3.2",
    "v3.3",
)
CURRENT_VERSION = "v3.3"
