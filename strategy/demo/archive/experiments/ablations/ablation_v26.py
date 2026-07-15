"""Ablation for retuned v2.6 (soft industry domain + limit-up + soft earnings)."""
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
preset_v25 = _leg.preset_v25
preset_v26 = _leg.preset_v26
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
    sig26 = load_signals(Path("cache/signals_2020-01-01_v1.0.parquet"))
    sig26["date"] = pd.to_datetime(sig26["date"])
    sig26 = sig26[sig26["date"] >= "2020-01-01"]
    sig25 = load_signals(Path("cache/signals_2020-01-01_v1.0.parquet"))
    sig25["date"] = pd.to_datetime(sig25["date"])
    sig25 = sig25[sig25["date"] >= "2020-01-01"]
    names = (
        sig26.drop_duplicates("code")
        .set_index("code")["name"]
        .fillna("")
        .astype(str)
        .to_dict()
    )
    bars = load_bars("2020-01-01")
    hs = load_hs300("2020-01-01")
    b25 = preset_v25()

    def on(**kw):
        return safe_replace(
            b25,
            version="abl",
            enable_sota_factors=True,
            enable_turn_stab_score=True,
            **kw,
        )

    variants = {
        "v2.5": (b25, sig25),
        "limit_only": (
            on(
                enable_ind_crowd_domain=False,
                enable_earnings_blackout=False,
                enable_limit_up_crowding=True,
            ),
            sig26,
        ),
        "limit+earn_soft": (
            on(
                enable_ind_crowd_domain=False,
                enable_earnings_blackout=True,
                earnings_blackout_take_profit=False,
                enable_limit_up_crowding=True,
            ),
            sig26,
        ),
        "limit+ind_soft": (
            on(
                enable_ind_crowd_domain=True,
                ind_crowd_high_pct=0.90,
                ind_crowd_score_penalty=0.92,
                ind_crowd_hold_cut_days=0,
                ind_crowd_block_extend=True,
                ind_crowd_tighter_trail=False,
                enable_earnings_blackout=False,
                enable_limit_up_crowding=True,
            ),
            sig26,
        ),
        "v2.6_default": (preset_v26(), sig26),
        # sanity: old aggressive domain still bad?
        "ind_aggressive": (
            on(
                enable_ind_crowd_domain=True,
                ind_crowd_high_pct=0.85,
                ind_crowd_score_penalty=0.85,
                ind_crowd_hold_cut_days=4,
                ind_crowd_block_extend=True,
                ind_crowd_tighter_trail=True,
                enable_earnings_blackout=False,
                enable_limit_up_crowding=True,
            ),
            sig26,
        ),
    }
    rows = []
    for name, (p, panel) in variants.items():
        port = run_portfolio(panel, bars, names=names, params=p, market=hs)
        m = compute_metrics(port["daily"], port["trades"], hs, p.initial_capital)
        y = year_rets(port["daily"])
        sells = port["trades"]
        sells = sells[sells["side"] == "SELL"] if len(sells) else sells
        reasons = sells.groupby("reason").size().to_dict() if len(sells) else {}
        n_buys = int((port["trades"]["side"] == "BUY").sum()) if len(port["trades"]) else 0
        row = dict(
            variant=name,
            ret=m["capital"]["total_return"],
            sharpe=m["risk"]["sharpe"],
            dd=m["risk"]["max_drawdown"],
            win=m["trading"]["win_rate"],
            pay=m["trading"]["payoff_ratio"],
            n_buys=n_buys,
            y2020=y.get(2020),
            y2025=y.get(2025),
            earnings_exits=reasons.get("earnings_blackout", 0),
        )
        rows.append(row)
        print(
            f"{name:18} ret={row['ret']:+.3f} sh={row['sharpe']:.3f} "
            f"dd={row['dd']:.3f} pay={row['pay']:.2f} buys={n_buys} "
            f"20={row['y2020']:+.3f} 25={row['y2025']:+.3f}",
            flush=True,
        )
    out = Path("experiments/ablations/ablation_v26.csv")
    pd.DataFrame(rows).to_csv(out, index=False)
    print(f"wrote {out}", flush=True)
    print("best_ret", max(rows, key=lambda r: r["ret"])["variant"], flush=True)
    print("best_sh", max(rows, key=lambda r: r["sharpe"])["variant"], flush=True)


if __name__ == "__main__":
    main()
