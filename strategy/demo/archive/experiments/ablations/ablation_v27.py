"""Ablation for v2.7: exact event blackout + index tier sizing."""
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
preset_v26 = _leg.preset_v26
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
    sig27 = load_signals(Path("cache/signals_2020-01-01_v1.0.parquet"))
    sig27["date"] = pd.to_datetime(sig27["date"])
    sig27 = sig27[sig27["date"] >= "2020-01-01"]
    sig26 = load_signals(Path("cache/signals_2020-01-01_v1.0.parquet"))
    sig26["date"] = pd.to_datetime(sig26["date"])
    sig26 = sig26[sig26["date"] >= "2020-01-01"]
    names = (
        sig27.drop_duplicates("code")
        .set_index("code")["name"]
        .fillna("")
        .astype(str)
        .to_dict()
    )
    bars = load_bars("2020-01-01")
    hs = load_hs300("2020-01-01")
    b26 = preset_v26()

    def on(**kw):
        return safe_replace(
            b26,
            version="abl",
            enable_sota_factors=True,
            enable_turn_stab_score=True,
            enable_limit_up_crowding=True,
            **kw,
        )

    variants = {
        "v2.6": (b26, sig26),
        "earn_exact": (
            on(
                enable_earnings_blackout=True,
                earnings_use_event_blackout=False,
                enable_index_tier_sizing=False,
            ),
            sig27,
        ),
        "event_bo": (
            on(
                enable_earnings_blackout=True,
                earnings_use_event_blackout=True,
                enable_index_tier_sizing=False,
            ),
            sig27,
        ),
        "tier_only": (
            on(
                enable_earnings_blackout=False,
                enable_index_tier_sizing=True,
            ),
            sig27,
        ),
        "earn+tier": (
            on(
                enable_earnings_blackout=True,
                earnings_use_event_blackout=False,
                enable_index_tier_sizing=True,
            ),
            sig27,
        ),
        "v2.7_default": (preset_v27(), sig27),
        # milder tier: only boost hs300, don't cut other as hard
        "tier_mild": (
            on(
                enable_earnings_blackout=False,
                enable_index_tier_sizing=True,
                hs300_size_mult=1.15,
                zz500_size_mult=1.0,
                other_size_mult=0.95,
                hs300_max_weight=0.28,
                zz500_max_weight=0.25,
                other_max_weight=0.22,
            ),
            sig27,
        ),
    }
    rows = []
    for name, (p, panel) in variants.items():
        port = run_portfolio(panel, bars, names=names, params=p, market=hs)
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
            y2025=y.get(2025),
        )
        rows.append(row)
        print(
            f"{name:14} ret={row['ret']:+.3f} sh={row['sharpe']:.3f} "
            f"dd={row['dd']:.3f} cash={row['cash']:.3f} buys={n_buys} "
            f"20={row['y2020']:+.3f} 25={row['y2025']:+.3f}",
            flush=True,
        )
    out = Path("experiments/ablations/ablation_v27.csv")
    pd.DataFrame(rows).to_csv(out, index=False)
    print(f"wrote {out}", flush=True)
    print("best_ret", max(rows, key=lambda r: r["ret"])["variant"], flush=True)
    print("best_sh", max(rows, key=lambda r: r["sharpe"])["variant"], flush=True)


if __name__ == "__main__":
    main()
