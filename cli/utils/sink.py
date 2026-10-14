"""Publish complete daily plans; CSV exports are explicit and optional."""

from pathlib import Path
import json
from functools import partial
from typing import Any

import pandas as pd

from ashare_alpha.plans import publish_bundle
from .sizing import format_orders_markdown


def _md_table(rows):
    if not rows:
        return "_（无）_"
    columns = [
        key
        for key in ("code", "name", "shares", "avg_cost", "current_price")
        if any(key in row for row in rows)
    ]
    lines = [
        "| " + " | ".join(columns) + " |",
        "| " + " | ".join("---" for _ in columns) + " |",
    ]
    for row in rows:
        cells = []
        for key in columns:
            value = row.get(key)
            if value is None:
                cell = ""
            elif isinstance(value, float):
                cell = f"{value:.2f}"
            else:
                cell = str(value).replace("|", "/").replace("\n", " ")
            cells.append(cell)
        lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines)


def build_report_markdown(result: dict[str, Any]) -> str:
    execution = result.get("exec_date") or "待日历确认"
    return "\n".join(
        [
            f"# 日计划 · {result['strategy']} · 执行日 {execution}",
            "",
            f"> 信号日 **{result['asof']}** → 下一交易日 **{execution}** 开盘执行",
            "",
            "\n".join(f"- {warning}" for warning in result.get("warnings", [])),
            "",
            format_orders_markdown(result.get("orders")),
            "",
            "## 模型持仓",
            "",
            _md_table(result.get("positions", [])),
        ]
    )


def write_run_bundle(
    out_dir: Path, result: dict[str, Any], *, csv=False
) -> dict[str, Path]:
    return publish_bundle(
        out_dir, result, partial(_write_bundle, csv=csv), variant="csv" if csv else None
    )


def _write_bundle(
    out_dir: Path, result: dict[str, Any], *, csv=False
) -> dict[str, Path]:
    result_path = out_dir / "result.json"
    result_path.write_text(
        json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False),
        encoding="utf-8",
    )
    report_path = out_dir / "report.md"
    report_path.write_text(build_report_markdown(result), encoding="utf-8")
    paths = {"dir": out_dir, "result_json": result_path, "report_md": report_path}
    if csv:
        for name, rows, empty_columns in (
            ("buy", result.get("buy", []), ["code", "budget"]),
            ("sell", result.get("reduce", []), ["code", "quantity"]),
            ("positions", result.get("positions", []), ["code", "shares"]),
        ):
            path = out_dir / f"{name}.csv"
            frame = pd.DataFrame(rows) if rows else pd.DataFrame(columns=empty_columns)
            frame.to_csv(path, index=False)
            paths[name + "_csv"] = path
    return paths
