"""Checkpoint manager — stage-level resume for update jobs.

Strategy-agnostic: keyed by strategy_id.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from ashare_alpha.paths import JOBS_ROOT
from ashare_alpha.storage import atomic_json

JOBS_BASE = JOBS_ROOT


def _strategy_jobs_dir(strategy_id: str, *, create=False) -> Path:
    d = JOBS_BASE / strategy_id
    if create:
        d.mkdir(parents=True, exist_ok=True)
    return d


def _checkpoint_file(strategy_id: str) -> Path:
    return _strategy_jobs_dir(strategy_id) / "update_checkpoint.json"


def _last_success_file(strategy_id: str) -> Path:
    return _strategy_jobs_dir(strategy_id) / "last_successful_update.json"


def _golden_status_file(strategy_id: str) -> Path:
    return _strategy_jobs_dir(strategy_id) / "golden_status.json"


# ════════════════════════════════════════════════════════════════
#  Checkpoint (update resume)
# ════════════════════════════════════════════════════════════════


def read_checkpoint(strategy_id: str) -> dict | None:
    f = _checkpoint_file(strategy_id)
    if f.exists():
        try:
            return json.loads(f.read_text())
        except (ValueError, OSError) as exc:
            return {
                "corrupt": True,
                "error": str(exc),
                "steps": [{"name": "checkpoint", "status": "interrupted"}],
            }
    return None


def write_checkpoint(
    strategy_id: str,
    job_id: str,
    steps_status: dict[str, str],
    t_full_before: str | None,
    target_date: str | None = None,
):
    data = {
        "job_id": job_id,
        "steps": [
            {
                "name": name,
                "status": steps_status.get(name, "pending"),
                "completed_at": (
                    datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    if steps_status.get(name) == "completed"
                    else None
                ),
            }
            for name in sorted(steps_status.keys())
        ],
        "target_date": target_date,
        "t_full_before": t_full_before,
        "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
    atomic_json(_checkpoint_file(strategy_id), data)


def clear_checkpoint(strategy_id: str):
    f = _checkpoint_file(strategy_id)
    if f.exists():
        f.unlink()


def has_incomplete_update(strategy_id: str) -> bool:
    cp = read_checkpoint(strategy_id)
    if not cp:
        return False
    for step in cp.get("steps", []):
        if step["status"] != "completed":
            return True
    return False


# ════════════════════════════════════════════════════════════════
#  Last successful update (T_full advancement)
# ════════════════════════════════════════════════════════════════


def write_last_success(strategy_id: str, t_full: str | None):
    data = {
        "t_full": t_full,
        "completed_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
    atomic_json(_last_success_file(strategy_id), data)


# ════════════════════════════════════════════════════════════════
#  Golden status cache
# ════════════════════════════════════════════════════════════════


def get_golden_status(strategy_id: str) -> dict:
    f = _golden_status_file(strategy_id)
    if f.exists():
        try:
            return json.loads(f.read_text())
        except Exception:
            pass
    return {"status": "unknown", "last_check": None, "summary": "未检查"}


def save_golden_status(strategy_id: str, status: str, summary: str, output: str = ""):
    data = {
        "status": status,
        "last_check": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "summary": summary,
        "output": output[-500:] if output else "",
    }
    atomic_json(_golden_status_file(strategy_id), data)


# ════════════════════════════════════════════════════════════════
#  Job persistence (jsonl)
# ════════════════════════════════════════════════════════════════


def job_file(strategy_id: str, job_id: str) -> Path:
    return _strategy_jobs_dir(strategy_id) / f"{job_id}.jsonl"


def persist_job(strategy_id: str, job: dict):
    if not job.get("job_id"):
        return
    f = job_file(strategy_id, job["job_id"])
    f.parent.mkdir(parents=True, exist_ok=True)
    records = [{"t": _now_ts(), "line": line} for line in job.get("log", [])]
    records.append(
        {
            "t": _now_ts(),
            "final": True,
            "status": job["status"],
            "stage": job.get("stage", ""),
            "strategy_id": strategy_id,
            "target_date": job.get("target_date"),
            "job_type": job.get("job_type", ""),
            "start_time": job.get("start_time"),
            "end_time": job.get("end_time"),
            "error": job.get("error"),
        }
    )
    from data.ashare_data.storage import atomic_bytes

    atomic_bytes(
        "".join(
            json.dumps(record, ensure_ascii=False) + "\n" for record in records
        ).encode(),
        f,
    )


def load_job(strategy_id: str, job_id: str) -> dict | None:
    f = job_file(strategy_id, job_id)
    if not f.exists():
        return None
    log = []
    final = None
    for line in f.read_text(encoding="utf-8").strip().split("\n"):
        try:
            obj = json.loads(line)
            if obj.get("final"):
                final = obj
            else:
                log.append(obj["line"])
        except Exception:
            continue
    if final:
        return {
            "job_id": job_id,
            "status": "interrupted"
            if final["status"] == "running"
            else final["status"],
            "stage": final.get("stage", ""),
            "strategy_id": strategy_id,
            "target_date": final.get("target_date"),
            "job_type": final.get("job_type", ""),
            "start_time": final.get("start_time"),
            "end_time": final.get("end_time"),
            "error": final.get("error"),
            "log": log[-50:],
        }
    return {"job_id": job_id, "status": "interrupted", "log": log[-50:]}


def find_latest_job(strategy_id: str) -> str | None:
    d = _strategy_jobs_dir(strategy_id)
    files = sorted(d.glob("*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True)
    return files[0].stem if files else None


def _now_ts() -> float:
    import time

    return time.time()
