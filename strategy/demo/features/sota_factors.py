"""SOTA-inspired secondary factors for sparse crowding × price-volume entries.

Computed from the existing signals panel (no catalog rebuild required).
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from config.params import Params


def enrich_sota_factors(signals: pd.DataFrame, lookback: int = 20) -> pd.DataFrame:
    """Add turnover stability / return-vol columns used by turn-stab score."""
    need = {"code", "date", "pct_change", "turnover_pct"}
    if not need.issubset(signals.columns):
        return signals
    if "turn_cv_20" in signals.columns and "ret_vol_20" in signals.columns:
        return signals
    s = signals.copy()
    s["date"] = pd.to_datetime(s["date"])
    s = s.sort_values(["code", "date"])

    open_mask = s["action"].isin(["open_A", "open_B", "open_C"]) if "action" in s.columns else slice(None)
    if isinstance(open_mask, pd.Series):
        codes = set(s.loc[open_mask, "code"].astype(str).unique())
        work = s[s["code"].astype(str).isin(codes)].copy()
    else:
        work = s
        codes = None

    g = work.groupby("code", sort=False)
    min_p = max(8, lookback // 2)
    work["ret_vol_20"] = g["pct_change"].transform(lambda x: x.rolling(lookback, min_periods=min_p).std())
    turn_mean = g["turnover_pct"].transform(lambda x: x.rolling(lookback, min_periods=min_p).mean())
    turn_std = g["turnover_pct"].transform(lambda x: x.rolling(lookback, min_periods=min_p).std())
    work["turn_cv_20"] = turn_std / turn_mean.replace(0, np.nan)

    cols = ["ret_vol_20", "turn_cv_20"]
    if codes is not None:
        for c in cols:
            s[c] = np.nan
        s.loc[work.index, cols] = work[cols].to_numpy()
        return s
    return work


def apply_sota_open_filters(df: pd.DataFrame, params: Params) -> pd.DataFrame:
    """Reserved hook — v1.0 ships score boost only; hard filters removed."""
    return df


def apply_sota_score_boost(df: pd.DataFrame, params: Params) -> pd.DataFrame:
    """Re-rank opens: prefer stable turnover."""
    if df.empty or "score" not in df.columns:
        return df
    out = df.copy()
    score = out["score"].astype(float)

    if params.enable_turn_stab_score and "turn_cv_20" in out.columns:
        r = out.groupby("date")["turn_cv_20"].rank(pct=True, ascending=True)
        good = (1.0 - r).fillna(0.5)
        score = score * (params.sota_score_floor + (1.0 - params.sota_score_floor) * 2 * good)

    out["score"] = score
    return out
