#!/usr/bin/env python3
"""Rebuild live v1.0 enriched panel from base latest; drop intermediate stage caches.

Internal stages (not versions): latest → sota → v26 → v27 → v1.0.
Requires catalog (+ northbound/margin views for final stage).
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CACHE = ROOT / "cache"


def run(script: str) -> None:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT)
    cmd = [sys.executable, str(ROOT / "cli" / script)]
    print("+", " ".join(cmd))
    subprocess.check_call(cmd, cwd=str(ROOT), env=env)


def main() -> None:
    base = CACHE / "signals_2020-01-01_latest.parquet"
    if not base.exists():
        raise SystemExit(
            f"missing base panel {base}; rebuild via features/build_panel or daily_update"
        )
    for script in (
        "build_sota_cache.py",
        "build_v26_cache.py",
        "build_v27_cache.py",
        "build_v28_cache.py",  # final stage writes signals_*_v1.0.parquet
    ):
        run(script)
    for name in (
        "signals_2020-01-01_sota.parquet",
        "signals_2020-01-01_v26.parquet",
        "signals_2020-01-01_v27.parquet",
        "signals_2020-01-01_v28.parquet",  # legacy name if any leftover
    ):
        p = CACHE / name
        if p.exists():
            p.unlink()
            print(f"removed intermediate {p.name}")
    out = CACHE / "signals_2020-01-01_v1.0.parquet"
    if not out.exists():
        raise SystemExit(f"expected {out} after rebuild")
    print(f"done → {out} ({out.stat().st_size / 1e6:.0f} MB)  [strategy panel v1.0]")


if __name__ == "__main__":
    main()
