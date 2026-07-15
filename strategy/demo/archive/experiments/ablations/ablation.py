"""Module ablation: turn off layers and compare vs full v1.3."""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[3]
_REPO = Path(__file__).resolve().parents[5]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))
sys.path.insert(0, str(ROOT))

from backtest.metrics import compute_metrics
from engine.portfolio import run_portfolio
from cli.run_backtest import load_bars, load_hs300
from config.params import DEFAULT_PARAMS, REPORT_DIR, SAMPLE_START, safe_replace
from features.build_panel import load_signals


def main() -> None:
    panel = ROOT / "cache" / "signals_2020-01-01_v1.0.parquet"
    if not panel.exists():
        raise SystemExit(f"missing panel {panel}; rebuild first")
    sig = load_signals(panel)
    sig["date"] = pd.to_datetime(sig["date"])
    sig = sig[sig["date"] >= SAMPLE_START]
    names = sig.drop_duplicates("code").set_index("code")["name"].fillna("").astype(str).to_dict()
    bars = load_bars(SAMPLE_START)
    hs = load_hs300(SAMPLE_START)

    variants = {
        "full_v1.3": DEFAULT_PARAMS,
        "no_open_c": safe_replace(DEFAULT_PARAMS, enable_open_c=False, satellite_n=0, version="ablate_no_c"),
        "no_hold_extend": safe_replace(DEFAULT_PARAMS, enable_hold_extend=False, version="ablate_no_hold"),
        "no_sector_shrink": safe_replace(DEFAULT_PARAMS, enable_sector_shrink_exit=False, version="ablate_no_sec"),
        "avoid_ignore": safe_replace(DEFAULT_PARAMS, avoid_mode="ignore", version="ablate_avoid_ignore"),
    }
    rows = []
    for name, p in variants.items():
        port = run_portfolio(sig, bars, names=names, params=p)
        m = compute_metrics(port["daily"], port["trades"], hs, p.initial_capital)
        rows.append(
            {
                "variant": name,
                "total_return": m["capital"]["total_return"],
                "ann_return": m["returns"]["ann_return"],
                "max_dd": m["risk"]["max_drawdown"],
                "sharpe": m["risk"]["sharpe"],
                "fees": m["capital"]["total_fees"],
                "turnover": m["trading"]["turnover_ann"],
                "buys": m["trading"]["n_buys"],
            }
        )
    out = pd.DataFrame(rows)
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    path = ROOT / "experiments" / "ablations" / "ablation_v13.csv"
    out.to_csv(path, index=False)
    print(out.to_string(index=False))
    print(f"Wrote {path}")


if __name__ == "__main__":
    main()
