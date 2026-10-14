"""Per-security source receipts and durable announcement-to-financial handoff."""

from __future__ import annotations
from datetime import date
import json
import threading

from ..paths import STATE_ROOT, PARQUET_ROOT
from ..storage import read_json, atomic_json
from .receipts import fingerprint
from .identity import source_implementation_signature, semantic_config


def source_version(name, config):
    return source_implementation_signature(name) + json.dumps(
        semantic_config(name, config), sort_keys=True, default=str
    )


def refresh_securities(
    name, function, config, codes, *, workers, cadence_days=1, force=False, inputs=()
):
    from ..incremental_files import _listed_universe

    selected = _listed_universe(codes).code.astype(str).tolist()
    path = STATE_ROOT / "fetch_receipts" / f"{name}.json"
    entries = read_json(path, {})
    target = date.fromisoformat(config["end_date"])
    version = source_version(name, config)
    pending, signatures = [], {}
    for code in selected:
        source_paths = [PARQUET_ROOT / p.format(code=code) for p in inputs]
        signature = version + fingerprint(source_paths)
        signatures[code] = signature
        old = entries.get(code, {})
        output = PARQUET_ROOT / name / f"{code}.parquet"
        checked = (
            date.fromisoformat(old["checked_through"])
            if old.get("checked_through")
            else None
        )
        recent = checked is not None and 0 <= (target - checked).days < cadence_days
        if (
            force
            or config.get("refresh_from")
            or not recent
            or old.get("input") != signature
            or not output.exists()
            or old.get("output") != fingerprint([output])
        ):
            pending.append(code)
    channel = config.get("_ready_queue") if name == "announcements" else None
    batch_size = max(1, int(config.get("source_batch_size", 50)))
    if channel is not None:
        ready = sorted(set(selected) - set(pending))
        if ready:
            channel.put({"codes": ready})
    lock = threading.Lock()
    completed = set()
    notifications = []

    def flush():
        atomic_json(entries, path)
        # Publish only after both canonical data and its success record exist.
        if channel is not None and notifications:
            channel.put({"codes": list(notifications)})
        notifications.clear()

    def done(code):
        output = PARQUET_ROOT / name / f"{code}.parquet"
        if code not in signatures or not output.exists():
            raise RuntimeError(f"{name}: invalid security completion: {code}")
        with lock:
            entries[code] = {
                "checked_through": str(target),
                "input": signatures[code],
                "output": fingerprint([output]),
            }
            completed.add(code)
            notifications.append(code)
            if len(notifications) >= batch_size:
                flush()

    try:
        if pending:
            runtime_config = dict(config)
            runtime_config["_source_checked_through"] = {
                code: (
                    entries.get(code, {}).get("checked_through")
                    if not force and (PARQUET_ROOT / name / f"{code}.parquet").exists()
                    else None
                )
                for code in pending
            }
            function(
                runtime_config, codes=pending, max_workers=workers, on_code_done=done
            )
    finally:
        with lock:
            flush()
        if channel is not None:
            channel.put({"end": True, "failed": sorted(set(pending) - completed)})
    missing = set(pending) - completed
    if missing:
        raise RuntimeError(
            f"{name}: {len(missing)} 只证券未完成；成功证券已记录，可续传。例: {sorted(missing)[:10]}"
        )
    return {"processed": len(pending), "skipped": len(selected) - len(pending)}


def stream_financials(config, codes, *, workers, force=False):
    from ..incremental_files import _listed_universe
    from ..sources.financial_statements import update_financials_incremental
    from ..runtime import Throttle

    selected = set(_listed_universe(codes).code.astype(str))
    channel = config["_ready_queue"]
    processed = skipped = 0
    received = set()
    failures = []
    # Preserve one provider request budget across every received batch.
    runtime = {**config, "_sina_budget": Throttle(config["sina_min_interval_seconds"])}
    while True:
        message = channel.get()
        batch = sorted((set(message.get("codes", [])) & selected) - received)
        if batch:
            received.update(batch)
            try:
                result = refresh_securities(
                    "financials",
                    update_financials_incremental,
                    runtime,
                    batch,
                    workers=workers,
                    cadence_days=7,
                    force=force,
                    inputs=("announcements/{code}.parquet",),
                )
                processed += result["processed"]
                skipped += result["skipped"]
            except Exception as exc:
                failures.append(str(exc))
        if message.get("end"):
            blocked = selected - received
            if blocked or message.get("failed") or message.get("error"):
                failures.append(
                    f"announcement checks incomplete: {len(blocked)} blocked securities"
                )
            break
    if failures:
        raise RuntimeError(
            "Financial handoff incomplete; successful security receipts retained: "
            + "; ".join(failures)[:800]
        )
    return {
        "processed": processed,
        "skipped": skipped,
        "handoff_securities": len(received),
    }
