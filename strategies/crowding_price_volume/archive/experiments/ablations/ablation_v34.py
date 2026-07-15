"""Ablation for v3.4: smart sizing (inv-vol + conviction tiers) vs v3.3."""
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
preset_v33 = _leg.preset_v33
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
    sig = load_signals(Path("cache/signals_2020-01-01_v1.0.parquet"))
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
    base = preset_v33()

    def on(**kw):
        return safe_replace(base, version="abl", **kw)

    variants = {
        "v3.3": base,
        "inv_vol": on(enable_inv_vol_sizing=True),
        "conv": on(enable_conviction_sizing=True),
        "inv+conv": on(enable_inv_vol_sizing=True, enable_conviction_sizing=True),
        "inv_tight": on(
            enable_inv_vol_sizing=True,
            inv_vol_floor=0.020,
            inv_vol_cap=0.060,
        ),
        "conv_mild": on(
            enable_conviction_sizing=True,
            conv_low_mult=0.85,
            conv_high_mult=1.15,
        ),
        "inv+conv_mild": on(
            enable_inv_vol_sizing=True,
            enable_conviction_sizing=True,
            conv_low_mult=0.85,
            conv_high_mult=1.15,
        ),
        "conv_no_ron": on(
            enable_conviction_sizing=True,
            conv_count_risk_on=False,
        ),
    }

    rows = []
    for name, p in variants.items():
        port = run_portfolio(sig, bars, names=names, params=p, market=hs)
        m = compute_metrics(port["daily"], port["trades"], hs, p.initial_capital)
        yr = year_rets(port["daily"])
        n_buys = int((port["trades"]["side"] == "BUY").sum()) if len(port["trades"]) else 0
        row = dict(
            variant=name,
            ret=m["capital"]["total_return"],
            sharpe=m["risk"]["sharpe"],
            dd=m["risk"]["max_drawdown"],
            win=m["trading"]["win_rate"],
            pay=m["trading"]["payoff_ratio"],
            n_buys=n_buys,
            cash=m["trading"]["avg_cash_ratio"],
            y2020=yr.get(2020),
            y2024=yr.get(2024),
            y2025=yr.get(2025),
        )
        rows.append(row)
        print(
            f"{name:14s} ret={row['ret']:+.1%} sh={row['sharpe']:.2f} dd={row['dd']:.1%} "
            f"buys={n_buys} cash={row['cash']:.0%} "
            f"y20={row['y2020']:+.1%} y24={row['y2024']:+.1%} y25={row['y2025']:+.1%}",
            flush=True,
        )

    out = ROOT / "experiments" / "ablations" / "ablation_v34.csv"
    pd.DataFrame(rows).to_csv(out, index=False)
    print(f"wrote {out}", flush=True)


if __name__ == "__main__":
    main()
