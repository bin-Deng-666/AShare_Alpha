"""Display audited data coverage and the latest closed session for the chosen strategy."""

from __future__ import annotations
import hashlib
import json
from pathlib import Path
import duckdb
import pandas as pd
from webui.app.core.config_loader import get_strategy_paths
from webui.app.core.sessions import closed_session
from webui.app.core import checkpoint as cp


def _md5(path):
    path = Path(path)
    if not path.is_file():
        return "N/A"
    h = hashlib.md5()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()[:8]


def _catalog_ok(path):
    if not Path(path).is_file():
        return False
    try:
        with duckdb.connect(str(path), read_only=True) as connection:
            tables = {row[0] for row in connection.execute("SHOW TABLES").fetchall()}
        return {"stock_daily_raw", "trade_calendar", "security_master"} <= tables
    except (OSError, duckdb.Error):
        return False


def _canonical_status(cfg):
    from data.ashare_data.pipeline.status import read_status

    root = get_strategy_paths(cfg)["catalog"].parent.parent
    return read_status(state_root=root / "state", data_root=root)


def _t_full(cfg):
    paths = get_strategy_paths(cfg)
    if paths["t_full_state"].name == "quality_report.json":
        status = _canonical_status(cfg)
        quality = status.get("quality", {})
        observed = quality.get("bar_max_date")
        latest = status.get("latest_run") or {}
        return (
            str(pd.Timestamp(observed).date())
            if observed
            and quality.get("current_for_latest_run")
            and latest.get("status") == "success"
            else None
        )
    # Static fixture/state contracts can supply an explicit observed date.
    if paths["t_full_state"].is_file():
        state = json.loads(paths["t_full_state"].read_text())
        for field in ("stock_daily_asof", "bars_asof", "asof", "end_date"):
            if state.get(field):
                return str(pd.Timestamp(state[field]).date())
    return None


def _per_source_dates(cfg):
    path = get_strategy_paths(cfg)["catalog"]
    result = {source.id: None for source in cfg.aux_sources}
    if not _catalog_ok(path):
        return result
    with duckdb.connect(str(path), read_only=True) as connection:
        for source in cfg.aux_sources:
            try:
                # Names come from trusted strategy configuration, never request input.
                observed = connection.execute(
                    f"SELECT max(CAST({source.date_column} AS DATE)) FROM {source.table}"
                ).fetchone()[0]
                result[source.id] = str(observed) if observed else None
            except duckdb.Error:
                pass
    return result


def build_status(cfg):
    paths = get_strategy_paths(cfg)
    clock = closed_session(paths["calendar"])
    status = _canonical_status(cfg)
    quality = status.get("quality", {})
    observed = _t_full(cfg)
    latest = clock["session"]
    current = bool(
        latest
        and observed
        and observed >= latest
        and quality.get("current_for_latest_run")
    )
    reasons = []
    if not clock["calendar_covered"]:
        reasons.append("交易日历待更新，尚不能确认最新收盘交易日。")
    if status.get("writer_active"):
        reasons.append("数据正在更新，完成审计后才能生成计划。")
    elif not quality.get("current_for_latest_run"):
        reasons.append("数据需要重新核验。")
    elif latest and (not observed or observed < latest):
        reasons.append("数据尚未更新到最新收盘交易日。")
    if quality.get("status") == "review":
        reasons.extend(quality.get("issues", []))
    golden = cp.get_golden_status(cfg.strategy_id)
    dataset = None
    if paths["panel"].name == "dataset.json" and paths["panel"].is_file():
        dataset = json.loads(paths["panel"].read_text())
    return {
        "strategy_id": cfg.strategy_id,
        "display_name": cfg.display_name,
        "version": cfg.version,
        "today": clock["today"],
        "update_target": clock["cutoff"],
        "before_close": clock["before_close"],
        "t_full": observed,
        "latest_trade_date": latest,
        "calendar_covered": clock["calendar_covered"],
        "data_current": current,
        "can_generate": current and golden["status"] != "fail",
        "quality_status": quality.get("status", "not_audited"),
        "quality_issues": quality.get("issues", []),
        "audit_current": quality.get("current_for_latest_run", False),
        "writer_active": status.get("writer_active", False),
        "messages": reasons,
        "per_source_dates": _per_source_dates(cfg),
        "catalog_ok": _catalog_ok(paths["catalog"]),
        "input_fingerprint": dataset.get("fingerprint") if dataset else None,
        "has_incomplete_update": cp.has_incomplete_update(cfg.strategy_id),
        "golden_status": golden["status"],
    }
