"""mock_alpha strategy adapters — stub for acceptance testing.

Reads from fixture files instead of making real CLI calls.
Implements the same adapter interface as production strategies.
"""
from __future__ import annotations

import json
from pathlib import Path


def _fixtures_dir(cfg) -> Path:
    """Return the fixtures directory for this mock strategy."""
    from webui.app.core.config_loader import get_strategy_paths
    paths = get_strategy_paths(cfg)
    # strat_root points to webui/tests/mock_strategy/fixtures
    return paths["strat_root"]


def parse_positions(cfg) -> dict:
    """Parse trades.csv into standard Positions schema (fixture-based)."""
    import csv
    fixtures = _fixtures_dir(cfg)
    trades_csv = fixtures / "trades.csv"

    if not trades_csv.exists():
        return {"positions": [], "note": "trades.csv 不存在", "t_full": None}

    with open(trades_csv, encoding="utf-8") as f:
        reader = csv.DictReader(f)
        rows = list(reader)

    if not rows:
        return {"positions": [], "note": "当前无持仓", "t_full": None}

    # Group by code, take last row per code, filter position_shares > 0
    by_code: dict[str, dict] = {}
    for row in rows:
        by_code[row["code"]] = row

    held = {code: row for code, row in by_code.items()
            if int(row.get("position_shares", 0)) > 0}

    if not held:
        return {"positions": [], "note": "当前无持仓", "t_full": None}

    # Mock current prices (fixture data, no catalog query)
    mock_prices = {
        "600519": 1720.50,
        "000858": 158.20,
        "002594": 258.30,
    }

    positions = []
    for code, row in held.items():
        avg_cost = float(row["avg_cost"])
        cur_price = mock_prices.get(code, avg_cost)
        pnl_pct = (cur_price / avg_cost - 1) * 100 if avg_cost > 0 else 0
        tp_line = avg_cost * (1 + cfg.take_profit)
        sl_line = avg_cost * (1 - cfg.stop_loss)
        positions.append({
            "code": code,
            "name": row["name"],
            "shares": int(row["position_shares"]),
            "avg_cost": round(avg_cost, 3),
            "current_price": round(cur_price, 3),
            "pnl_pct": round(pnl_pct, 2),
            "hold_days": int(row["hold_days"]),
            "entry_date": row["date"],
            "reason": row["reason"],
            "tp_line": round(tp_line, 3),
            "sl_line": round(sl_line, 3),
            "to_tp_pct": round((tp_line / cur_price - 1) * 100, 2) if cur_price > 0 else 0,
            "to_sl_pct": round((sl_line / cur_price - 1) * 100, 2) if cur_price > 0 else 0,
            "mfe": None,
            "mfe_pct": None,
        })

    return {"positions": positions, "note": "模型持仓（mock fixture）", "t_full": "2026-07-24"}


