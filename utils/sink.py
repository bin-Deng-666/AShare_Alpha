"""Write daily-run artifacts under output/<strategy>/<YYYYMMDD>/."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pandas as pd

PICK_COLS = [
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
    "reason",
]


def _records_to_df(rows: list[dict[str, Any]]) -> pd.DataFrame:
    if not rows:
        return pd.DataFrame(columns=PICK_COLS)
    df = pd.DataFrame(rows)
    for c in PICK_COLS:
        if c not in df.columns:
            df[c] = None
    return df[PICK_COLS]


def _md_table(df: pd.DataFrame) -> str:
    if df is None or df.empty:
        return "_（无）_\n"
    use = [c for c in PICK_COLS if c in df.columns]
    show = df[use]
    lines = [
        "| " + " | ".join(use) + " |",
        "| " + " | ".join("---" for _ in use) + " |",
    ]
    for _, row in show.iterrows():
        cells = []
        for c in use:
            v = row[c]
            if v is None or (isinstance(v, float) and v != v):
                cells.append("")
            elif isinstance(v, float):
                cells.append(f"{v:.4g}" if abs(v) < 1 else f"{v:.2f}")
            else:
                cells.append(str(v).replace("|", "/"))
        lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines) + "\n"


def build_report_markdown(result: dict[str, Any]) -> str:
    from utils.sizing import format_orders_markdown

    meta = dict(result.get("meta") or {})
    buy_df = _records_to_df(result.get("buy") or [])
    sat_df = _records_to_df(result.get("satellite") or [])
    red_df = _records_to_df(result.get("reduce") or [])
    buy_body = (
        "_无买入候选（防御性 regime 或无 setup 时属正常）_\n"
        if buy_df.empty
        else _md_table(buy_df)
    )
    orders_md = format_orders_markdown(result.get("orders"))
    return "\n".join(
        [
            f"# 日荐 · {result.get('strategy')} · 执行日 {result.get('exec_date')}",
            "",
            f"> 信号日 **{result.get('asof')}** → 下一交易日 **{result.get('exec_date')}** 开盘执行",
            "",
            orders_md,
            "## 策略细节",
            "",
            f"- regime：`{meta.get('mkt_regime', 'n/a')}` · 宇宙 {meta.get('universe_n', 'n/a')}",
            f"- open_A/B/C：{meta.get('open_A_n', 0)} / {meta.get('open_B_n', 0)} / {meta.get('open_C_n', 0)}",
            f"- reduce / avoid：{meta.get('reduce_n', 0)} / {meta.get('avoid_n', 0)}",
            "",
            "### BUY（候选明细）",
            "",
            buy_body,
            "### SATELLITE",
            "",
            _md_table(sat_df),
            "### REDUCE / AVOID（明细）",
            "",
            _md_table(red_df),
            "",
        ]
    )


def write_run_bundle(out_dir: Path, result: dict[str, Any]) -> dict[str, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    meta = dict(result.get("meta") or {})

    meta_path = out_dir / "meta.json"
    meta_path.write_text(
        json.dumps(
            {
                "strategy": result.get("strategy"),
                "asof": result.get("asof"),
                "exec_date": result.get("exec_date"),
                "exec_hint": result.get("exec_hint", "T+1_open"),
                "meta": meta,
                "attack_dist": result.get("attack_dist") or [],
                "n_buy": len(result.get("buy") or []),
                "n_satellite": len(result.get("satellite") or []),
                "n_reduce": len(result.get("reduce") or []),
                "orders": result.get("orders"),
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    buy_path = out_dir / "buy.csv"
    sat_path = out_dir / "satellite.csv"
    red_path = out_dir / "reduce.csv"
    _records_to_df(result.get("buy") or []).to_csv(buy_path, index=False)
    _records_to_df(result.get("satellite") or []).to_csv(sat_path, index=False)
    _records_to_df(result.get("reduce") or []).to_csv(red_path, index=False)

    report_path = out_dir / "report.md"
    report_path.write_text(build_report_markdown(result), encoding="utf-8")

    result_path = out_dir / "result.json"
    result_path.write_text(
        json.dumps(
            {
                "strategy": result.get("strategy"),
                "asof": result.get("asof"),
                "exec_date": result.get("exec_date"),
                "exec_hint": result.get("exec_hint"),
                "meta": meta,
                "attack_dist": result.get("attack_dist") or [],
                "buy": result.get("buy") or [],
                "satellite": result.get("satellite") or [],
                "reduce": result.get("reduce") or [],
                "orders": result.get("orders"),
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    return {
        "dir": out_dir,
        "meta_json": meta_path,
        "result_json": result_path,
        "buy_csv": buy_path,
        "satellite_csv": sat_path,
        "reduce_csv": red_path,
        "report_md": report_path,
    }
