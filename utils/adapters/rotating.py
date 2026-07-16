"""Adapter: rotating daily picks."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pandas as pd

from utils.registry import register

STRATEGY_NAME = "rotating"
STRATEGY_ROOT = Path(__file__).resolve().parents[2] / "strategy" / "rotating"

PICK_FIELDS = [
    "code",
    "name",
    "action",
    "score",
    "industry_name",
    "board",
    "composite_score",
    "position_mult",
    "n_max",
]


def _ensure_strategy_path() -> None:
    root = str(STRATEGY_ROOT)
    if root not in sys.path:
        sys.path.insert(0, root)


def _row_to_dict(row: pd.Series, reason: str | None = None) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for k in PICK_FIELDS:
        if k not in row.index:
            out[k] = None
            continue
        v = row[k]
        if hasattr(v, "item"):
            try:
                v = v.item()
            except (ValueError, AttributeError):
                pass
        if isinstance(v, float) and v != v:
            v = None
        if k == "code" and v is not None:
            v = str(v).zfill(6)
        out[k] = v
    out["reason"] = reason
    return out


def _df_to_records(df: pd.DataFrame) -> list[dict[str, Any]]:
    if df is None or len(df) == 0:
        return []
    rows = []
    for _, row in df.iterrows():
        reason = (
            row["reason"]
            if "reason" in row.index and pd.notna(row.get("reason"))
            else "rebalance"
        )
        rows.append(_row_to_dict(row, reason=str(reason)))
    return rows


def predict(
    asof: str | None = None,
    *,
    rebuild: bool = False,
    panel: str | Path | None = None,
) -> dict[str, Any]:
    _ensure_strategy_path()

    # TODO: implement once strategy code is built
    raise NotImplementedError(
        f"Strategy '{STRATEGY_NAME}' is under construction. "
        "See strategy/rotating/docs/STRATEGY.md for design."
    )


register(STRATEGY_NAME, predict)
