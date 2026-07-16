#!/usr/bin/env python3
"""Rebuild regime_sparse v0.1 base signal panel (no v26/v27/v28 enrichment chain).

T+1-safe base features only; SOTA turn_cv is enriched inside the portfolio engine
when enable_sota_factors=True.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(REPO))

from config.params import DEFAULT_PARAMS, SAMPLE_START
from features.build_panel import build_signal_panel
from lib.db import ensure_dirs
from lib.panel_paths import PANEL_V01


def main() -> None:
    ensure_dirs()
    out = PANEL_V01
    print(f"Building base panel → {out}")
    path = build_signal_panel(
        start=SAMPLE_START,
        end=None,
        params=DEFAULT_PARAMS,
        out_path=out,
    )
    print(f"done → {path} ({path.stat().st_size / 1e6:.0f} MB)  [regime_sparse {DEFAULT_PARAMS.version}]")


if __name__ == "__main__":
    main()
