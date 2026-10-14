"""CLI adapter for the one-instrument buy-and-hold demo."""

from pathlib import Path
from strategy.demo.run import daily
from ashare_alpha.research.artifacts import ROOT


def predict(asof=None, *, rebuild=False, panel=None, capital=None):
    result = daily(
        asof, capital=capital, snapshot=Path(panel) if panel else None, rebuild=rebuild
    )
    result["output_root"] = str(ROOT / "demo/plans")
    return result
