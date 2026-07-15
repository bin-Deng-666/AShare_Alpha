"""Version presets for the live lineage (A-share realistic execution).

Only **v1.0** is live. Strategy params, reports, and enriched panel share this id
(`cache/signals_*_v1.0.parquet`). Panel rebuild scripts named build_v26/v28 are
internal enrichment stages, not separate strategy versions.

Legacy v2.0–v3.3 (flat 15bp) presets live in
`archive/config/versions_legacy.py`.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace

from config.params import DEFAULT_PARAMS, Params


def preset_v10() -> Params:
    """v1.0: v3.3 alpha + A-share fee stack + open slippage (research default)."""
    return replace(DEFAULT_PARAMS, version="v1.0")


VERSION_PRESETS: dict[str, Callable[[], Params]] = {
    "v1.0": preset_v10,
}

ARCHIVABLE = ("v1.0",)
CURRENT_VERSION = "v1.0"
