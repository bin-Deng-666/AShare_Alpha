"""Open-candidate enrichment, filters, and flow/scarcity sizing helpers."""

from __future__ import annotations

import numpy as np
import pandas as pd

from config.params import Params


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
