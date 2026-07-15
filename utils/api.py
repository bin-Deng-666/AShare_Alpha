"""Public API: predict_next / list_strategies."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from utils.registry import get_predictor, list_registered
from utils.sink import write_run_bundle
from utils.sizing import attach_orders

REPO_ROOT = Path(__file__).resolve().parents[1]
STRATEGIES_ROOT = REPO_ROOT / "strategy"
OUTPUT_DIR = REPO_ROOT / "output"


def list_strategies() -> list[str]:
    return list_registered()


def _sizing_kwargs(strategy: str, capital: float | None) -> dict[str, Any]:
    """Load sizing params from strategy defaults when available."""
    kw: dict[str, Any] = {}
    if capital is not None:
        kw["capital"] = float(capital)
    if strategy != "crowding_price_volume":
        kw.setdefault("capital", 1_000_000.0)
        return kw
    try:
        import sys

        root = str(STRATEGIES_ROOT / "demo")
        if root not in sys.path:
            sys.path.insert(0, root)
        from config.params import DEFAULT_PARAMS as p

        kw.setdefault("capital", float(p.initial_capital))
        kw.update(
            top_n=int(p.top_n),
            max_single_weight=float(p.max_single_weight),
            satellite_weight_frac=float(p.satellite_weight_frac),
            reduce_frac=float(p.reduce_frac),
            lot_size=int(p.lot_size),
            min_position_mult_to_buy=float(p.min_position_mult_to_buy),
        )
    except Exception:
        kw.setdefault("capital", 1_000_000.0)
    return kw


def predict_next(
    strategy: str,
    asof: str | None = None,
    *,
    rebuild: bool = False,
    panel: str | Path | None = None,
    write: bool = True,
    capital: float | None = None,
) -> dict[str, Any]:
    """Cross-section as of `asof` → T+1 open action list (not a price forecast)."""
    fn = get_predictor(strategy)
    result = fn(asof=asof, rebuild=rebuild, panel=panel)
    result.setdefault("strategy", strategy)
    result.setdefault("exec_hint", "T+1_open")
    exec_date = result.get("exec_date") or result.get("asof")
    result["exec_date"] = exec_date
    attach_orders(result, **_sizing_kwargs(strategy, capital))
    if write:
        out_dir = OUTPUT_DIR / strategy / str(exec_date).replace("-", "")
        paths = write_run_bundle(out_dir, result)
        result["paths"] = {k: str(v) for k, v in paths.items()}
    else:
        result.setdefault("paths", {})
    return result
