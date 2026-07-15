"""Ablation for v2.4 regime exposure / vol target / bull hold."""
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
preset_v23 = _leg.preset_v23
preset_v24 = _leg.preset_v24
from features.build_panel import load_signals


def year_rets(daily: pd.DataFrame) -> dict[int, float]:
    d = daily.copy()
    d["date"] = pd.to_datetime(d["date"])
    d["year"] = d["date"].dt.year
    d = d.sort_values("date")
    out: dict[int, float] = {}
    for y, g in d.groupby("year"):
        e0 = float(g["equity"].iloc[0])
        e1 = float(g["equity"].iloc[-1])
        out[int(y)] = e1 / e0 - 1.0 if e0 else 0.0
    return out


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
    base23 = preset_v23()
    deploy = dict(
        max_entries_per_day=3,
        cash_refill_ratio=0.4,
        cash_refill_extra_entries=2,
        max_single_weight=0.28,
    )
    variants = {
        "v2.3": base23,
        "deploy_only": safe_replace(base23, version="abl_deploy", **deploy),
        "exposure": safe_replace(
            base23,
            version="abl_exp",
            enable_regime_exposure=True,
            enable_vol_target=False,
            enable_bull_hold=False,
            enable_bull_pyramid=False,
            **deploy,
        ),
        "exp+vol": safe_replace(
            base23,
            version="abl_ev",
            enable_regime_exposure=True,
            enable_vol_target=True,
            enable_bull_hold=False,
            enable_bull_pyramid=False,
            **deploy,
        ),
        "exp+vol+hold": safe_replace(
            base23,
            version="abl_evh",
            enable_regime_exposure=True,
            enable_vol_target=True,
            enable_bull_hold=True,
            enable_bull_pyramid=False,
            **deploy,
        ),
        "v2.4_full": preset_v24(),
    }
    rows = []
    for name, p in variants.items():
        port = run_portfolio(sig, bars, names=names, params=p, market=hs)
        m = compute_metrics(port["daily"], port["trades"], hs, p.initial_capital)
        yret = year_rets(port["daily"])
        row = {
            "variant": name,
            "ret": m["capital"]["total_return"],
            "sharpe": m["risk"]["sharpe"],
            "dd": m["risk"]["max_drawdown"],
            "win": m["trading"]["win_rate"],
            "pay": m["trading"]["payoff_ratio"],
            "cash": m["trading"]["avg_cash_ratio"],
            "beta": m["relative"]["beta"],
            "y2020": yret.get(2020),
            "y2025": yret.get(2025),
        }
        rows.append(row)
        print(
            f"{name:14} ret={row['ret']:.3f} sh={row['sharpe']:.3f} "
            f"dd={row['dd']:.3f} cash={row['cash']:.3f} beta={row['beta']:.3f} "
            f"2020={row['y2020']:.3f} 2025={row['y2025']:.3f}"
        )
    out = Path("experiments/ablations/ablation_v24.csv")
    pd.DataFrame(rows).to_csv(out, index=False)
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
