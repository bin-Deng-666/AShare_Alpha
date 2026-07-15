"""Simple walk-forward: train windows unused (rule strategy); evaluate rolling OOS years."""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backtest.metrics import compute_metrics
from backtest.portfolio import run_portfolio
from cli.run_backtest import load_bars, load_hs300
from config.params import DEFAULT_PARAMS, REPORT_DIR
from features.build_panel import load_signals
from lib.panel_paths import default_panel_path


def main() -> None:
    panel = default_panel_path()
    if not panel.exists():
        panel = ROOT / "cache" / "signals_2020-01-01_latest.parquet"
    sig = load_signals(panel)
    sig["date"] = pd.to_datetime(sig["date"])
    names = sig.drop_duplicates("code").set_index("code")["name"].fillna("").astype(str).to_dict()
    bars = load_bars("2020-01-01")
    hs = load_hs300("2020-01-01")
    params = DEFAULT_PARAMS

    years = list(range(2020, 2027))
    rows = []
    for y in years:
        start, end = f"{y}-01-01", f"{y}-12-31"
        s = sig[(sig["date"] >= start) & (sig["date"] <= end)]
        if s.empty:
            continue
        port = run_portfolio(s, bars, names=names, params=params)
        if len(port["daily"]) < 5:
            continue
        m = compute_metrics(port["daily"], port["trades"], hs, params.initial_capital)
        rows.append(
            {
                "oos_year": y,
                "total_return": m["capital"]["total_return"],
                "ann_return": m["returns"]["ann_return"],
                "max_dd": m["risk"]["max_drawdown"],
                "sharpe": m["risk"]["sharpe"],
                "vs_hs300": m["capital"]["total_return"] - m["returns"]["bench_total_return"],
                "buys": m["trading"]["n_buys"],
            }
        )
    out = pd.DataFrame(rows)
    path = REPORT_DIR / "walkforward_by_year.csv"
    out.to_csv(path, index=False)
    print(out.to_string(index=False))
    print(f"Wrote {path}")


if __name__ == "__main__":
    main()
