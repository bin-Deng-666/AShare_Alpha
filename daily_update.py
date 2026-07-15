#!/usr/bin/env python3
"""盘后更新：行情 → research 层 → 策略面板（run.py 前置一站做完）。

默认跑完后可直接：
  python3 run.py crowding_price_volume

本脚本不调用 run.py；只保证信号面板 asof 对齐最近交易日行情。

Usage (from repo root):
  python3 daily_update.py
  python3 daily_update.py --skip-slow
  python3 daily_update.py --data-only       # 只更新库，不重建面板
  python3 daily_update.py --force           # 强制重拉行情
  python3 daily_update.py --force-panel     # 强制重建面板（即使已对齐）
  python3 daily_update.py --workers 8       # 8 线程并行拉取
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

import pandas as pd

_REPO = Path(__file__).resolve().parent
_DATA = _REPO / "data"
_BUILD = _DATA / "scripts" / "build_dataset.py"
_FLOW = _DATA / "scripts" / "ingest_flow_data.py"
_STRAT = _REPO / "strategies" / "crowding_price_volume"
_REBUILD_PANEL = _STRAT / "cli" / "rebuild_panel_cache.py"
_PANEL = _STRAT / "cache" / "signals_2020-01-01_v1.0.parquet"
_HS300 = _DATA / "parquet" / "reference" / "index_bars" / "000300.parquet"


def _run(cmd: list[str], *, cwd: Path, env: dict | None = None) -> int:
    print("→", " ".join(cmd), flush=True)
    return subprocess.call(cmd, cwd=str(cwd), env=env)


def _bars_asof() -> pd.Timestamp | None:
    lasts: list[pd.Timestamp] = []
    for code in ("000001", "600519", "300750"):
        p = _DATA / "parquet" / "daily_bars" / "adjustment=qfq" / f"{code}.parquet"
        if not p.exists():
            continue
        d = pd.to_datetime(pd.read_parquet(p, columns=["date"])["date"], errors="coerce").max()
        if pd.notna(d):
            lasts.append(pd.Timestamp(d).normalize())
    return max(lasts) if lasts else None


def _panel_asof() -> pd.Timestamp | None:
    if not _PANEL.exists():
        return None
    d = pd.to_datetime(pd.read_parquet(_PANEL, columns=["date"])["date"], errors="coerce").max()
    if pd.isna(d):
        return None
    return pd.Timestamp(d).normalize()


def _stock_daily_asof() -> pd.Timestamp | None:
    cat = _DATA / "catalog" / "quant.duckdb"
    if not cat.exists():
        return None
    try:
        import duckdb

        con = duckdb.connect(str(cat), read_only=True)
        try:
            row = con.execute("SELECT max(date) FROM stock_daily").fetchone()
        finally:
            con.close()
    except Exception:
        return None
    if not row or row[0] is None:
        return None
    return pd.Timestamp(row[0]).normalize()


def _next_exec(asof: pd.Timestamp) -> str:
    sys.path.insert(0, str(_REPO))
    from utils.calendar import next_trade_date

    return next_trade_date(
        str(asof.date()),
        index_bars=_HS300 if _HS300.exists() else None,
    )


def rebuild_base_latest() -> int:
    """Rebuild signals_*_latest.parquet from catalog (needed before v1.0 enrichment)."""
    env = os.environ.copy()
    env["PYTHONPATH"] = str(_STRAT)
    code = r"""
