"""Ablation for v2.5 SOTA secondary factors."""
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
preset_v24 = _leg.preset_v24
preset_v25 = _leg.preset_v25
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
    # Prefer prebuilt SOTA cache when present
    cache = Path("cache/signals_2020-01-01_v1.0.parquet")
    panel = cache if cache.exists() else Path("cache/signals_2020-01-01_v1.0.parquet")
    print(f"loading {panel}")
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
    base = preset_v24()

    if "turn_cv_20" not in sig.columns:
        from features.sota_factors import enrich_sota_factors

        print("enriching sota factors…", flush=True)
        sig = enrich_sota_factors(sig, 20)
        print("enrich done", flush=True)
    else:
        print("sota columns present", flush=True)

    def on(**kw):
        return safe_replace(base, enable_sota_factors=True, version="abl", **kw)

    variants = {
        "v2.4": base,
        "anti_chase_only": on(
            enable_anti_chase_filter=True,
            enable_anti_chase_score=True,
            enable_turn_stab_score=False,
            enable_low_ivol_score=False,
            enable_path_eff_score=False,
        ),
        "turn_stab_score": on(
            enable_turn_stab_score=True,
            enable_low_ivol_score=False,
            enable_path_eff_score=False,
            enable_anti_chase_score=False,
            enable_anti_chase_filter=False,
        ),
        "low_ivol_score": on(
            enable_turn_stab_score=False,
            enable_low_ivol_score=True,
            enable_path_eff_score=False,
            enable_anti_chase_score=False,
            enable_anti_chase_filter=False,
        ),
        "path_eff_score": on(
            enable_turn_stab_score=False,
            enable_low_ivol_score=False,
            enable_path_eff_score=True,
            enable_anti_chase_score=False,
            enable_anti_chase_filter=False,
        ),
        "scores_all": on(
            enable_turn_stab_score=True,
            enable_low_ivol_score=True,
            enable_path_eff_score=True,
            enable_anti_chase_score=True,
            enable_anti_chase_filter=False,
        ),
        "scores+filter": on(
            enable_turn_stab_score=True,
            enable_low_ivol_score=True,
            enable_path_eff_score=True,
            enable_anti_chase_score=True,
            enable_anti_chase_filter=True,
        ),
        "v2.5_default": preset_v25(),
        "hard_filters": on(
            enable_turn_stab_score=False,
            enable_low_ivol_score=False,
            enable_path_eff_score=False,
            enable_anti_chase_score=False,
            enable_anti_chase_filter=True,
            enable_low_ivol_filter=True,
            enable_turn_stab_filter=True,
        ),
    }
    rows = []
    for name, p in variants.items():
        port = run_portfolio(sig, bars, names=names, params=p, market=hs)
        m = compute_metrics(port["daily"], port["trades"], hs, p.initial_capital)
        y = year_rets(port["daily"])
        n_buys = int((port["trades"]["side"] == "BUY").sum()) if len(port["trades"]) else 0
        row = {
            "variant": name,
            "ret": m["capital"]["total_return"],
            "sharpe": m["risk"]["sharpe"],
            "dd": m["risk"]["max_drawdown"],
            "win": m["trading"]["win_rate"],
            "pay": m["trading"]["payoff_ratio"],
            "cash": m["trading"]["avg_cash_ratio"],
            "n_buys": n_buys,
            "y2020": y.get(2020),
            "y2025": y.get(2025),
        }
        rows.append(row)
        print(
            f"{name:16} ret={row['ret']:+.3f} sh={row['sharpe']:.3f} "
            f"dd={row['dd']:.3f} pay={row['pay']:.2f} buys={n_buys} "
            f"20={row['y2020']:+.3f} 25={row['y2025']:+.3f}"
        )
    out = Path("experiments/ablations/ablation_v25.csv")
    pd.DataFrame(rows).to_csv(out, index=False)
    print(f"wrote {out}")
    print("best_ret", max(rows, key=lambda r: r["ret"])["variant"])
    print("best_sh", max(rows, key=lambda r: r["sharpe"])["variant"])


if __name__ == "__main__":
    main()
