"""SOTA turn-stability score for open_A ranking (computed in-engine on panel)."""

from __future__ import annotations

import numpy as np
import pandas as pd

from config.params import Params


def enrich_sota_factors(signals: pd.DataFrame, lookback: int = 20) -> pd.DataFrame:
    """Add turn_cv_20 / ret_vol_20 for codes that ever print open_A."""
    need = {"code", "date", "pct_change", "turnover_pct"}
    if not need.issubset(signals.columns):
        return signals
    if "turn_cv_20" in signals.columns and "ret_vol_20" in signals.columns:
        return signals

    s = signals.copy()
    s["date"] = pd.to_datetime(s["date"])
    s = s.sort_values(["code", "date"])

    if "action" in s.columns:
        codes = set(s.loc[s["action"].eq("open_A"), "code"].astype(str).unique())
        work = s[s["code"].astype(str).isin(codes)].copy()
    else:
        work = s
        codes = None

    if work.empty:
        return s

    g = work.groupby("code", sort=False)
    min_p = max(8, lookback // 2)
    work["ret_vol_20"] = g["pct_change"].transform(
        lambda x: x.rolling(lookback, min_periods=min_p).std()
    )
    turn_mean = g["turnover_pct"].transform(
        lambda x: x.rolling(lookback, min_periods=min_p).mean()
    )
    turn_std = g["turnover_pct"].transform(
        lambda x: x.rolling(lookback, min_periods=min_p).std()
    )
    work["turn_cv_20"] = turn_std / turn_mean.replace(0, np.nan)

    if codes is not None:
        for c in ("ret_vol_20", "turn_cv_20"):
            s[c] = np.nan
        s.loc[work.index, ["ret_vol_20", "turn_cv_20"]] = work[
            ["ret_vol_20", "turn_cv_20"]
        ].to_numpy()
        return s
    return work


def apply_sota_score_boost(df: pd.DataFrame, params: Params) -> pd.DataFrame:
    """Prefer low turnover CV when ranking opens."""
    if df.empty or "score" not in df.columns:
        return df
    if not params.enable_turn_stab_score or "turn_cv_20" not in df.columns:
        return df
    out = df.copy()
    r = out.groupby("date")["turn_cv_20"].rank(pct=True, ascending=True)
    good = (1.0 - r).fillna(0.5)
    floor = float(params.sota_score_floor)
    out["score"] = out["score"].astype(float) * (floor + (1.0 - floor) * 2 * good)
    return out
