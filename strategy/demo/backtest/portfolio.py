"""Capital-aware portfolio simulation (v3.3 + optional smart sizing research)."""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from config.params import DEFAULT_PARAMS, Params
from features.sota_factors import (
    apply_sota_open_filters,
    apply_sota_score_boost,
    enrich_sota_factors,
)


def _build_breadth_by_date(sig: pd.DataFrame) -> pd.Series:
    """Share of names with ind_regime==expand each day (0 if column missing)."""
    if sig.empty or "ind_regime" not in sig.columns:
        return pd.Series(dtype=float)
    g = sig.copy()
    g["date"] = pd.to_datetime(g["date"])
    expand = g["ind_regime"].astype(str).eq("expand")
    return expand.groupby(g["date"]).mean().rename("breadth")


def _build_internals_by_date(
    sig: pd.DataFrame, lookback: int = 5, min_streak: int = 1
) -> pd.DataFrame:
    """Daily thrust / advance shares and N-day deltas (false-bull inputs)."""
    empty = pd.DataFrame(
        columns=["thrust_share", "adv_share", "thrust_delta", "adv_delta", "false_bull"]
    )
    if sig.empty:
        return empty
    g = sig.copy()
    g["date"] = pd.to_datetime(g["date"])
    thrust = g["pattern_primary"].astype(str).eq("thrust_up")
    adv = (
        g["pct_change"].astype(float).gt(0)
        if "pct_change" in g.columns
        else pd.Series(False, index=g.index)
    )
    out = pd.DataFrame(
        {
            "thrust_share": thrust.groupby(g["date"]).mean(),
            "adv_share": adv.groupby(g["date"]).mean(),
        }
    ).sort_index()
    lb = max(1, int(lookback))
    out["thrust_delta"] = out["thrust_share"] - out["thrust_share"].shift(lb)
    out["adv_delta"] = out["adv_share"] - out["adv_share"].shift(lb)
    both = ((out["thrust_delta"] < 0) & (out["adv_delta"] < 0)).fillna(False)
    streak_n = max(1, int(min_streak))
    if streak_n <= 1:
        out["false_bull"] = both
    else:
        # require streak_n consecutive both-shrink days
        run = both.astype(int).groupby((~both).cumsum()).cumsum()
        out["false_bull"] = run >= streak_n
    return out


