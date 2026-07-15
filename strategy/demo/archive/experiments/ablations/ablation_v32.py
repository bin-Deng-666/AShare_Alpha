"""Ablation for v3.2: false-bull fuse vs v3.1."""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[3]
_REPO = Path(__file__).resolve().parents[5]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backtest.metrics import compute_metrics
from engine.portfolio import run_portfolio
from cli.run_backtest import load_bars, load_hs300
import importlib.util
from pathlib import Path as _Path
_leg_path = _Path(__file__).resolve().parents[2] / "config" / "versions_legacy.py"
_leg_spec = importlib.util.spec_from_file_location("versions_legacy", _leg_path)
_leg = importlib.util.module_from_spec(_leg_spec)
_leg_spec.loader.exec_module(_leg)
preset_v31 = _leg.preset_v31
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


def month_ret(daily: pd.DataFrame, ym: str) -> float:
    d = daily.copy()
    d["date"] = pd.to_datetime(d["date"])
    m = d[d["date"].dt.to_period("M").astype(str) == ym]
    if m.empty:
        return float("nan")
    return float(m["equity"].iloc[-1] / m["equity"].iloc[0] - 1.0)


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
    base = preset_v31()

    def on(**kw):
        return safe_replace(base, version="abl", enable_false_bull_fuse=True, **kw)

    variants = {
        "v3.1": base,
        "exp_only": on(false_bull_gate_exposure=True, false_bull_gate_risk_on=False),
        "gate_ron": on(
            false_bull_gate_exposure=True,
            false_bull_gate_risk_on=True,
        ),
        "block_opens": on(
            false_bull_gate_exposure=True,
            false_bull_gate_risk_on=False,
            false_bull_block_opens=True,
        ),
        "size_cut07": on(
            false_bull_gate_exposure=True,
            false_bull_gate_risk_on=False,
            false_bull_size_cut=0.70,
        ),
        "gate_ron+block": on(
            false_bull_gate_exposure=True,
            false_bull_gate_risk_on=True,
            false_bull_block_opens=True,
        ),
        "exp+mkt": on(
            false_bull_gate_exposure=True,
            false_bull_gate_risk_on=False,
            risk_on_require_mkt_expand=True,
        ),
        "gate_ron+size07": on(
            false_bull_gate_exposure=True,
            false_bull_gate_risk_on=True,
            false_bull_size_cut=0.70,
        ),
    }

    rows = []
    for name, p in variants.items():
        port = run_portfolio(sig, bars, names=names, params=p, market=hs)
        m = compute_metrics(port["daily"], port["trades"], hs, p.initial_capital)
        yr = year_rets(port["daily"])
        fb = (
            float(port["daily"]["false_bull"].mean())
            if "false_bull" in port["daily"].columns
            else 0.0
        )
        ron = (
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
            cash=m["trading"]["avg_cash_ratio"],
            ron=ron,
            fb=fb,
            y2020=yr.get(2020),
            y2024=yr.get(2024),
            y2025=yr.get(2025),
            m202402=month_ret(port["daily"], "2024-02"),
            m202105=month_ret(port["daily"], "2021-05"),
            m202106=month_ret(port["daily"], "2021-06"),
            m202605=month_ret(port["daily"], "2026-05"),
        )
        rows.append(row)
        print(
            f"{name:16s} ret={row['ret']:+.1%} sh={row['sharpe']:.2f} dd={row['dd']:.1%} "
            f"cash={row['cash']:.0%} ron={row['ron']:.0%} fb={row['fb']:.0%} "
            f"y20={row['y2020']:+.1%} y24={row['y2024']:+.1%} "
            f"feb24={row['m202402']:+.1%} jun21={row['m202106']:+.1%} may26={row['m202605']:+.1%}",
            flush=True,
        )

    out = pd.DataFrame(rows)
    path = Path("experiments/ablations/ablation_v32.csv")
    out.to_csv(path, index=False)
    print(f"wrote {path.resolve()}")


if __name__ == "__main__":
    main()
