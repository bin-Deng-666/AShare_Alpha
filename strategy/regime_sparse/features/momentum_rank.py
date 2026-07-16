"""MACD / KDJ ranking overlays for open_A (score boost only, never hard filters).

Status features — not same-bar golden-cross gates — so thrust_up days are not
killed the way anti_chase / path_eff were in LESSONS.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from config.params import Params


def _ema(s: pd.Series, span: int) -> pd.Series:
    return s.astype(float).ewm(span=span, adjust=False, min_periods=max(3, span // 2)).mean()


def _macd_cols(g: pd.DataFrame) -> pd.DataFrame:
    close = g["close"].astype(float)
    dif = _ema(close, 12) - _ema(close, 26)
    dea = _ema(dif, 9)
    hist = 2.0 * (dif - dea)
    out = pd.DataFrame(index=g.index)
    out["macd_dif"] = dif
    out["macd_dea"] = dea
    out["macd_hist"] = hist
    out["macd_hist_prev"] = hist.shift(1)
    return out


def _kdj_cols(g: pd.DataFrame, n: int = 9) -> pd.DataFrame:
    high = g["high"].astype(float)
    low = g["low"].astype(float)
    close = g["close"].astype(float)
    low_n = low.rolling(n, min_periods=max(3, n // 2)).min()
    high_n = high.rolling(n, min_periods=max(3, n // 2)).max()
    denom = (high_n - low_n).replace(0, np.nan)
    rsv = (close - low_n) / denom * 100.0
    # Classic A-share KDJ smoother: K=2/3 prev + 1/3 RSV
    k = rsv.copy()
    d = rsv.copy()
    k_vals = rsv.to_numpy(dtype=float)
    k_out = np.full_like(k_vals, np.nan)
    d_out = np.full_like(k_vals, np.nan)
    prev_k = 50.0
    prev_d = 50.0
    for i, rv in enumerate(k_vals):
        if not np.isfinite(rv):
            k_out[i] = np.nan
            d_out[i] = np.nan
            continue
        prev_k = (2.0 / 3.0) * prev_k + (1.0 / 3.0) * rv
        prev_d = (2.0 / 3.0) * prev_d + (1.0 / 3.0) * prev_k
        k_out[i] = prev_k
        d_out[i] = prev_d
    out = pd.DataFrame(index=g.index)
    out["kdj_k"] = k_out
    out["kdj_d"] = d_out
    out["kdj_j"] = 3.0 * out["kdj_k"] - 2.0 * out["kdj_d"]
    out["kdj_j_prev"] = out["kdj_j"].shift(1)
    return out


def enrich_momentum_rank(signals: pd.DataFrame) -> pd.DataFrame:
    """Add MACD/KDJ columns for codes that ever print open_A."""
    need = {"code", "date", "close", "high", "low"}
    if not need.issubset(signals.columns):
        return signals
    have = {"macd_hist", "kdj_j"}
    if have.issubset(signals.columns):
        return signals

    s = signals.copy()
    s["date"] = pd.to_datetime(s["date"])
    s = s.sort_values(["code", "date"])

    if "action" in s.columns:
        codes = set(s.loc[s["action"].astype(str).eq("open_A"), "code"].astype(str).unique())
        work = s[s["code"].astype(str).isin(codes)].copy()
    else:
        work = s
        codes = None

    if work.empty:
        return s

    macd_parts: list[pd.DataFrame] = []
    kdj_parts: list[pd.DataFrame] = []
    for _, g in work.groupby("code", sort=False):
        macd_parts.append(_macd_cols(g))
        kdj_parts.append(_kdj_cols(g))
    macd = pd.concat(macd_parts)
    kdj = pd.concat(kdj_parts)
    work = work.join(macd).join(kdj)

    # Soft quality scores in [0, 1] — status, not cross events
    hist = work["macd_hist"]
    hist_prev = work["macd_hist_prev"]
    dif = work["macd_dif"]
    dea = work["macd_dea"]
    macd_q = np.zeros(len(work), dtype=float)
    macd_q = np.where(dif > dea, macd_q + 0.45, macd_q)
    macd_q = np.where(hist > 0, macd_q + 0.30, macd_q)
    macd_q = np.where(hist > hist_prev, macd_q + 0.25, macd_q)
    work["macd_rank_q"] = macd_q

    k = work["kdj_k"]
    d = work["kdj_d"]
    j = work["kdj_j"]
    j_prev = work["kdj_j_prev"]
    kdj_q = np.zeros(len(work), dtype=float)
    kdj_q = np.where(k > d, kdj_q + 0.45, kdj_q)
    kdj_q = np.where(j > j_prev, kdj_q + 0.25, kdj_q)
    kdj_q = np.where(j < 90.0, kdj_q + 0.30, kdj_q)
    # Soft penalty when already frothy (thrust chasing climax)
    kdj_q = np.where(j >= 90.0, kdj_q * 0.35, kdj_q)
    work["kdj_rank_q"] = kdj_q

    cols = [
        "macd_dif",
        "macd_dea",
        "macd_hist",
        "macd_hist_prev",
        "macd_rank_q",
        "kdj_k",
        "kdj_d",
        "kdj_j",
        "kdj_j_prev",
        "kdj_rank_q",
    ]
    if codes is not None:
        for c in cols:
            s[c] = np.nan
        s.loc[work.index, cols] = work[cols].to_numpy()
        return s
    return work


def _boost_by_quality(
    df: pd.DataFrame, col: str, floor: float
) -> pd.DataFrame:
    if df.empty or "score" not in df.columns or col not in df.columns:
        return df
    out = df.copy()
    # Within-day rank of quality (higher better)
    r = out.groupby("date")[col].rank(pct=True, ascending=True)
    good = r.fillna(0.5)
    fl = float(floor)
    fl = min(max(fl, 0.0), 1.0)
    out["score"] = out["score"].astype(float) * (fl + (1.0 - fl) * good)
    return out


def apply_macd_score_boost(df: pd.DataFrame, params: Params) -> pd.DataFrame:
    if not getattr(params, "enable_macd_rank", False):
        return df
    return _boost_by_quality(df, "macd_rank_q", getattr(params, "macd_rank_floor", 0.85))


def apply_kdj_score_boost(df: pd.DataFrame, params: Params) -> pd.DataFrame:
    if not getattr(params, "enable_kdj_rank", False):
        return df
    return _boost_by_quality(df, "kdj_rank_q", getattr(params, "kdj_rank_floor", 0.85))
