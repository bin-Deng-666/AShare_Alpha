"""Ablation for v2.8: market northbound + SSE margin soft overlays vs v2.7."""
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
preset_v27 = _leg.preset_v27
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
    sig28 = load_signals(Path("cache/signals_2020-01-01_v1.0.parquet"))
    sig28["date"] = pd.to_datetime(sig28["date"])
    sig28 = sig28[sig28["date"] >= "2020-01-01"]
    names = (
        sig28.drop_duplicates("code")
        .set_index("code")["name"]
        .fillna("")
        .astype(str)
        .to_dict()
    )
    bars = load_bars("2020-01-01")
    hs = load_hs300("2020-01-01")
    base = preset_v27()

    def on(**kw):
        return safe_replace(base, version="abl", **kw)

    variants = {
        "v2.7": base,
        "nb_soft": on(enable_nb_flow_soft=True),
        "nb_boost": on(enable_nb_flow_boost=True),
        "nb_soft+boost": on(enable_nb_flow_soft=True, enable_nb_flow_boost=True),
        "mg_stress": on(enable_margin_stress=True),
        "nb_soft+mg": on(enable_nb_flow_soft=True, enable_margin_stress=True),
        "all_flow": on(
            enable_nb_flow_soft=True,
            enable_nb_flow_boost=True,
            enable_margin_stress=True,
        ),
        # milder size cuts
        "nb_soft_mild": on(
            enable_nb_flow_soft=True,
            nb_weak_size_mult=0.92,
            nb_weak_block_extend=True,
            nb_weak_as_stress=True,
        ),
        "mg_mild": on(
            enable_margin_stress=True,
            mg_stress_size_mult=0.95,
            mg_stress_block_extend=True,
            mg_stress_as_stress=False,
        ),
        "nb_size_only": on(
            enable_nb_flow_soft=True,
            nb_weak_size_mult=0.85,
            nb_weak_block_extend=False,
            nb_weak_as_stress=False,
        ),
        "mg_stress_exit_only": on(
            enable_margin_stress=True,
            mg_stress_size_mult=1.0,
            mg_stress_block_extend=False,
            mg_stress_as_stress=True,
        ),
    }
    rows = []
    for name, p in variants.items():
        port = run_portfolio(sig28, bars, names=names, params=p, market=hs)
        m = compute_metrics(port["daily"], port["trades"], hs, p.initial_capital)
        y = year_rets(port["daily"])
        n_buys = int((port["trades"]["side"] == "BUY").sum()) if len(port["trades"]) else 0
        cash = m["trading"]["avg_cash_ratio"]
        row = dict(
            variant=name,
            ret=m["capital"]["total_return"],
            sharpe=m["risk"]["sharpe"],
            dd=m["risk"]["max_drawdown"],
            win=m["trading"]["win_rate"],
            pay=m["trading"]["payoff_ratio"],
            n_buys=n_buys,
            cash=cash,
            y2020=y.get(2020),
            y2021=y.get(2021),
            y2022=y.get(2022),
            y2023=y.get(2023),
            y2024=y.get(2024),
            y2025=y.get(2025),
            y2026=y.get(2026),
        )
        rows.append(row)
        print(
            f"{name:20s} ret={row['ret']:+.1%} sharpe={row['sharpe']:.2f} "
            f"dd={row['dd']:.1%} buys={n_buys} cash={cash:.0%}"
        )

    out = Path("experiments/ablations/ablation_v28.csv")
    pd.DataFrame(rows).to_csv(out, index=False)
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
