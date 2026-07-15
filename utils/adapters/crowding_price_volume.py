"""Adapter: crowding_price_volume daily picks."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pandas as pd

from utils.registry import register

STRATEGY_NAME = "crowding_price_volume"
STRATEGY_ROOT = Path(__file__).resolve().parents[2] / "strategies" / STRATEGY_NAME

PICK_FIELDS = [
    "code",
    "name",
    "action",
    "score",
    "industry_name",
    "pattern_primary",
    "attack_mode",
    "crowding_bucket",
    "valuation_bucket",
    "pct_change",
    "close",
    "position_mult",
]


def _ensure_strategy_path() -> None:
    root = str(STRATEGY_ROOT)
    if root not in sys.path:
        sys.path.insert(0, root)


def _row_to_dict(row: pd.Series, reason: str | None = None) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for k in PICK_FIELDS:
        if k not in row.index:
            out[k] = None
            continue
        v = row[k]
        if hasattr(v, "item"):
            try:
                v = v.item()
            except (ValueError, AttributeError):
                pass
        if isinstance(v, float) and v != v:
            v = None
        if k == "code" and v is not None:
            v = str(v).zfill(6)
        out[k] = v
    out["reason"] = reason
    return out


def _df_to_records(df: pd.DataFrame) -> list[dict[str, Any]]:
    if df is None or len(df) == 0:
        return []
    from cli.run_daily import format_reason

    rows = []
    for _, row in df.iterrows():
        reason = (
            row["reason"]
            if "reason" in row.index and pd.notna(row.get("reason"))
            else format_reason(row)
        )
        rows.append(_row_to_dict(row, reason=str(reason)))
    return rows


def predict(
    asof: str | None = None,
    *,
    rebuild: bool = False,
    panel: str | Path | None = None,
) -> dict[str, Any]:
    _ensure_strategy_path()
    from cli.run_daily import format_reason, picks_for_date
    from config.params import DEFAULT_PARAMS, SAMPLE_START
    from features.build_panel import build_signal_panel, load_signals
    from lib.panel_paths import default_panel_path

    params = DEFAULT_PARAMS

    if panel is not None:
        panel_path = Path(panel)
        if not panel_path.is_absolute():
            panel_path = STRATEGY_ROOT / panel_path
    else:
        panel_path = default_panel_path()
        if not panel_path.is_absolute():
            panel_path = STRATEGY_ROOT / panel_path
        # default_panel_path is under strategy cache already absolute via CACHE_DIR
        if rebuild or not panel_path.exists():
            out = STRATEGY_ROOT / "cache" / f"signals_{SAMPLE_START}_{asof or 'latest'}.parquet"
            panel_path = build_signal_panel(
                start=SAMPLE_START, end=asof, params=params, out_path=out
            )

    df = load_signals(panel_path)
    df["date"] = pd.to_datetime(df["date"])
    use_asof = asof or str(df["date"].max().date())
    raw = picks_for_date(df, use_asof, params)
    meta = raw["meta"].iloc[0].to_dict()

    from config.params import DATA_ROOT
    from utils.calendar import next_trade_date

    hs300 = DATA_ROOT / "parquet" / "reference" / "index_bars" / "000300.parquet"
    exec_date = next_trade_date(
        use_asof,
        calendar_dates=df["date"].drop_duplicates(),
        index_bars=hs300 if hs300.exists() else None,
    )

    # Compare panel signal day vs underlying bar coverage (common stale-panel trap).
    bars_asof = None
    warnings: list[str] = []
    try:
        from pathlib import Path as _Path

        sample = ["000001", "600519", "300750"]
        lasts = []
        for code in sample:
            p = DATA_ROOT / "parquet" / "daily_bars" / "adjustment=qfq" / f"{code}.parquet"
            if not p.exists():
                continue
            last = pd.to_datetime(pd.read_parquet(p, columns=["date"])["date"], errors="coerce").max()
            if pd.notna(last):
                lasts.append(pd.Timestamp(last).normalize())
        if lasts:
            bars_asof = str(max(lasts).date())
            if pd.Timestamp(bars_asof) > pd.Timestamp(use_asof):
                warnings.append(
                    f"信号面板停在 {use_asof}，行情已到 {bars_asof}。"
                    f"run.py 只读面板，不会自动用最新行情。"
                    f"请先：python3 daily_update.py  （或策略目录 rebuild_panel_cache）"
                )
    except Exception:
        pass

    buy = raw["buy"].copy()
    if len(buy):
        buy["reason"] = buy.apply(format_reason, axis=1)
    sat = raw["satellite"].copy()
    if len(sat):
        sat["reason"] = sat.apply(format_reason, axis=1)
    red = raw["reduce"].copy()
    if len(red):
        red["reason"] = red.apply(format_reason, axis=1)

    attack_dist = []
    ad = raw.get("attack_dist")
    if ad is not None and len(ad):
        for _, r in ad.iterrows():
            attack_dist.append({"attack_mode": str(r["attack_mode"]), "n": int(r["n"])})

    return {
        "strategy": STRATEGY_NAME,
        "asof": use_asof,
        "exec_date": exec_date,
        "exec_hint": "T+1_open",
        "bars_asof": bars_asof,
        "warnings": warnings,
        "panel": str(panel_path),
        "meta": {k: (int(v) if hasattr(v, "item") else v) for k, v in meta.items()},
        "attack_dist": attack_dist,
        "buy": _df_to_records(buy),
        "satellite": _df_to_records(sat),
        "reduce": _df_to_records(red),
    }


register(STRATEGY_NAME, predict)
