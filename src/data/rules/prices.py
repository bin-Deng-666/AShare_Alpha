"""Shared exchange-price normalization rules."""

from __future__ import annotations

import numpy as np
import pandas as pd


def round_cny_price_to_cent(values: pd.Series) -> pd.Series:
    """Round CNY prices to the one-cent exchange grid using half-up ties."""
    numeric = pd.to_numeric(values, errors="coerce")
    return np.floor(numeric * 100.0 + 0.5 + 1e-8) / 100.0


def cny_cent_key(value: float) -> int | None:
    """Return a deterministic integer-cent key for a positive CNY price."""
    if not np.isfinite(value) or value <= 0.0:
        return None
    return int(np.floor(value * 100.0 + 0.5 + 1e-8))
