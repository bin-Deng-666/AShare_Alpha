"""Cost/slippage realism sensitivity on v3.3 alpha (archived legacy vs live stack)."""
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
    base = preset_v33()  # legacy flat 15bp, no open slip (archive headline)

    def on(**kw):
        return safe_replace(base, version="abl", **kw)

    variants = {
        "legacy_15bp": base,
        "fees_only": on(
            enable_realistic_costs=True,
            enable_open_slippage=False,
        ),
        "fees+slip_base": on(
            enable_realistic_costs=True,
            enable_open_slippage=True,
        ),
        "fees+slip_mild": on(
            enable_realistic_costs=True,
            enable_open_slippage=True,
            slippage_bps_large=2.0,
            slippage_bps_mid=5.0,
            slippage_bps_small=12.0,
        ),
        "fees+slip_harsh": on(
            enable_realistic_costs=True,
            enable_open_slippage=True,
            slippage_bps_large=5.0,
            slippage_bps_mid=12.0,
            slippage_bps_small=30.0,
        ),
        "legacy_20bp": on(cost_bps=20.0),
        "legacy_25bp": on(cost_bps=25.0),
    }

    rows = []
    for name, p in variants.items():
        port = run_portfolio(sig, bars, names=names, params=p, market=hs)
        m = compute_metrics(port["daily"], port["trades"], hs, p.initial_capital)
        yr = year_rets(port["daily"])
        tr = port["trades"]
        n_buys = int((tr["side"] == "BUY").sum()) if len(tr) else 0
        fees = float(tr["fee"].sum()) if len(tr) and "fee" in tr.columns else 0.0
        slip = float(tr["slip_bps"].mean()) if len(tr) and "slip_bps" in tr.columns else 0.0
        row = dict(
            variant=name,
            ret=m["capital"]["total_return"],
            sharpe=m["risk"]["sharpe"],
            dd=m["risk"]["max_drawdown"],
            win=m["trading"]["win_rate"],
            pay=m["trading"]["payoff_ratio"],
            n_buys=n_buys,
            total_fees=fees,
            avg_slip_bps=slip,
            cash=m["trading"]["avg_cash_ratio"],
            y2020=yr.get(2020),
            y2024=yr.get(2024),
            y2025=yr.get(2025),
        )
        rows.append(row)
        print(
            f"{name:16s} ret={row['ret']:+.1%} sh={row['sharpe']:.2f} dd={row['dd']:.1%} "
            f"fees={fees:,.0f} slip={slip:.1f}bp buys={n_buys} "
            f"y20={row['y2020']:+.1%} y24={row['y2024']:+.1%} y25={row['y2025']:+.1%}",
            flush=True,
        )

    out = ROOT / "experiments" / "ablations" / "ablation_realistic_costs.csv"
    pd.DataFrame(rows).to_csv(out, index=False)
    print(f"wrote {out}", flush=True)


if __name__ == "__main__":
    main()
