"""Presentation adapters for the public buy-and-hold demo."""

import json
from pathlib import Path
from ashare_alpha.plans import current_plan, report_dates, report_directory
from webui.app.core.config_loader import get_strategy_paths
from webui.app.core.sessions import market_clock


def _json(path):
    path = Path(path)
    return json.loads(path.read_text()) if path.is_file() else None


def parse_positions(cfg):
    paths = get_strategy_paths(cfg)
    plan = current_plan(paths.get("output", paths["ledger"].parent / "plans")) or _json(
        paths["ledger"]
    )
    if plan is None:
        return {"positions": [], "note": "尚未运行模型账户", "t_full": None}
    return {
        "positions": plan.get("positions", []),
        "note": plan.get("note", "模型账户"),
        "t_full": plan.get("asof"),
        "run_id": plan.get("run_id"),
    }


def parse_history(cfg):
    root = get_strategy_paths(cfg)["output"]
    reports = []
    for date in report_dates(root):
        result = _json(report_directory(root, date) / "result.json")
        if result is not None:
            reports.append(
                {
                    "date": date,
                    "asof": result["asof"],
                    "exec_date": result["exec_date"],
                    "n_buy": len(result.get("buy", [])),
                    "n_reduce": len(result.get("reduce", [])),
                    "mkt_regime": result.get("meta", {}).get("mkt_regime", ""),
                }
            )
    return {"reports": reports}


def parse_history_detail(cfg, date_str):
    if len(date_str) != 8 or not date_str.isdigit():
        return {"error": "无效报告日期", "result": None}
    directory = report_directory(get_strategy_paths(cfg)["output"], date_str)
    result = _json(directory / "result.json") if directory else None
    if result is None:
        return {"error": f"不存在: {date_str}", "result": None}
    report = directory / "report.md"
    return {
        "date": date_str,
        "result": result,
        "report_md": report.read_text() if report.exists() else "",
    }


def parse_daily_latest(cfg):
    reports = [
        row
        for row in parse_history(cfg)["reports"]
        if row["asof"] <= market_clock()["cutoff"]
    ]
    if not reports:
        return {"error": "尚未生成当前收盘日期可用的计划", "result": None}
    selected = max(reports, key=lambda row: row["asof"])
    detail = parse_history_detail(cfg, selected["date"])
    detail["t_full"] = detail["result"]["asof"]
    return detail
