"""Canonical panel path for regime_sparse v0.1 (base panel + in-engine SOTA)."""

from __future__ import annotations

from pathlib import Path

from config.params import CACHE_DIR, SAMPLE_START

PANEL_V01 = CACHE_DIR / f"signals_{SAMPLE_START}_v0.1.parquet"


def default_panel_path(*, allow_legacy: bool = True) -> Path:
    """Return the panel path live code should use."""
    del allow_legacy  # no legacy alias yet
    return PANEL_V01
