"""Daily action list / 荐股 CLI."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import duckdb
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from config.params import DEFAULT_PARAMS, REPORT_DIR, SAMPLE_START, Params
from features.build_panel import build_signal_panel, load_signals
from lib.db import ensure_dirs
from lib.panel_paths import default_panel_path

DAILY_DIR = REPORT_DIR / "daily"


def picks_for_date(df: pd.DataFrame, asof: str, params: Params = DEFAULT_PARAMS) -> dict[str, pd.DataFrame]:
    day = df[df["date"].astype(str).str[:10] == asof].copy()
    if day.empty:
        raise ValueError(f"No rows for date {asof}")

    # v1.0 mainline: open_A only (B/C gated by params, default OFF — matches portfolio).
    buy_actions = ["open_A"]
    if params.enable_open_b:
        buy_actions.append("open_B")
    opens = day[day["action"].isin(buy_actions)].sort_values("score", ascending=False)
    if params.enable_open_c:
        sat = (
            day[day["action"] == "open_C"]
            .sort_values("score", ascending=False)
            .head(params.satellite_n)
        )
    else:
        sat = day.iloc[0:0].copy()
    reduce = day[day["action"].isin(["reduce", "avoid"])].sort_values("crowding_score", ascending=False)

    # diversify industries in main picks
    main_rows = []
    seen_ind = set()
    for _, row in opens.iterrows():
        ind = row["industry_code"]
        if ind in seen_ind and len(main_rows) >= params.top_n // 2:
            continue
        main_rows.append(row)
        seen_ind.add(ind)
        if len(main_rows) >= params.top_n:
            break
    main = pd.DataFrame(main_rows)

    mkt = day[["mkt_regime"]].drop_duplicates().iloc[0]["mkt_regime"] if len(day) else "n/a"
    attack_dist = day.groupby("attack_mode").size().sort_values(ascending=False)

    return {
        "meta": pd.DataFrame(
            [
                {
                    "asof": asof,
                    "mkt_regime": mkt,
                    "universe_n": len(day),
                    "open_A_n": int((day["action"] == "open_A").sum()),
                    "open_B_n": int((day["action"] == "open_B").sum()),
                    "open_C_n": int((day["action"] == "open_C").sum()),
                    "reduce_n": int((day["action"] == "reduce").sum()),
                    "avoid_n": int((day["action"] == "avoid").sum()),
                }
            ]
        ),
        "attack_dist": attack_dist.reset_index(name="n"),
        "buy": main,
        "satellite": sat,
        "reduce": reduce.head(20),
    }


def format_reason(row: pd.Series) -> str:
    return (
        f"{row['action']} | {row['pattern_primary']} | "
        f"mkt={row['mkt_regime']} ind={row['ind_regime']} ({row['attack_mode']}) | "
        f"crowd={row['crowding_bucket']} val={row['valuation_bucket']}"
    )


def main() -> None:
    ap = argparse.ArgumentParser(description="Daily recommend / action list")
    ap.add_argument("--date", default=None, help="Signal date YYYY-MM-DD (default: latest in panel)")
    ap.add_argument("--start", default=SAMPLE_START, help="Panel build start (warmup)")
    ap.add_argument("--rebuild", action="store_true", help="Force rebuild signal panel")
    ap.add_argument("--panel", default=None, help="Existing signals parquet")
    args = ap.parse_args()

    ensure_dirs()
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    DAILY_DIR.mkdir(parents=True, exist_ok=True)
    params = DEFAULT_PARAMS

    if args.panel:
        panel = Path(args.panel)
        if not panel.is_absolute():
            panel = ROOT / panel
    else:
        panel = default_panel_path()
        if args.rebuild or not panel.exists():
            print("Building signal panel (base; prefer rebuild_panel_cache for v1.0 enriched)...")
            end = args.date
            out = ROOT / "cache" / f"signals_{args.start}_{end or 'latest'}.parquet"
            panel = build_signal_panel(start=args.start, end=end, params=params, out_path=out)

    df = load_signals(panel)
    df["date"] = pd.to_datetime(df["date"])
    asof = args.date or str(df["date"].max().date())
    print(f"Using asof={asof} panel={panel}")

    out = picks_for_date(df, asof, params)
    meta = out["meta"].iloc[0].to_dict()
    print("\n=== META ===")
    for k, v in meta.items():
        print(f"  {k}: {v}")

    cols = [
        "code", "name", "action", "score", "industry_name", "pattern_primary",
        "attack_mode", "crowding_bucket", "valuation_bucket", "pct_change",
    ]

    print("\n=== BUY (T+1) ===")
    buy = out["buy"]
    if buy.empty:
        print("  (none — regime may be defensive or no setups)")
    else:
        buy = buy.copy()
        buy["reason"] = buy.apply(format_reason, axis=1)
        print(buy[cols + ["reason"]].to_string(index=False))

    print("\n=== SATELLITE open_C (v1.0 default: off) ===")
    sat = out["satellite"]
    if sat.empty:
        print("  (none)")
    else:
        print(sat[cols].to_string(index=False))

    print("\n=== REDUCE / AVOID (top) ===")
    red = out["reduce"]
    if red.empty:
        print("  (none)")
    else:
        print(red[cols].head(15).to_string(index=False))

    stamp = asof.replace("-", "")
    buy_path = DAILY_DIR / f"{stamp}_buy.csv"
    if not buy.empty:
        buy.to_csv(buy_path, index=False)
        print(f"\nWrote {buy_path}")
    meta_path = DAILY_DIR / f"{stamp}_meta.csv"
    out["meta"].to_csv(meta_path, index=False)


if __name__ == "__main__":
    main()
