"""API routes — all endpoints are strategy-scoped.

Uses config_loader to resolve strategies, adapters to parse outputs,
and the global JobManager for subprocess execution.
"""

from __future__ import annotations

import json

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, StrictBool
from fastapi.responses import JSONResponse, StreamingResponse

from ashare_alpha.web.core.config_loader import (
    get_adapter_dir,
    get_strategy as _get_strategy,
    list_strategies,
)
from ashare_alpha.web.core.schema import StrategyConfig
from ashare_alpha.web.core.t_full import build_status
from ashare_alpha.web.core.job_manager import jm

router = APIRouter()


class PrepareRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    resume: StrictBool = False


class DailyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")


def get_strategy(strategy_id):
    try:
        return _get_strategy(strategy_id)
    except KeyError as exc:
        raise HTTPException(
            status_code=404, detail="策略不存在或尚未接入操作台"
        ) from exc


# ════════════════════════════════════════════════════════════════
#  Adapter loader — dynamically import a strategy's adapters module
# ════════════════════════════════════════════════════════════════

_adapter_cache: dict[str, object] = {}


def _load_adapter(strategy_id: str):
    """Import the adapters.py module for a given strategy."""
    if strategy_id in _adapter_cache:
        return _adapter_cache[strategy_id]
    adapter_dir = get_adapter_dir(strategy_id)
    module_path = adapter_dir / "adapters.py"

    # Use file-based loading to support both app/strategies/ and tests/
    import importlib.util

    mod_name = f"_adapter_{strategy_id}"
    spec = importlib.util.spec_from_file_location(mod_name, module_path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    _adapter_cache[strategy_id] = mod
    return mod


# ════════════════════════════════════════════════════════════════
#  Strategy registry & config endpoints
# ════════════════════════════════════════════════════════════════


@router.get("/api/strategies")
async def get_strategies():
    """List all registered strategies."""
    return {"strategies": list_strategies()}


@router.get("/api/strategies/{strategy_id}/config")
async def get_strategy_config(strategy_id: str):
    """Return strategy configuration for frontend rendering."""
    cfg = get_strategy(strategy_id)
    return _config_to_dict(cfg)


def _config_to_dict(cfg: StrategyConfig) -> dict:
    """Serialize config to a frontend-friendly dict."""
    return {
        "strategy_id": cfg.strategy_id,
        "display_name": cfg.display_name,
        "version": cfg.version,
        "aux_sources": [
            {"id": s.id, "display_name": s.display_name} for s in cfg.aux_sources
        ],
        "market_status_fields": [
            {"key": f.key, "label": f.label} for f in cfg.market_status_fields
        ],
        "plan_columns": {
            "buy": [{"key": c.key, "label": c.label} for c in cfg.plan_columns_buy],
            "sell": [{"key": c.key, "label": c.label} for c in cfg.plan_columns_sell],
        },
        "positions_columns": [
            {"key": c.key, "label": c.label} for c in cfg.positions_columns
        ],
        "update_stages": [
            {
                "id": s.id,
                "label": s.label,
                "source_desc": s.source_desc,
            }
            for s in cfg.update_pipeline
        ],
        "post_update_stages": [
            {
                "id": c.id,
                "label": c.label,
            }
            for c in cfg.post_update_checks
        ],
    }


# ════════════════════════════════════════════════════════════════
#  Data endpoints (strategy-scoped)
# ════════════════════════════════════════════════════════════════


@router.get("/api/strategies/{strategy_id}/status")
async def get_status(strategy_id: str):
    cfg = get_strategy(strategy_id)
    return build_status(cfg)


@router.get("/api/strategies/{strategy_id}/positions")
async def get_positions(strategy_id: str):
    cfg = get_strategy(strategy_id)
    adapter = _load_adapter(strategy_id)
    return adapter.parse_positions(cfg)


@router.get("/api/strategies/{strategy_id}/history")
async def get_history(strategy_id: str):
    cfg = get_strategy(strategy_id)
    adapter = _load_adapter(strategy_id)
    return adapter.parse_history(cfg)


@router.get("/api/strategies/{strategy_id}/daily/latest")
async def get_daily_latest(strategy_id: str):
    cfg = get_strategy(strategy_id)
    adapter = _load_adapter(strategy_id)
    payload = adapter.parse_daily_latest(cfg)
    from ashare_alpha.web.core.sessions import closed_session
    from ashare_alpha.web.core.config_loader import get_strategy_paths

    clock = closed_session(get_strategy_paths(cfg)["calendar"])
    result = payload.get("result")
    signal_date = result.get("asof") if result else None
    payload["current"] = bool(
        signal_date
        and signal_date == clock["session"]
        and build_status(cfg)["data_current"]
    )
    payload["latest_trade_date"] = clock["session"]
    return payload


@router.get("/api/strategies/{strategy_id}/daily/{date_str}")
async def get_daily_by_date(strategy_id: str, date_str: str):
    cfg = get_strategy(strategy_id)
    adapter = _load_adapter(strategy_id)
    return adapter.parse_history_detail(cfg, date_str)


# ════════════════════════════════════════════════════════════════
#  Job endpoints (global singleton, but strategy-scoped actions)
# ════════════════════════════════════════════════════════════════


@router.post("/api/strategies/{strategy_id}/prepare")
async def prepare_next_session(strategy_id: str, body: PrepareRequest | None = None):
    """Update audited inputs and publish a complete selected-strategy plan."""
    cfg = get_strategy(strategy_id)
    body = body or PrepareRequest()
    result = await jm.start_update(cfg, resume=body.resume, generate_plan=True)
    return JSONResponse(result, status_code=409 if result.get("error") else 200)


@router.post("/api/strategies/{strategy_id}/update")
async def start_update(strategy_id: str, body: PrepareRequest | None = None):
    cfg = get_strategy(strategy_id)
    body = body or PrepareRequest()
    result = await jm.start_update(cfg, resume=body.resume)
    return JSONResponse(result, status_code=409 if result.get("error") else 200)


@router.post("/api/strategies/{strategy_id}/daily")
async def start_daily(strategy_id: str, body: DailyRequest | None = None):
    cfg = get_strategy(strategy_id)
    result = await jm.start_daily(cfg)
    status = (
        409
        if result.get("job_id") and result.get("error")
        else 422
        if result.get("error")
        else 200
    )
    return JSONResponse(result, status_code=status)


@router.get("/api/job/stream")
async def job_stream():
    import asyncio

    async def event_generator():
        while True:
            state = jm.get_state()
            yield f"data: {json.dumps(state, ensure_ascii=False)}\n\n"

            if state["status"] in (
                "done",
                "failed",
                "cancelled",
                "interrupted",
                "idle",
            ):
                break
            await asyncio.sleep(1)

    return StreamingResponse(event_generator(), media_type="text/event-stream")


@router.get("/api/job/current")
async def get_current_job():
    state = jm.get_state()
    if state["status"] == "idle":
        # Check for persisted jobs across all strategies
        from ashare_alpha.web.core.checkpoint import find_latest_job, load_job

        for sid in list_strategies():
            latest = find_latest_job(sid)
            if latest:
                loaded = load_job(sid, latest)
                if loaded:
                    return loaded
        return state
    return state


@router.post("/api/job/cancel")
async def cancel_job():
    success = await jm.cancel()
    return {"success": success}
