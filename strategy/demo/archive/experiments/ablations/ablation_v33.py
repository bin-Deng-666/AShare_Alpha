"""Ablation for v3.3: rare mainline diffusion (top-N + cap) vs v3.2."""
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
preset_v32 = _leg.preset_v32
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
    base = preset_v32()

    def on(**kw):
        return safe_replace(base, version="abl", enable_mainline_diffusion=True, **kw)

    variants = {
        "v3.2": base,
        # rare top-3 + 1 extra/day
        "struct_top3": on(
            mainline_allow_structure=True,
            mainline_top_n=3,
            mainline_max_extra_per_day=1,
        ),
        "struct_top2_ron": on(
            mainline_allow_structure=True,
            mainline_top_n=2,
            mainline_require_risk_on=True,
            mainline_max_extra_per_day=1,
        ),
        "struct_top1": on(
            mainline_allow_structure=True,
            mainline_top_n=1,
            mainline_max_extra_per_day=1,
            mainline_peak_pct=0.90,
            mainline_still_pct=0.80,
        ),
        "size115_top3": on(
            mainline_allow_structure=False,
            mainline_top_n=3,
            mainline_score_boost=1.15,
        ),
        "hold_proxy_score105": on(
            mainline_allow_structure=False,
            mainline_top_n=3,
            mainline_score_boost=1.05,
        ),
        # exit-side soft: skip is already risk_on; try structure+ron+top2
        "struct_top2": on(
            mainline_allow_structure=True,
            mainline_top_n=2,
            mainline_max_extra_per_day=1,
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
            f"{name:18s} ret={row['ret']:+.1%} sh={row['sharpe']:.2f} dd={row['dd']:.1%} "
            f"buys={n_buys} cash={row['cash']:.0%} "
            f"y20={row['y2020']:+.1%} y24={row['y2024']:+.1%} y25={row['y2025']:+.1%}",
            flush=True,
        )

    # merge with prior wide ablation if present
    out = ROOT / "experiments" / "ablations" / "ablation_v33b.csv"
    pd.DataFrame(rows).to_csv(out, index=False)
    print(f"wrote {out}", flush=True)


if __name__ == "__main__":
    main()
