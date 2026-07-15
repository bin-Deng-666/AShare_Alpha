"""Ablation for v3.0 Risk-On (Absolute Momentum + breadth) vs v2.8."""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backtest.metrics import compute_metrics
from backtest.portfolio import run_portfolio
from cli.run_backtest import load_bars, load_hs300
import importlib.util
from pathlib import Path as _Path
_leg_path = _Path(__file__).resolve().parents[2] / "config" / "versions_legacy.py"
_leg_spec = importlib.util.spec_from_file_location("versions_legacy", _leg_path)
_leg = importlib.util.module_from_spec(_leg_spec)
_leg_spec.loader.exec_module(_leg)
preset_v28 = _leg.preset_v28
from features.build_panel import load_signals


def year_rets(daily: pd.DataFrame) -> dict[int, float]:
    d = daily.copy()
    d["date"] = pd.to_datetime(d["date"])
    d["year"] = d["date"].dt.year
    d = d.sort_values("date")
    return {
        int(y): float(g["equity"].iloc[-1]) / float(g["equity"].iloc[0]) - 1.0
        for y, g in d.groupby("year")
    }


def main() -> None:
    panel = Path("cache/signals_2020-01-01_v1.0.parquet")
    sig = load_signals(panel)
    sig["date"] = pd.to_datetime(sig["date"])
    sig = sig[sig["date"] >= "2020-01-01"]
    names = (
        sig.drop_duplicates("code")
        .set_index("code")["name"]
        .fillna("")
        .astype(str)
        .to_dict()
    )
    bars = load_bars("2020-01-01")
    hs = load_hs300("2020-01-01")
    base = preset_v28()

    def on(**kw):
        return safe_replace(base, version="abl", enable_risk_on=True, **kw)

    variants = {
        "v2.8": base,
        # P0 exit only
        "P0_exit": on(risk_on_exposure=False, risk_on_exit_policy=True),
        "P0_no_breadth": on(
            risk_on_exposure=False,
            risk_on_exit_policy=True,
            risk_on_require_breadth=False,
        ),
        "P0_tp22": on(risk_on_exposure=False, risk_on_take_profit_pct=0.22),
        "P0_no_tp": on(
            risk_on_exposure=False,
            risk_on_disable_tp=True,
            risk_on_take_profit_pct=0.22,
        ),
        # P1 exposure only (no exit change)
        "P1_exp": on(
            risk_on_exit_policy=False,
            risk_on_exposure=True,
            risk_on_exposure_mult=1.25,
        ),
        # P0+P1
        "P0+P1": on(
            risk_on_exit_policy=True,
            risk_on_exposure=True,
            risk_on_exposure_mult=1.25,
        ),
        "P0+P1_mild": on(
            risk_on_exit_policy=True,
            risk_on_exposure=True,
            risk_on_exposure_mult=1.15,
            risk_on_extra_entries=1,
        ),
        "P0+P1_no_breadth": on(
            risk_on_require_breadth=False,
            risk_on_exit_policy=True,
            risk_on_exposure=True,
            risk_on_exposure_mult=1.25,
        ),
        # legacy v2.4-style for contrast
        "legacy_bull": safe_replace(
            base,
            enable_regime_exposure=True,
            enable_bull_hold=True,
            bull_exposure_mult=1.35,
        ),
    }
    rows = []
    for name, p in variants.items():
        port = run_portfolio(sig, bars, names=names, params=p, market=hs)
        m = compute_metrics(port["daily"], port["trades"], hs, p.initial_capital)
        y = year_rets(port["daily"])
        cash = m["trading"]["avg_cash_ratio"]
        risk_on_share = (
            float(port["daily"]["risk_on"].mean())
            if "risk_on" in port["daily"].columns
            else 0.0
        )
        row = dict(
            variant=name,
            ret=m["capital"]["total_return"],
            sharpe=m["risk"]["sharpe"],
            dd=m["risk"]["max_drawdown"],
            win=m["trading"]["win_rate"],
            pay=m["trading"]["payoff_ratio"],
            n_buys=int((port["trades"]["side"] == "BUY").sum()) if len(port["trades"]) else 0,
            cash=cash,
            risk_on=risk_on_share,
            y2020=y.get(2020),
            y2024=y.get(2024),
            y2025=y.get(2025),
        )
        rows.append(row)
        print(
            f"{name:18s} ret={row['ret']:+.1%} sh={row['sharpe']:.2f} dd={row['dd']:.1%} "
            f"cash={cash:.0%} ron={risk_on_share:.0%} "
            f"y20={row['y2020']:+.1%} y24={row['y2024']:+.1%} y25={row['y2025']:+.1%}"
        )

    out = ROOT / "experiments" / "ablations" / "ablation_v30.csv"
    pd.DataFrame(rows).to_csv(out, index=False)
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
