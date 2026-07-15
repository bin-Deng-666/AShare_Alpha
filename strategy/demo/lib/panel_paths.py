"""Canonical panel path for live strategy v1.0.

Only one public version: **v1.0** (params + enriched panel).
`build_v26/v27/v28_cache.py` are internal enrichment *stages*, not strategy versions.
"""

from __future__ import annotations

from pathlib import Path

from config.params import CACHE_DIR, SAMPLE_START

# Public artifact name matches strategy version.
PANEL_V10 = CACHE_DIR / f"signals_{SAMPLE_START}_v1.0.parquet"

# Legacy filename from panel enrichment stage history (pre–version-unify).
_LEGACY_PANEL_V28 = CACHE_DIR / f"signals_{SAMPLE_START}_v28.parquet"


def default_panel_path(*, allow_legacy: bool = True) -> Path:
    """Return the panel path live code should use."""
    if PANEL_V10.exists():
        return PANEL_V10
    if allow_legacy and _LEGACY_PANEL_V28.exists():
        return _LEGACY_PANEL_V28
    return PANEL_V10


def ensure_v10_panel_alias() -> Path | None:
    """If only legacy v28 exists, hardlink/copy it to v1.0 name. Returns path or None."""
    if PANEL_V10.exists():
        return PANEL_V10
    if not _LEGACY_PANEL_V28.exists():
        return None
    import os
    import shutil

    try:
        os.link(_LEGACY_PANEL_V28, PANEL_V10)
    except OSError:
        shutil.copy2(_LEGACY_PANEL_V28, PANEL_V10)
    return PANEL_V10
