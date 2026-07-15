"""Market breadth / internals / HS300 overlay for risk-on and false-bull."""

from __future__ import annotations

import numpy as np
import pandas as pd

from config.params import Params


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
        run = both.astype(int).groupby((~both).cumsum()).cumsum()
        out["false_bull"] = run >= streak_n
    return out


def _build_market_overlay(
    market: pd.DataFrame | None,
    params: Params,
    breadth: pd.Series | None = None,
    internals: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """HS300 trend / Risk-On by date (+ optional false-bull flags)."""
    need = params.enable_risk_on or params.enable_false_bull_fuse
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

    out = pd.DataFrame(
        {
            "date": m["date"].values,
            "bull": bull.fillna(False).astype(bool).values,
            "abs_mom": abs_mom.fillna(False).astype(bool).values,
            "vol_scale": 1.0,
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

    exposure_ok = out["risk_on"].astype(bool)
    if params.enable_false_bull_fuse and params.false_bull_block_both_shrink and params.false_bull_gate_exposure:
        exposure_ok = exposure_ok & ~out["false_bull"]
    out["risk_on_exposure_ok"] = exposure_ok.fillna(False).astype(bool)
    return out