def parse_monitor(cfg) -> dict:
    """Parse monitor status from fixture files."""
    from webui.app.core import checkpoint as cp
    from webui.app.core.t_full import _md5

    golden = cp.get_golden_status(cfg.strategy_id)

    # Golden meta from fixture
    fixtures = _fixtures_dir(cfg)
    golden_meta = {}
    golden_meta_path = fixtures / "golden_meta.json"
    if golden_meta_path.exists():
        try:
            golden_meta = json.loads(golden_meta_path.read_text())
        except Exception:
            pass

    # Factor health — no reports in mock
    fh_last = None

    # Latest daily report from fixture output
    latest_daily = None
    output_dir = fixtures / "output"
    if output_dir.exists():
        dirs = sorted([d for d in output_dir.iterdir() if d.is_dir()], reverse=True)
        if dirs:
            result_file = dirs[0] / "result.json"
            if result_file.exists():
                try:
                    data = json.loads(result_file.read_text(encoding="utf-8"))
                    latest_daily = {
                        "date": dirs[0].name,
                        "asof": data.get("asof", ""),
                        "exec_date": data.get("exec_date", ""),
                        "n_buy": len(data.get("buy", [])),
                        "n_reduce": len(data.get("reduce", [])),
                    }
                except Exception:
                    pass

    # Panel hash — mock panel doesn't exist
    panel_path = fixtures / cfg.panel_path
    panel_hash = _md5(panel_path)
    golden_panel_hash = golden_meta.get("panel_md5", "")[:8]
    panel_ok = (panel_hash == golden_panel_hash) if golden_panel_hash else None

    return {
        "golden": {
            "status": golden["status"],
            "last_check": golden.get("last_check"),
            "summary": golden.get("summary"),
        },
        "golden_meta": {
            "version": golden_meta.get("version", ""),
            "total_return": golden_meta.get("headline", {}).get("total_return", 0),
            "sharpe": golden_meta.get("headline", {}).get("sharpe", 0),
            "max_drawdown": golden_meta.get("headline", {}).get("max_drawdown", 0),
            "n_buys": golden_meta.get("headline", {}).get("n_buys", 0),
            "n_sells": golden_meta.get("headline", {}).get("n_sells", 0),
            "panel_file": golden_meta.get("panel_file", ""),
            "panel_md5": golden_panel_hash,
        },
        "panel_hash_current": panel_hash,
        "panel_ok": panel_ok,
        "factor_health": fh_last,
        "latest_daily": latest_daily,
    }


def parse_history(cfg) -> dict:
    """Parse history list from fixture output dir."""
    fixtures = _fixtures_dir(cfg)
    output_dir = fixtures / "output"
    reports = []
    if output_dir.exists():
        for d in sorted(output_dir.iterdir(), reverse=True):
            if not d.is_dir():
                continue
            result_file = d / "result.json"
            if not result_file.exists():
                continue
            try:
                data = json.loads(result_file.read_text(encoding="utf-8"))
                reports.append({
                    "date": d.name,
                    "asof": data.get("asof", ""),
                    "exec_date": data.get("exec_date", ""),
                    "n_buy": len(data.get("buy", [])),
                    "n_reduce": len(data.get("reduce", [])),
                    "mkt_regime": data.get("meta", {}).get("mkt_regime", ""),
                })
            except Exception:
                reports.append({
                    "date": d.name, "asof": "", "exec_date": "",
                    "n_buy": 0, "n_reduce": 0, "mkt_regime": "",
                })
    return {"reports": reports}


def parse_history_detail(cfg, date_str: str) -> dict:
    """Parse a specific historical report from fixtures."""
    fixtures = _fixtures_dir(cfg)
    result_file = fixtures / "output" / date_str / "result.json"
    if not result_file.exists():
        return {"error": f"不存在: {date_str}", "result": None}

    data = json.loads(result_file.read_text(encoding="utf-8"))
    report_file = fixtures / "output" / date_str / "report.md"
    report_md = report_file.read_text(encoding="utf-8") if report_file.exists() else ""

    return {"date": date_str, "result": data, "report_md": report_md}


def parse_daily_latest(cfg) -> dict:
    """Read the latest result.json from fixture output directory."""
    fixtures = _fixtures_dir(cfg)
    output_dir = fixtures / "output"
    if not output_dir.exists():
        return {"error": "无历史日荐报告", "result": None}

    dirs = sorted([d for d in output_dir.iterdir() if d.is_dir()], reverse=True)
    if not dirs:
        return {"error": "无历史日荐报告", "result": None}

    result_file = dirs[0] / "result.json"
    if not result_file.exists():
        return {"error": f"result.json 不存在: {dirs[0].name}", "result": None}

    data = json.loads(result_file.read_text(encoding="utf-8"))
    report_file = dirs[0] / "report.md"
    report_md = report_file.read_text(encoding="utf-8") if report_file.exists() else ""

    return {"date": dirs[0].name, "result": data, "report_md": report_md, "t_full": "2026-07-24"}