def _build_market_overlay(
    market: pd.DataFrame | None,
    params: Params,
    breadth: pd.Series | None = None,
    internals: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """HS300 trend / vol / Risk-On by date (+ optional false-bull flags)."""
    need = (
        params.enable_regime_exposure
        or params.enable_vol_target
        or params.enable_bull_hold
        or params.enable_risk_on
        or params.enable_false_bull_fuse
    )
    if market is None or not need or len(market) == 0:
        return pd.DataFrame(
            columns=["bull", "vol_scale", "risk_on", "false_bull", "risk_on_exposure_ok"]
        ).rename_axis("date")
    m = market.copy()
    m["date"] = pd.to_datetime(m["date"])
    m = m.sort_values("date").drop_duplicates("date")
    close = m["close"].astype(float)
    ma_fast = close.rolling(params.bull_ma_fast, min_periods=params.bull_ma_fast).mean()
    ma_slow = close.rolling(params.bull_ma_slow, min_periods=params.bull_ma_slow).mean()
    bull = (close > ma_slow) & (ma_fast > ma_slow)
    abs_mom = close / close.shift(params.risk_on_abs_mom_days) - 1.0 > 0.0
    vol_scale = pd.Series(1.0, index=m.index)
    if params.enable_vol_target:
        ret = close.pct_change()
        realized = ret.rolling(params.vol_lookback, min_periods=max(5, params.vol_lookback // 2)).std() * np.sqrt(
            252.0
        )
        raw = params.vol_target_ann / realized.replace(0, np.nan)
        vol_scale = raw.clip(params.vol_scale_floor, params.vol_scale_cap).fillna(1.0)

    out = pd.DataFrame(
        {
            "date": m["date"].values,
            "bull": bull.fillna(False).astype(bool).values,
            "abs_mom": abs_mom.fillna(False).astype(bool).values,
            "vol_scale": vol_scale.values,
        }
    ).set_index("date")
    if breadth is not None and len(breadth):
        b = breadth.copy()
        b.index = pd.to_datetime(b.index)
        out = out.join(b.rename("breadth"), how="left")
    else:
        out["breadth"] = np.nan

    risk_on = out["bull"].astype(bool)
    if params.risk_on_require_abs_mom:
        risk_on = risk_on & out["abs_mom"].astype(bool)
    if params.risk_on_require_breadth:
        thr = float(params.risk_on_breadth_min)
        br = out["breadth"]
        risk_on = risk_on & br.notna() & (br >= thr)
    out["risk_on"] = risk_on.fillna(False).astype(bool)

    if internals is not None and len(internals):
        inn = internals.copy()
        inn.index = pd.to_datetime(inn.index)
        cols = [c for c in ("thrust_delta", "adv_delta", "false_bull") if c in inn.columns]
        out = out.join(inn[cols], how="left")
    if "false_bull" not in out.columns:
        out["false_bull"] = False
    else:
        out["false_bull"] = out["false_bull"].fillna(False).astype(bool)

    if params.enable_false_bull_fuse and params.false_bull_block_both_shrink and params.false_bull_gate_risk_on:
        out["risk_on"] = out["risk_on"] & ~out["false_bull"]

    exposure_ok = out["risk_on"].astype(bool)
    if params.enable_false_bull_fuse and params.false_bull_block_both_shrink and params.false_bull_gate_exposure:
        exposure_ok = exposure_ok & ~out["false_bull"]
    out["risk_on_exposure_ok"] = exposure_ok.fillna(False).astype(bool)
    return out


@dataclass
class Position:
    shares: int
    cost_total: float
    entry_date: pd.Timestamp
    entry_price: float
    reason: str
    peak_close: float = 0.0
    bucket: str = "core"
    soft_stopped: bool = False
    stall_taken: bool = False
    pyramided: bool = False
    progress_checked: bool = False
    mfe_checked: bool = False
    entry_low: float = 0.0
    hold_ok_fail_streak: int = 0
    atr_at_entry: float = np.nan
    initial_risk_pct: float = 0.12
    r1_taken: bool = False
    r2_taken: bool = False
    breakeven_stop: bool = False


@dataclass
class PortfolioState:
    cash: float
    positions: dict[str, Position] = field(default_factory=dict)
    trade_id: int = 0
    cooldown_until: dict[str, pd.Timestamp] = field(default_factory=dict)


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


def _add_atr(bars: pd.DataFrame, period: int) -> pd.DataFrame:
    b = bars.sort_values(["code", "date"]).copy()
    if "high" not in b.columns or "low" not in b.columns:
        b["atr"] = np.nan
        return b
    prev = b.groupby("code")["close"].shift(1)
    tr = pd.concat(
        [
            (b["high"] - b["low"]).abs(),
            (b["high"] - prev).abs(),
            (b["low"] - prev).abs(),
        ],
        axis=1,
    ).max(axis=1)
    b["atr"] = tr.groupby(b["code"]).transform(
        lambda s: s.rolling(period, min_periods=max(5, period // 2)).mean()
    )
    return b


def _enrich_crowd_traj(sig: pd.DataFrame, params: Params) -> pd.DataFrame:
    """Add crowding delta / rising flags for uncrowded-momentum entries."""
    if sig.empty or "crowding_score" not in sig.columns:
        out = sig.copy()
        out["crowd_delta_5"] = np.nan
        out["crowd_rising"] = False
        out["crowd_traj_entry"] = False
        return out
    out = sig.sort_values(["code", "date"]).copy()
    score = pd.to_numeric(out["crowding_score"], errors="coerce")
    out["crowd_delta_5"] = score.groupby(out["code"]).diff(params.crowd_traj_lookback)
    out["crowd_rising"] = out["crowd_delta_5"].fillna(0.0) > float(params.crowd_traj_min_delta)
    return out

def _enrich_mainline(sig: pd.DataFrame, params: Params) -> pd.DataFrame:
    """Flag rare industry-days: top-N share, peaked high, mild pullback, still elevated."""
    out = sig.copy()
    if out.empty or "ind_crowd_pct" not in out.columns or "industry_code" not in out.columns:
        out["mainline_rebalance"] = False
        return out
    out["date"] = pd.to_datetime(out["date"])
    ind = (
        out[["date", "industry_code", "ind_crowd_pct"]]
        .drop_duplicates(["date", "industry_code"])
        .sort_values(["industry_code", "date"])
        .copy()
    )
    ic = pd.to_numeric(ind["ind_crowd_pct"], errors="coerce")
    ind["ic"] = ic
    g = ind.groupby("industry_code")["ic"]
    peak_lb = max(5, int(params.mainline_peak_lookback))
    pb_lb = max(1, int(params.mainline_pullback_lookback))
    ind["ic_max"] = g.transform(lambda s: s.rolling(peak_lb, min_periods=max(5, peak_lb // 2)).max())
    ind["ic_delta"] = g.diff(pb_lb)
    top_n = max(1, int(params.mainline_top_n))
    ind["cs_rank"] = ind.groupby("date")["ic"].rank(ascending=False, method="first")
    ind["mainline_rebalance"] = (
        (ind["cs_rank"] <= top_n)
        & (ind["ic_max"] >= float(params.mainline_peak_pct))
        & (ind["ic"] >= float(params.mainline_still_pct))
        & (ind["ic_delta"] <= float(params.mainline_pullback_max))
        & (ind["ic_delta"] >= float(params.mainline_pullback_min))
    ).fillna(False)
    out = out.drop(columns=["mainline_rebalance"], errors="ignore")
    out = out.merge(
        ind[["date", "industry_code", "mainline_rebalance"]],
        on=["date", "industry_code"],
        how="left",
    )
    out["mainline_rebalance"] = out["mainline_rebalance"].fillna(False).astype(bool)
    return out

def _build_scarcity_mult(opens: pd.DataFrame, params: Params) -> pd.Series:
    """Map date -> size multiplier from open-A scarcity / abundance."""
    if opens.empty or not params.enable_scarcity_premium:
        return pd.Series(dtype=float)
    counts = opens.groupby("date").size().astype(float).sort_index()
    pct = counts.copy() * np.nan
    vals = counts.values
    idx = counts.index
    lb = params.scarcity_lookback
    for i in range(len(vals)):
        lo = max(0, i - lb + 1)
        window = vals[lo : i + 1]
        pct.iloc[i] = float((window <= vals[i]).mean())
    scarce = pct <= float(params.scarcity_low_pctile)
    abundant = pct >= float(params.scarcity_high_pctile)
    mult = pd.Series(1.0, index=idx)
    mult = mult.where(~scarce, float(params.scarcity_boost_mult))
    mult = mult.where(~abundant, float(params.scarcity_cut_mult))
    return mult

def _filter_opens(df: pd.DataFrame, params: Params, modes: tuple, actions: list[str]) -> pd.DataFrame:
    out = df[df["action"].isin(actions)].copy()
    if out.empty:
        return out
    if "position_mult" in out.columns:
        out = out[out["position_mult"] >= params.min_position_mult_to_buy]
    if "attack_mode" in out.columns:
        mode = out["attack_mode"].astype(str)
        modes_ok = mode.isin(list(modes))
        mainline = (
            out["mainline_rebalance"].fillna(False).astype(bool)
            if "mainline_rebalance" in out.columns
            else pd.Series(False, index=out.index)
        )
        if params.enable_mainline_diffusion and params.mainline_allow_structure:
            modes_ok = modes_ok | (mode.eq("structure_attack") & mainline)
        out = out[modes_ok].copy()
        if out.empty:
            out["crowd_traj_entry"] = False
            out["mainline_entry"] = False
            return out
        mainline = (
            out["mainline_rebalance"].fillna(False).astype(bool)
            if "mainline_rebalance" in out.columns
            else pd.Series(False, index=out.index)
        )
        out["mainline_entry"] = mode.loc[out.index].eq("structure_attack") & mainline & ~mode.loc[
            out.index
        ].isin(list(modes))
    else:
        out["mainline_entry"] = False

    if "crowding_bucket" in out.columns:
        bucket = out["crowding_bucket"].astype(str)
        low_ok = bucket.eq("low")
        traj_ok = pd.Series(False, index=out.index)
        if params.enable_crowd_traj:
            score = pd.to_numeric(out.get("crowding_score"), errors="coerce")
            rising = out["crowd_rising"].fillna(False) if "crowd_rising" in out.columns else False
            mid_ok = bucket.eq("mid")
            score_ok = score.notna() & (score <= float(params.crowd_traj_max_score))
            traj_ok = mid_ok & rising & score_ok
        out = out[low_ok | traj_ok].copy()
        out["crowd_traj_entry"] = (~low_ok.loc[out.index]) & traj_ok.loc[out.index]
    else:
        out["crowd_traj_entry"] = False

    if "valuation_bucket" in out.columns:
        out = out[out["valuation_bucket"].isin(list(params.allowed_valuation))]
    if "amt_vs_med" in out.columns:
        out = out[out["amt_vs_med"] >= params.min_amt_vs_med]
    if params.enable_earnings_blackout and params.earnings_blackout_block_entry:
        if "earnings_blackout" in out.columns:
            out = out[~out["earnings_blackout"].fillna(False).astype(bool)]
    return out

def _ind_crowd_high(srow: pd.Series | None, params: Params) -> bool:
    if not params.enable_ind_crowd_domain or srow is None:
        return False
    if "ind_crowd_bucket" in srow.index and str(srow.get("ind_crowd_bucket", "")) == "high":
        return True
    try:
        pct = float(srow.get("ind_crowd_pct", np.nan))
    except (TypeError, ValueError):
        return False
    return np.isfinite(pct) and pct >= params.ind_crowd_high_pct


def _apply_ind_crowd_score(df: pd.DataFrame, params: Params) -> pd.DataFrame:
    if df.empty or not params.enable_ind_crowd_domain or "score" not in df.columns:
        return df
    out = df.copy()
    high = False
    if "ind_crowd_bucket" in out.columns:
        high = out["ind_crowd_bucket"].astype(str) == "high"
    elif "ind_crowd_pct" in out.columns:
        high = out["ind_crowd_pct"].astype(float) >= params.ind_crowd_high_pct
    if isinstance(high, pd.Series) and high.any():
        out.loc[high, "score"] = out.loc[high, "score"].astype(float) * params.ind_crowd_score_penalty
    return out


def _flag(srow: pd.Series | None, col: str) -> bool:
    if srow is None or col not in srow.index:
        return False
    val = srow.get(col, False)
    if val is None or (isinstance(val, float) and not np.isfinite(val)):
        return False
    return bool(val)

def _is_exit_stress(srow: pd.Series | None, params: Params) -> bool:
    if srow is None:
        return False
    crowd = str(srow.get("crowding_bucket", "") or "")
    pat = str(srow.get("pattern_primary", "") or "")
    shrinking = bool(srow.get("industry_shrinking", False)) or str(srow.get("ind_regime", "")) == "shrink"
    try:
        amt = float(srow.get("amt_vs_med", 0) or 0)
    except (TypeError, ValueError):
        amt = 0.0
    if crowd in ("high", "extreme"):
        return True
    if pat in ("break_down_vol", "climax_volume", "stall_high"):
        return True
    if shrinking:
        return True
    if amt >= params.climax_amt_vs_med:
        return True
    return False

def _flow_size_mult(srow: pd.Series | dict | None, params: Params) -> float:
    """Northbound / margin soft size overlays."""
    if srow is None:
        return 1.0
    mult = 1.0
    get = srow.get if hasattr(srow, "get") else lambda k, d=None: srow[k] if k in srow.index else d
    if params.enable_nb_flow_boost and bool(get("nb_strong", False)):
        mult *= params.nb_strong_size_mult
    if params.enable_margin_stress and bool(get("mg_stress", False)):
        mult *= params.mg_stress_size_mult
    return mult

def _asset_vol_frac(row: pd.Series, code: str, dt, px: pd.DataFrame) -> float:
    """Prefer ATR/price on signal day; fallback ret_vol_20."""
    try:
        brow = px.loc[(code, pd.Timestamp(dt))]
        atr = float(brow["atr"]) if "atr" in brow.index else np.nan
        close = float(brow["close"]) if "close" in brow.index else np.nan
        if np.isfinite(atr) and np.isfinite(close) and close > 0 and atr > 0:
            return float(atr / close)
    except (KeyError, TypeError, ValueError):
        pass
    try:
        v = float(row.get("ret_vol_20", np.nan))
        if np.isfinite(v) and v > 0:
            return v
    except (TypeError, ValueError):
        pass
    return float("nan")


def _conviction_count(
    row: pd.Series,
    params: Params,
    *,
    risk_on: bool,
) -> int:
    """Count independent confirming dimensions (not a raw score)."""
    n = 0
    if params.conv_count_full_attack and str(row.get("attack_mode", "")) == "full_attack":
        n += 1
    if params.conv_count_low_crowd and str(row.get("crowding_bucket", "")) == "low":
        n += 1
    if params.conv_count_nb_strong and bool(row.get("nb_strong", False)):
        n += 1
    if params.conv_count_risk_on and risk_on:
        n += 1
    if params.conv_count_mainline and bool(row.get("mainline_rebalance", False)):
        n += 1
    if params.conv_count_hs300:
        tier = str(row.get("index_tier", "") or "")
        if tier == "hs300" or bool(row.get("is_hs300", False)):
            n += 1
    if params.conv_count_strong_amt:
        try:
            amt = float(row.get("amt_vs_med", np.nan))
        except (TypeError, ValueError):
            amt = np.nan
        if np.isfinite(amt) and amt >= float(params.conv_strong_amt_vs_med):
            n += 1
    return int(n)


def _conviction_mult(count: int, params: Params) -> float:
    if count <= int(params.conv_low_max):
        return float(params.conv_low_mult)
    if count <= int(params.conv_mid_max):
        return float(params.conv_mid_mult)
    return float(params.conv_high_mult)


def _is_thesis_failure(srow: pd.Series | None, pos: Position, close: float, params: Params) -> bool:
    if srow is None:
        return False
    pat = str(srow.get("pattern_primary", "") or "")
    crowd = str(srow.get("crowding_bucket", "") or "")
    if pat == "break_down_vol":
        return True
    if pat in ("climax_volume", "stall_high") and crowd in ("high", "extreme"):
        return True
    if pos.hold_ok_fail_streak >= params.failure_hold_ok_streak:
        return True
    return False


def run_portfolio(
    signals: pd.DataFrame,
    bars: pd.DataFrame,
    names: dict[str, str] | None = None,
    params: Params = DEFAULT_PARAMS,
    market: pd.DataFrame | None = None,
) -> dict:
    names = names or {}
    sig = signals.copy()
    sig["date"] = pd.to_datetime(sig["date"])
    if params.enable_sota_factors and "turn_cv_20" not in sig.columns:
        sig = enrich_sota_factors(sig, params.sota_factor_lookback)
    if params.enable_crowd_traj or params.enable_scarcity_premium:
        sig = _enrich_crowd_traj(sig, params)
    elif "crowd_rising" not in sig.columns:
        sig = _enrich_crowd_traj(sig, params)
    if params.enable_mainline_diffusion:
        sig = _enrich_mainline(sig, params)
    elif "mainline_rebalance" not in sig.columns:
        sig = sig.copy()
        sig["mainline_rebalance"] = False
    bars = bars.copy()
    bars["date"] = pd.to_datetime(bars["date"])
    bars = _add_atr(bars, params.atr_period)
    px = bars.set_index(["code", "date"])
    sig_idx = sig.set_index(["date", "code"], drop=False)
    breadth = _build_breadth_by_date(sig) if params.enable_risk_on else None
    overlay = _build_market_overlay(market, params, breadth=breadth)

    dates = sorted(sig["date"].unique())
    if len(dates) < 2:
        return {"error": "not enough dates"}

    def day_overlay(dt) -> tuple[bool, float, bool]:
        if overlay.empty:
            return False, 1.0, False
        ts = pd.Timestamp(dt)
        if ts not in overlay.index:
            prior = overlay.index[overlay.index <= ts]
            if len(prior) == 0:
                return False, 1.0, False
            ts = prior[-1]
        row = overlay.loc[ts]
        return (
            bool(row["bull"]),
            float(row["vol_scale"]),
            bool(row["risk_on"]) if "risk_on" in row.index else False,
        )
    core_actions = ["open_A"]
    if params.enable_open_b:
        core_actions.append("open_B")
    core = _filter_opens(sig, params, params.allowed_attack_modes, core_actions)
    if params.enable_sota_factors and len(core):
        core = apply_sota_open_filters(core, params)
        core = apply_sota_score_boost(core, params)
    if len(core):
        core = _apply_ind_crowd_score(core, params)
    if not core.empty:
        core["rank"] = core.groupby("date")["score"].rank(ascending=False, method="first")
        picks_core = core[core["rank"] <= params.top_n]
    else:
        picks_core = core

    scarcity_mult = _build_scarcity_mult(picks_core if len(picks_core) else core, params)

    if params.enable_open_c:
        sat = _filter_opens(sig, params, params.open_c_attack_modes, ["open_C"])
        if params.enable_sota_factors and len(sat):
            sat = apply_sota_open_filters(sat, params)
            sat = apply_sota_score_boost(sat, params)
        if len(sat):
            sat = _apply_ind_crowd_score(sat, params)
        if not sat.empty:
            sat["rank"] = sat.groupby("date")["score"].rank(ascending=False, method="first")
            picks_sat = sat[sat["rank"] <= params.satellite_n]
        else:
            picks_sat = sat
    else:
        sat = pd.DataFrame()
        picks_sat = sat

    core_codes_by_date: dict[pd.Timestamp, set[str]] = {}
    if len(core):
        for d, g in core.groupby("date"):
            core_codes_by_date[pd.Timestamp(d)] = set(g["code"].astype(str))
    sat_codes_by_date: dict[pd.Timestamp, set[str]] = {}
    if len(sat):
        for d, g in sat.groupby("date"):
            sat_codes_by_date[pd.Timestamp(d)] = set(g["code"].astype(str))

    exit_sig = sig[sig["action"].isin(["reduce", "avoid"])][["date", "code", "action"]]

    state = PortfolioState(cash=float(params.initial_capital))
    trades: list[dict] = []
    daily: list[dict] = []
    pending_sells: list[dict] = []
    pending_buys: list[dict] = []
    pending_adds: list[dict] = []
    prev_equity = float(params.initial_capital)

    def lookup_sig(dt, code: str) -> pd.Series | None:
        try:
            row = sig_idx.loc[(dt, code)]
            if isinstance(row, pd.DataFrame):
                return row.iloc[0]
            return row
        except KeyError:
            return None

    def confirmed(code: str, dt, bucket: str) -> bool:
        if not params.require_entry_confirm or params.entry_confirm_days <= 0:
            return True
        by_date = core_codes_by_date if bucket == "core" else sat_codes_by_date
        try:
            idx = dates.index(pd.Timestamp(dt))
        except ValueError:
            return False
        need = params.entry_confirm_days
        if idx < need:
            return False
        for j in range(1, need + 1):
            prev = dates[idx - j]
            if str(code) not in by_date.get(pd.Timestamp(prev), set()):
                return False
        return True

    def in_cooldown(code: str, dt) -> bool:
        if not params.enable_stop_cooldown:
            return False
        until = state.cooldown_until.get(str(code))
        return until is not None and pd.Timestamp(dt) <= until

    def set_cooldown(code: str, dt, reason: str) -> None:
        if not params.enable_stop_cooldown:
            return
        if reason not in ("stop_loss", "failure_exit", "structure_stop", "atr_stop"):
            return
        # cooldown measured in calendar days from signal date; approx trading days via date offset
        state.cooldown_until[str(code)] = pd.Timestamp(dt) + pd.Timedelta(days=int(params.stop_cooldown_days * 1.5))

    def execute_sell(
        exec_date,
        code: str,
        frac: float,
        reason: str,
        slip_bps: float | None = None,
    ) -> None:
        if code not in state.positions:
            return
        try:
            row = px.loc[(code, exec_date)]
        except KeyError:
            return
        raw = float(row["open"])
        if not np.isfinite(raw) or raw <= 0:
            return
        if bool(row.get("is_limit_down", False)):
            return
        if "limit_down" in row.index:
            try:
                ld = float(row["limit_down"])
                if np.isfinite(ld) and ld > 0 and abs(raw - ld) / ld < 1e-6:
                    return
            except (TypeError, ValueError):
                pass
        sbps = 0.0 if slip_bps is None else float(slip_bps)
        price = _apply_slip(raw, "SELL", sbps)
        pos = state.positions[code]
        shares = pos.shares if frac >= 0.999 else _lot_floor(int(pos.shares * frac), params.lot_size)
        if shares < params.lot_size and frac < 0.999:
            return
        if shares <= 0:
            return
        shares = min(shares, pos.shares)
        amount = shares * price
        fee = _trade_fee(amount, "SELL", params)
        proceeds = amount - fee
        cost_cut = pos.cost_total * (shares / pos.shares)
        realized = proceeds - cost_cut
        hold_days = int((exec_date - pos.entry_date).days)
        state.cash += proceeds
        state.trade_id += 1
        left = pos.shares - shares
        trades.append(
            {
                "trade_id": state.trade_id,
                "date": exec_date,
                "side": "SELL",
                "code": code,
                "name": names.get(code, ""),
                "shares": shares,
                "price": price,
                "amount": amount,
                "fee": fee,
                "realized_pnl": realized,
                "reason": reason,
                "cash_after": state.cash,
                "position_shares": left,
                "hold_days": hold_days,
                "avg_cost": cost_cut / shares if shares else 0.0,
                "bucket": pos.bucket,
                "slip_bps": sbps,
            }
        )
        if left <= 0:
            set_cooldown(code, exec_date, reason)
            del state.positions[code]
        else:
            pos.shares = left
            pos.cost_total -= cost_cut

    def execute_buy_new(exec_date, order: dict) -> None:
        code = order["code"]
        if code in state.positions:
            return
        if in_cooldown(code, exec_date):
            return
        n_core = sum(1 for p in state.positions.values() if p.bucket == "core")
        n_sat = sum(1 for p in state.positions.values() if p.bucket == "satellite")
        bucket = order.get("bucket", "core")
        if bucket == "core" and n_core >= params.top_n:
            return
        if bucket == "satellite" and n_sat >= params.satellite_n:
            return
        try:
            row = px.loc[(code, exec_date)]
        except KeyError:
            return
        raw = float(row["open"])
        if not np.isfinite(raw) or raw <= 0 or bool(row.get("is_limit_up", False)):
            return
        if "limit_up" in row.index:
            try:
                lu = float(row["limit_up"])
                if np.isfinite(lu) and lu > 0 and abs(raw - lu) / lu < 1e-6:
                    return
            except (TypeError, ValueError):
                pass
        sbps = float(order.get("slip_bps", 0.0) or 0.0)
        price = _apply_slip(raw, "BUY", sbps)
        weight_cap = params.max_single_weight
        if params.enable_index_tier_sizing:
            tier = str(order.get("index_tier", "other") or "other")
            if tier == "hs300":
                weight_cap = params.hs300_max_weight
            elif tier == "zz500":
                weight_cap = params.zz500_max_weight
            else:
                weight_cap = params.other_max_weight
        target_notional = min(float(order["target_notional"]), prev_equity * weight_cap)
        budget = min(target_notional, state.cash * 0.995)
        if budget < price * params.lot_size:
            return
        shares = int(budget // (price * params.lot_size)) * params.lot_size
        if shares < params.lot_size:
            return
        amount = shares * price
        fee = _trade_fee(amount, "BUY", params)
        total_out = amount + fee
        if total_out > state.cash:
            # re-solve shares under fee+slip (approx using buy commission rate)
            fee_rate = (
                float(params.commission_bps + params.transfer_fee_bps) / 10000.0
                if params.enable_realistic_costs
                else float(params.cost_bps) / 10000.0
            )
            shares = int(state.cash / (price * (1 + fee_rate) * params.lot_size)) * params.lot_size
            if shares < params.lot_size:
                return
            amount = shares * price
            fee = _trade_fee(amount, "BUY", params)
            total_out = amount + fee
            if total_out > state.cash:
                return
        atr = float(row.get("atr", np.nan)) if "atr" in row.index else np.nan
        low0 = float(row["low"]) if "low" in row.index and np.isfinite(row["low"]) else price
        state.cash -= total_out
        state.positions[code] = Position(
            shares=shares,
            cost_total=total_out,
            entry_date=exec_date,
            entry_price=price,
            reason=order["reason"],
            peak_close=price,
            bucket=bucket,
            entry_low=low0,
            atr_at_entry=atr,
            initial_risk_pct=abs(float(params.stop_loss_pct)),
        )
        state.trade_id += 1
        trades.append(
            {
                "trade_id": state.trade_id,
                "date": exec_date,
                "side": "BUY",
                "code": code,
                "name": names.get(code, ""),
                "shares": shares,
                "price": price,
                "amount": amount,
                "fee": fee,
                "realized_pnl": 0.0,
                "reason": order["reason"],
                "cash_after": state.cash,
                "position_shares": shares,
                "hold_days": 0,
                "avg_cost": total_out / shares,
                "bucket": bucket,
                "slip_bps": sbps,
            }
        )

    def execute_add(exec_date, order: dict) -> None:
        code = order["code"]
        if code not in state.positions:
            return
        pos = state.positions[code]
        if pos.pyramided:
            return
        try:
            row = px.loc[(code, exec_date)]
        except KeyError:
            return
        raw = float(row["open"])
        if not np.isfinite(raw) or raw <= 0 or bool(row.get("is_limit_up", False)):
            return
        sbps = float(order.get("slip_bps", 0.0) or 0.0)
        price = _apply_slip(raw, "BUY", sbps)
        target_notional = min(float(order["target_notional"]), prev_equity * params.max_single_weight * 0.5)
        budget = min(target_notional, state.cash * 0.995)
        if budget < price * params.lot_size:
            return
        shares = int(budget // (price * params.lot_size)) * params.lot_size
        if shares < params.lot_size:
            return
        amount = shares * price
        fee = _trade_fee(amount, "BUY", params)
        total_out = amount + fee
        if total_out > state.cash:
            return
        state.cash -= total_out
        pos.shares += shares
        pos.cost_total += total_out
        pos.pyramided = True
        # keep original entry_price for thesis stops; peak already tracked
        state.trade_id += 1
        trades.append(
            {
                "trade_id": state.trade_id,
                "date": exec_date,
                "side": "BUY",
                "code": code,
                "name": names.get(code, ""),
                "shares": shares,
                "price": price,
                "amount": amount,
                "fee": fee,
                "realized_pnl": 0.0,
                "reason": "pyramid",
                "cash_after": state.cash,
                "position_shares": pos.shares,
                "hold_days": int((exec_date - pos.entry_date).days),
                "avg_cost": pos.cost_total / pos.shares,
                "bucket": pos.bucket,
                "slip_bps": sbps,
            }
        )

    for i, dt in enumerate(dates):
        if i > 0:
            for order in pending_sells:
                execute_sell(
                    dt,
                    order["code"],
                    float(order.get("frac", 1.0)),
                    order["reason"],
                    slip_bps=order.get("slip_bps"),
                )
            for order in pending_adds:
                execute_add(dt, order)
            for order in pending_buys:
                execute_buy_new(dt, order)

        pending_sells = []
        pending_buys = []
        pending_adds = []

        mv = 0.0
        for code, pos in state.positions.items():
            try:
                close = float(px.loc[(code, dt)]["close"])
            except KeyError:
                close = pos.entry_price
            if not np.isfinite(close) or close <= 0:
                close = pos.entry_price
            mv += pos.shares * close
        equity = state.cash + mv
        daily_pnl = equity - prev_equity
        daily_ret = daily_pnl / prev_equity if prev_equity > 0 else 0.0
        is_bull, vol_scale, is_risk_on, exposure_ok, is_false_bull = day_overlay(dt)
        risk_on_active = bool(params.enable_risk_on and is_risk_on)
        risk_on_exposure_ok = bool(params.enable_risk_on and exposure_ok)
        daily.append(
            {
                "date": dt,
                "cash": state.cash,
                "market_value": mv,
                "equity": equity,
                "nav": equity / params.initial_capital,
                "n_positions": len(state.positions),
                "daily_pnl": daily_pnl,
                "daily_ret": daily_ret,
                "cash_ratio": state.cash / equity if equity > 0 else 1.0,
                "risk_on": int(risk_on_active),
                "false_bull": int(bool(is_false_bull)),
            }
        )
        prev_equity = equity

        day_exits = exit_sig[exit_sig["date"] == dt]
        planned: dict[str, dict] = {}
        # Exit / trail policy: Risk-On (v3) takes precedence over legacy bull_hold
        if risk_on_active and params.risk_on_exit_policy:
            tp_pct = params.risk_on_take_profit_pct
            trail_act = params.risk_on_trail_activate_pct
            trail_dd = params.risk_on_trail_drawdown_pct
            disable_tp = bool(params.risk_on_disable_tp)
            skip_mfe_today = bool(params.risk_on_skip_mfe)
        else:
            tp_pct = (
                params.bull_take_profit_pct
                if (params.enable_bull_hold and is_bull)
                else params.take_profit_pct
            )
            trail_act = (
                params.bull_trail_activate_pct
                if (params.enable_bull_hold and is_bull)
                else params.trail_activate_pct
            )
            trail_dd = (
                params.bull_trail_drawdown_pct
                if (params.enable_bull_hold and is_bull)
                else params.trail_drawdown_pct
            )
            disable_tp = False
            skip_mfe_today = bool(params.enable_bull_hold and params.bull_skip_mfe and is_bull)

        for code, pos in list(state.positions.items()):
            try:
                brow = px.loc[(code, dt)]
                close = float(brow["close"])
                low = float(brow["low"]) if "low" in brow.index else close
                atr_now = float(brow["atr"]) if "atr" in brow.index else pos.atr_at_entry
            except KeyError:
                close = pos.entry_price
                low = pos.entry_price
                atr_now = pos.atr_at_entry
            if not np.isfinite(close) or close <= 0:
                close = pos.entry_price
            # structure reference = swing low BEFORE today's bar
            prior_swing_low = pos.entry_low if pos.entry_low > 0 else close
            pos.peak_close = max(pos.peak_close or pos.entry_price, close)
            pnl_pct = close / pos.entry_price - 1.0 if pos.entry_price > 0 else 0.0
            hold_days = (dt - pos.entry_date).days
            srow = lookup_sig(dt, code)
            hold_ok = bool(srow.get("hold_ok", False)) if srow is not None else False
            if hold_ok:
                pos.hold_ok_fail_streak = 0
            else:
                pos.hold_ok_fail_streak += 1

            ind_high = _ind_crowd_high(srow, params)
            earn_bo = False
            if srow is not None and params.enable_earnings_blackout:
                if (
                    getattr(params, "earnings_use_event_blackout", False)
                    and "event_blackout" in srow.index
                ):
                    earn_bo = bool(srow.get("event_blackout", False))
                else:
                    earn_bo = bool(srow.get("earnings_blackout", False))
            # domain-adjusted trail / TP for this name
            use_trail_act = trail_act
            use_trail_dd = trail_dd
            use_tp = tp_pct
            if ind_high and params.ind_crowd_tighter_trail:
                use_trail_act = min(use_trail_act, 0.06)
                use_trail_dd = min(use_trail_dd, 0.05)
            if (
                params.enable_earnings_blackout
                and params.earnings_blackout_take_profit
                and earn_bo
                and pnl_pct >= max(0.03, use_tp * 0.5)
            ):
                planned[code] = {"frac": 1.0, "reason": "earnings_blackout"}
                continue

            # --- hard / ATR / structure / breakeven stops ---
            if hold_days >= params.min_hold_days_before_stop:
                stopped = False
                if pos.breakeven_stop and pnl_pct <= 0.002:
                    planned[code] = {"frac": 1.0, "reason": "breakeven_stop"}
                    stopped = True
                elif pnl_pct <= params.stop_loss_pct:
                    planned[code] = {"frac": 1.0, "reason": "stop_loss"}
                    stopped = True
                elif params.enable_atr_stop and np.isfinite(atr_now) and atr_now > 0:
                    atr_stop_px = pos.entry_price - params.atr_mult * float(atr_now)
                    floor_px = pos.entry_price * (1.0 + params.stop_loss_pct)
                    stop_px = max(atr_stop_px, floor_px)
                    if close <= stop_px:
                        planned[code] = {"frac": 1.0, "reason": "atr_stop"}
                        stopped = True
                if (
                    not stopped
                    and params.enable_structure_stop
                    and prior_swing_low > 0
                    and hold_days >= params.min_hold_days_before_stop
                    and close < prior_swing_low * (1.0 - params.structure_stop_buffer)
                    and pnl_pct <= -0.03
                ):
                    planned[code] = {"frac": 1.0, "reason": "structure_stop"}
                    stopped = True
                if stopped:
                    if np.isfinite(low) and low > 0:
                        pos.entry_low = min(prior_swing_low, low) if prior_swing_low > 0 else low
                    continue

            # update swing low after stop checks
            if np.isfinite(low) and low > 0:
                pos.entry_low = min(prior_swing_low, low) if prior_swing_low > 0 else low

            if (
                params.enable_soft_stop
                and not pos.soft_stopped
                and hold_days >= params.min_hold_days_before_stop
                and pnl_pct <= params.soft_stop_pct
                and pnl_pct > params.stop_loss_pct
                and not pos.breakeven_stop
            ):
                if (not params.soft_stop_require_stress) or _is_exit_stress(srow, params):
                    pos.soft_stopped = True
                    planned[code] = {"frac": params.reduce_frac, "reason": "soft_stop"}
                    continue

            # --- R-multiple scale-out (v2.3a) ---
            r_unit = pos.initial_risk_pct if pos.initial_risk_pct > 1e-6 else abs(params.stop_loss_pct)
            pnl_r = pnl_pct / r_unit if r_unit > 0 else 0.0
            peak_gain = pos.peak_close / pos.entry_price - 1.0 if pos.entry_price > 0 else 0.0
            mfe_r = peak_gain / r_unit if r_unit > 0 else 0.0

            if params.enable_r_scale_out:
                if (not pos.r1_taken) and pnl_r >= params.r_scale_1:
                    pos.r1_taken = True
                    if params.enable_breakeven_after_r1:
                        pos.breakeven_stop = True
                    planned[code] = {"frac": params.r_scale_1_frac, "reason": "scale_1r"}
                    continue
                if pos.r1_taken and (not pos.r2_taken) and pnl_r >= params.r_scale_2:
                    pos.r2_taken = True
                    planned[code] = {"frac": params.r_scale_2_frac, "reason": "scale_2r"}
                    continue
                if pnl_pct >= use_tp or pnl_r >= params.r_scale_full:
                    planned[code] = {"frac": 1.0, "reason": "take_profit"}
                    continue
            elif (not disable_tp) and pnl_pct >= use_tp:
                planned[code] = {"frac": 1.0, "reason": "take_profit"}
                continue

            if peak_gain >= use_trail_act and pos.peak_close > 0:
                if close / pos.peak_close - 1.0 <= -abs(use_trail_dd):
                    planned[code] = {"frac": 1.0, "reason": "trail_stop"}
                    continue

            if (
                params.enable_stall_take
                and not pos.stall_taken
                and hold_days >= params.stall_take_min_days
                and pnl_pct >= params.stall_take_min_pnl
                and not hold_ok
                and not params.enable_r_scale_out  # R-scale replaces stall take when on
            ):
                pos.stall_taken = True
                planned[code] = {"frac": params.reduce_frac, "reason": "stall_take"}
                continue

            if params.enable_sector_shrink_exit and srow is not None:
                shrinking = bool(srow.get("industry_shrinking", False)) or str(srow.get("ind_regime", "")) == "shrink"
                if shrinking:
                    planned[code] = {"frac": params.reduce_frac, "reason": "sector_shrink"}
                    continue

            hit = day_exits[day_exits["code"] == code]
            if not hit.empty:
                action = str(hit.iloc[0]["action"])
                if action == "reduce":
                    planned[code] = {"frac": params.reduce_frac, "reason": "reduce"}
                elif action == "avoid":
                    if params.avoid_mode == "full":
                        planned[code] = {"frac": 1.0, "reason": "avoid"}
                    elif params.avoid_mode == "reduce_half":
                        planned[code] = {"frac": params.reduce_frac, "reason": "avoid_half"}
                    elif params.avoid_mode == "soft" and pnl_pct < 0:
                        planned[code] = {"frac": params.reduce_frac, "reason": "avoid_soft"}
                continue

            if (
                params.enable_failure_exit
                and hold_days >= params.failure_exit_min_days
                and pnl_pct <= params.failure_exit_max_pnl
                and _is_thesis_failure(srow, pos, close, params)
            ):
                planned[code] = {"frac": 1.0, "reason": "failure_exit"}
                continue

            if (
                params.enable_stale_cut
                and hold_days >= params.stale_min_days
                and pnl_pct <= params.stale_max_pnl
                and not hold_ok
            ):
                planned[code] = {"frac": 1.0, "reason": "stale_cut"}
                continue

            if (
                params.enable_progress_exit
                and not pos.progress_checked
                and hold_days >= params.progress_check_days
            ):
                pos.progress_checked = True
                if pnl_pct < params.progress_min_pnl:
                    planned[code] = {"frac": 1.0, "reason": "progress_exit"}
                    continue

            # MFE dead-money (skip under Risk-On / legacy bull_hold)
            if params.enable_mfe_exit and not pos.mfe_checked:
                if not skip_mfe_today:
                    attack = str(srow.get("attack_mode", "")) if srow is not None else ""
                    check_days = (
                        params.mfe_check_days_full_attack
                        if attack == "full_attack"
                        else params.mfe_check_days_default
                    )
                    if hold_days >= check_days:
                        pos.mfe_checked = True
                        skip = params.mfe_require_not_hold_ok and hold_ok
                        if (not skip) and mfe_r < params.mfe_min_r:
                            planned[code] = {"frac": 1.0, "reason": "mfe_exit"}
                            continue

            pyramid_ok = params.enable_pyramid or (
                params.enable_bull_pyramid and is_bull
            )
            if (
                pyramid_ok
                and not pos.pyramided
                and code not in planned
                and pnl_pct >= params.pyramid_min_pnl
                and (not params.pyramid_requires_hold_ok or hold_ok)
            ):
                add_notional = (pos.cost_total) * params.pyramid_notional_frac
                pending_adds.append(
                    {
                        "code": code,
                        "target_notional": add_notional,
                        "slip_bps": _slippage_bps_for_row(srow, params),
                    }
                )

            limit = params.max_hold_days
            if ind_high and params.ind_crowd_hold_cut_days > 0:
                limit = max(params.min_hold_days_before_stop + 1, limit - params.ind_crowd_hold_cut_days)
            can_extend = params.enable_hold_extend and hold_ok
            if can_extend and params.extend_requires_profit:
                can_extend = pnl_pct > 0
            if ind_high and params.ind_crowd_block_extend:
                can_extend = False
            if (
                params.enable_earnings_blackout
                and params.earnings_blackout_block_extend
                and earn_bo
            ):
                can_extend = False
            if (
                params.enable_nb_flow_soft
                and params.nb_weak_block_extend
                and _flag(srow, "nb_weak")
            ):
                can_extend = False
            if (
                params.enable_margin_stress
                and params.mg_stress_block_extend
                and _flag(srow, "mg_stress")
            ):
                can_extend = False
            # full_attack + already scaled: allow longer runner
            if can_extend or (params.enable_r_scale_out and pos.r1_taken and hold_ok and pnl_pct > 0):
                limit = params.max_hold_days + params.hold_extend_days
            if risk_on_active and params.risk_on_exit_policy and (can_extend or pnl_pct > 0):
                limit = max(limit, params.max_hold_days + params.risk_on_extra_hold_days)
            elif params.enable_bull_hold and is_bull and (can_extend or pnl_pct > 0):
                limit = max(limit, params.max_hold_days + params.bull_extra_hold_days)

            if hold_days >= limit:
                if params.enable_smart_max_hold:
                    if pnl_pct >= params.smart_hold_keep_pnl and hold_ok:
                        planned[code] = {"frac": params.reduce_frac, "reason": "max_hold_trim"}
                    elif pnl_pct >= params.smart_hold_trim_pnl:
                        planned[code] = {"frac": params.reduce_frac, "reason": "max_hold_trim"}
                    else:
                        planned[code] = {"frac": 1.0, "reason": "max_hold"}
                else:
                    planned[code] = {"frac": 1.0, "reason": "max_hold"}

        for code, od in planned.items():
            pending_sells.append(
                {
                    "code": code,
                    "frac": od["frac"],
                    "reason": od["reason"],
                    "slip_bps": _slippage_bps_for_row(lookup_sig(dt, code), params),
                }
            )

        day_core = picks_core[picks_core["date"] == dt].sort_values("score", ascending=False) if len(picks_core) else picks_core
        day_sat = picks_sat[picks_sat["date"] == dt].sort_values("score", ascending=False) if len(picks_sat) else picks_sat
        pos_mult = 0.0
        day_attack_ok = True
        if len(day_core):
            pos_mult = float(day_core["position_mult"].median())
            if "attack_mode" in day_core.columns:
                day_attack_ok = (day_core["attack_mode"] == "full_attack").any()
        elif len(day_sat):
            pos_mult = float(day_sat["position_mult"].median())
        if np.isnan(pos_mult):
            pos_mult = 0.0
        # v3 Risk-On mild exposure; false-bull can strip the boost only
        if risk_on_exposure_ok and params.risk_on_exposure and pos_mult > 0:
            pos_mult = min(pos_mult * params.risk_on_exposure_mult, params.risk_on_exposure_cap)
        if (
            params.enable_false_bull_fuse
            and is_false_bull
            and params.false_bull_size_cut < 1.0
            and pos_mult > 0
        ):
            pos_mult = pos_mult * float(params.false_bull_size_cut)
        if params.enable_regime_exposure and is_bull and pos_mult > 0:
            pos_mult = min(pos_mult * params.bull_exposure_mult, params.bull_exposure_cap)
        if params.enable_vol_target and pos_mult > 0:
            pos_mult = min(pos_mult * vol_scale, params.bull_exposure_cap)
        if pos_mult < params.min_position_mult_to_buy and len(day_core) == 0 and len(day_sat) == 0:
            continue

        selling = {o["code"] for o in pending_sells if o.get("frac", 1) >= 0.999}
        stay_core = [c for c, p in state.positions.items() if c not in selling and p.bucket == "core"]
        stay_sat = [c for c, p in state.positions.items() if c not in selling and p.bucket == "satellite"]
        max_entries = params.max_entries_per_day
        if risk_on_exposure_ok and params.risk_on_exposure:
            max_entries = params.max_entries_per_day + params.risk_on_extra_entries
        elif params.enable_regime_exposure and is_bull:
            max_entries = params.max_entries_per_day + params.bull_extra_entries
        if params.enable_false_bull_fuse and params.false_bull_block_opens and is_false_bull:
            max_entries = 0
            day_core = day_core.iloc[0:0] if len(day_core) else day_core
            day_sat = day_sat.iloc[0:0] if len(day_sat) else day_sat
        refill_ok = True
        if params.cash_refill_require_full_attack:
            refill_ok = day_attack_ok
        if (
            params.enable_cash_refill
            and refill_ok
            and equity > 0
            and (state.cash / equity) >= params.cash_refill_ratio
            and len(stay_core) < params.top_n
        ):
            max_entries = max_entries + params.cash_refill_extra_entries
        slots_core = min(max(params.top_n - len(stay_core), 0), max_entries)
        slots_sat = min(max(params.satellite_n - len(stay_sat), 0), params.max_satellite_entries_per_day)
        per_core = (equity * max(pos_mult, 0.0)) / max(params.top_n, 1)
        per_sat = per_core * params.satellite_weight_frac

        ind_map = {}
        if "industry_code" in sig.columns:
            ind_map = (
                sig.loc[sig["date"] == dt, ["code", "industry_code"]]
                .drop_duplicates("code")
                .set_index("code")["industry_code"]
                .to_dict()
            )
        ind_held: dict = {}
        for c in stay_core + stay_sat:
            ind = ind_map.get(c)
            if ind is not None:
                ind_held[ind] = ind_held.get(ind, 0) + 1

        def enqueue(day_df, slots, bucket, per_slot):
            if day_df is None or len(day_df) == 0 or slots <= 0:
                return
            day_scarcity = 1.0
            if params.enable_scarcity_premium and len(scarcity_mult):
                ts = pd.Timestamp(dt)
                if ts in scarcity_mult.index:
                    day_scarcity = float(scarcity_mult.loc[ts])
                else:
                    prior = scarcity_mult.index[scarcity_mult.index <= ts]
                    if len(prior):
                        day_scarcity = float(scarcity_mult.loc[prior[-1]])
                if day_scarcity > 1.0 and params.scarcity_boost_require_risk_on and not risk_on_active:
                    day_scarcity = 1.0

            # Phase 1: collect eligible candidates (filters only; no size yet)
            cands: list[dict] = []
            mainline_extra_bought = 0
            for _, row in day_df.iterrows():
                if len(cands) >= slots:
                    break
                code = str(row["code"])
                if code in state.positions and code not in selling:
                    continue
                if in_cooldown(code, dt):
                    continue
                if not confirmed(code, dt, bucket):
                    continue
                if bool(row.get("crowd_traj_entry", False)) and not risk_on_active:
                    continue
                is_mainline_extra = bool(row.get("mainline_entry", False)) or bool(
                    row.get("mainline_mid_entry", False)
                )
                if (
                    is_mainline_extra
                    and params.mainline_require_risk_on
                    and not risk_on_active
                ):
                    continue
                if is_mainline_extra and mainline_extra_bought >= int(
                    params.mainline_max_extra_per_day
                ):
                    continue
                ind = row["industry_code"] if "industry_code" in row.index else None
                if ind is not None and ind_held.get(ind, 0) >= 1:
                    continue
                tier = "other"
                if "index_tier" in row.index and row["index_tier"] is not None:
                    tier = str(row["index_tier"])
                elif "is_hs300" in row.index and bool(row.get("is_hs300", False)):
                    tier = "hs300"
                elif "is_zz500" in row.index and bool(row.get("is_zz500", False)):
                    tier = "zz500"

                base = 1.0
                if params.enable_index_tier_sizing:
                    if tier == "hs300":
                        base = params.hs300_size_mult
                    elif tier == "zz500":
                        base = params.zz500_size_mult
                    else:
                        base = params.other_size_mult
                base *= _flow_size_mult(row, params)
                base *= day_scarcity

                inv_raw = 1.0
                if params.enable_inv_vol_sizing:
                    vol = _asset_vol_frac(row, code, dt, px)
                    if np.isfinite(vol) and vol > 0:
                        vol = float(np.clip(vol, params.inv_vol_floor, params.inv_vol_cap))
                        inv_raw = 1.0 / vol
                    else:
                        inv_raw = 1.0 / float(params.inv_vol_floor)

                conv_m = 1.0
                if params.enable_conviction_sizing:
                    conv_m = _conviction_mult(
                        _conviction_count(row, params, risk_on=risk_on_active),
                        params,
                    )

                cands.append(
                    {
                        "code": code,
                        "row": row,
                        "ind": ind,
                        "tier": tier,
                        "base": float(base),
                        "inv_raw": float(inv_raw),
                        "conv": float(conv_m),
                        "is_mainline_extra": is_mainline_extra,
                        "action": str(row["action"]),
                    }
                )
                if ind is not None:
                    ind_held[ind] = ind_held.get(ind, 0) + 1
                if is_mainline_extra:
                    mainline_extra_bought += 1

            if not cands:
                return

            # Phase 2: normalize inv-vol so mean weight stays 1 (preserve day budget)
            if params.enable_inv_vol_sizing:
                invs = np.array([c["inv_raw"] for c in cands], dtype=float)
                mean_inv = float(np.mean(invs)) if len(invs) else 1.0
                if mean_inv <= 0:
                    mean_inv = 1.0
                for c in cands:
                    c["inv"] = c["inv_raw"] / mean_inv
            else:
                for c in cands:
                    c["inv"] = 1.0

            for c in cands:
                size_mult = c["base"] * c["inv"] * c["conv"]
                pending_buys.append(
                    {
                        "code": c["code"],
                        "reason": c["action"],
                        "target_notional": per_slot * size_mult,
                        "bucket": bucket,
                        "index_tier": c["tier"],
                        "slip_bps": _slippage_bps_for_row(c["row"], params),
                    }
                )

        if pos_mult >= params.min_position_mult_to_buy:
            enqueue(day_core, slots_core, "core", per_core)
            enqueue(day_sat, slots_sat, "satellite", per_sat)

    return {
        "trades": pd.DataFrame(trades),
        "daily": pd.DataFrame(daily),
        "initial_capital": params.initial_capital,
        "ending_equity": float(daily[-1]["equity"]) if daily else params.initial_capital,
        "ending_cash": float(daily[-1]["cash"]) if daily else params.initial_capital,
        "ending_market_value": float(daily[-1]["market_value"]) if daily else 0.0,
    }
