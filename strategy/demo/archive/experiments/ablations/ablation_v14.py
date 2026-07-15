"""Ablate v1.4 exit / refill modules against v1.3-like baseline."""

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
from config.params import DEFAULT_PARAMS, Params, REPORT_DIR, safe_replace
from features.build_panel import load_signals


def _summary(port: dict, hs300: pd.DataFrame, p: Params) -> dict:
    m = compute_metrics(port["daily"], port["trades"], hs300, p.initial_capital)
    return {
        "total_return": m["capital"]["total_return"],
        "ann_return": m["returns"]["ann_return"],
        "max_dd": m["risk"]["max_drawdown"],
        "sharpe": m["risk"]["sharpe"],
        "fees": m["capital"]["total_fees"],
        "turnover": m["trading"]["turnover_ann"],
        "buys": m["trading"]["n_buys"],
        "cash_ratio": m["trading"]["avg_cash_ratio"],
        "vs_hs300": m["returns"]["excess_total"],
        "ir": m["relative"]["information_ratio"],
    }


def main() -> None:
    panel = ROOT / "cache" / "signals_2020-01-01_v1.0.parquet"
    sig = load_signals(panel)
    bars = load_bars("2020-01-01")
    hs300 = load_hs300("2020-01-01")
    names = dict(zip(sig["code"].astype(str), sig.get("name", pd.Series(dtype=str))))

    baseline = safe_replace(
        DEFAULT_PARAMS,
        version="v1.3_like",
        enable_soft_stop=False,
        trail_activate_pct=0.08,
        trail_drawdown_pct=0.06,
        extend_requires_profit=False,
        enable_stale_cut=False,
        enable_stall_take=False,
        enable_cash_refill=False,
    )

    variants: list[tuple[str, Params]] = [
        ("v1.3_like", baseline),
        ("full_v1.4", DEFAULT_PARAMS),
        ("no_soft_stop", safe_replace(DEFAULT_PARAMS, enable_soft_stop=False)),
        ("no_stale_cut", safe_replace(DEFAULT_PARAMS, enable_stale_cut=False)),
        ("no_stall_take", safe_replace(DEFAULT_PARAMS, enable_stall_take=False)),
        ("no_extend_profit", safe_replace(DEFAULT_PARAMS, extend_requires_profit=False)),
        ("no_cash_refill", safe_replace(DEFAULT_PARAMS, enable_cash_refill=False)),
        ("old_trail", safe_replace(DEFAULT_PARAMS, trail_activate_pct=0.08, trail_drawdown_pct=0.06)),
        (
            "exits_only",
            safe_replace(DEFAULT_PARAMS, enable_cash_refill=False),
        ),
        (
            "stale_only",
            safe_replace(
                baseline,
                enable_stale_cut=True,
                extend_requires_profit=True,
                version="stale_only",
            ),
        ),
        (
            "soft_trail",
            safe_replace(
                baseline,
                enable_soft_stop=True,
                trail_activate_pct=0.06,
                trail_drawdown_pct=0.05,
                version="soft_trail",
            ),
        ),
    ]

    rows = []
    for name, p in variants:
        print(f"running {name}...")
        port = run_portfolio(sig, bars, names=names, params=p)
        row = {"variant": name, **_summary(port, hs300, p)}
        rows.append(row)
        print(
            f"  ret={row['total_return']:.2%} sharpe={row['sharpe']:.2f} "
            f"dd={row['max_dd']:.2%} cash={row['cash_ratio']:.1%}"
        )

    out = pd.DataFrame(rows).sort_values("sharpe", ascending=False)
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    path = ROOT / "experiments" / "ablations" / "ablation_v14.csv"
    out.to_csv(path, index=False)
    print("\n=== ranked by Sharpe ===")
    print(out.to_string(index=False))
    print(f"\nwrote {path}")


if __name__ == "__main__":
    main()