from config.params import DEFAULT_PARAMS, SAMPLE_START, CACHE_DIR
from features.build_panel import build_signal_panel
out = CACHE_DIR / f"signals_{SAMPLE_START}_latest.parquet"
print(f"building base panel → {out}")
build_signal_panel(start=SAMPLE_START, end=None, params=DEFAULT_PARAMS, out_path=out)
print(f"done base → {out}")
"""
    return _run([sys.executable, "-c", code], cwd=_STRAT, env=env)


def ensure_panel_for_run(*, force_panel: bool = False) -> int:
    """Rebuild v1.0 panel when it lags bars/catalog; verify asof for run.py."""
    bars = _bars_asof()
    panel = _panel_asof()
    stock = _stock_daily_asof()
    print(
        f"asof check: bars={bars.date() if bars else None}  "
        f"stock_daily={stock.date() if stock else None}  "
        f"panel={panel.date() if panel else None}",
        flush=True,
    )
    if bars is None:
        print("error: no bars asof — cannot prepare run.py", file=sys.stderr)
        return 1
    if stock is not None and stock < bars:
        print(
            f"error: stock_daily ({stock.date()}) lags bars ({bars.date()}). "
            "update-daily research sync should have fixed this.",
            file=sys.stderr,
        )
        return 1

    need = force_panel or panel is None or panel < bars
    if not need:
        print(f"panel already at {panel.date()} — skip rebuild", flush=True)
    else:
        if not _STRAT.exists():
            print(f"missing strategy {_STRAT}", file=sys.stderr)
            return 1
        rc = rebuild_base_latest()
        if rc != 0:
            return rc
        env = os.environ.copy()
        env["PYTHONPATH"] = str(_STRAT)
        rc = _run([sys.executable, str(_REBUILD_PANEL)], cwd=_STRAT, env=env)
        if rc != 0:
            return rc

    panel2 = _panel_asof()
    if panel2 is None or panel2 < bars:
        print(
            f"error: panel asof {panel2.date() if panel2 else None} "
            f"still behind bars {bars.date()}",
            file=sys.stderr,
        )
        return 1

    exec_date = _next_exec(panel2)
    print(
        f"ready for run.py: 信号日 {panel2.date()} → 执行日 {exec_date}\n"
        f"  python3 run.py crowding_price_volume",
        flush=True,
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Update market data + research layer + signal panel (run.py 前置)"
    )
    ap.add_argument(
        "--skip-slow",
        action="store_true",
        help="bars/indicators only; skip corporate/equity/financials/announcements",
    )
    ap.add_argument(
        "--codes",
        nargs="*",
        default=None,
        help="optional subset of stock codes for data update",
    )
    ap.add_argument(
        "--no-flow",
        action="store_true",
        help="skip northbound/margin ingest (default: refresh for v1.0 panel enrichment)",
    )
    ap.add_argument(
        "--data-only",
        action="store_true",
        help="only update data (+flow); skip panel rebuild",
    )
    ap.add_argument(
        "--force",
        action="store_true",
        help="force update-daily even if bars already cover the latest trading day",
    )
    ap.add_argument(
        "--force-panel",
        action="store_true",
        help="force panel rebuild even if panel asof already matches bars",
    )
    ap.add_argument(
        "--panel-only",
        action="store_true",
        help="skip data/flow; only ensure panel is ready for run.py",
    )
    ap.add_argument(
        "--workers",
        type=int,
        default=4,
        help="parallel worker threads for bars/financials/announcements (default: 4)",
    )
    args = ap.parse_args(argv)

    if args.panel_only:
        return ensure_panel_for_run(force_panel=args.force_panel)

    if not _BUILD.exists():
        print(f"missing {_BUILD}", file=sys.stderr)
        return 1

    # 1) market data + research layer (skips bar fetch if current; still syncs catalog)
    cmd = [sys.executable, str(_BUILD), "update-daily", "--workers", str(args.workers)]
    if args.skip_slow:
        cmd.append("--skip-slow")
    if args.force:
        cmd.append("--force")
    if args.codes:
        cmd.append("--codes")
        cmd.extend(args.codes)
    rc = _run(cmd, cwd=_DATA)
    if rc != 0:
        return rc

    # 2) flow features (for v1.0 panel)
    if not args.no_flow:
        if not _FLOW.exists():
            print(f"missing {_FLOW}", file=sys.stderr)
            return 1
        rc = _run([sys.executable, str(_FLOW), "--catalog"], cwd=_DATA)
        if rc != 0:
            return rc

    if args.data_only:
        print("data-only done. (panel not rebuilt — run.py may see stale asof)", flush=True)
        return 0

    # 3) signal panel for run.py
    return ensure_panel_for_run(force_panel=args.force_panel)


if __name__ == "__main__":
    raise SystemExit(main())
