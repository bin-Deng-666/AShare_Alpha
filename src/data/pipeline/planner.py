"""Plan tasks from source freshness, output integrity and dependency changes."""

from __future__ import annotations
from datetime import date
import hashlib
import json
from pathlib import Path

from ..paths import PARQUET_ROOT, STATE_ROOT, CATALOG_PATH
from ..storage import read_json
from .receipts import task_state_path
from .tasks import select_tasks
from .identity import implementation_signature, semantic_config


def files(patterns):
    return sorted(
        {p for pattern in patterns for p in PARQUET_ROOT.glob(pattern) if p.is_file()}
    )


def task_paths(task):
    inputs = files(task.inputs)
    outputs = [CATALOG_PATH] if task.name == "catalog" else files(task.outputs)
    return inputs, outputs


class PlanningContext:
    """A read snapshot reused until a task writes data.

    Never persist stat caches across runs. Invalidate all file observations after
    any executed task, including partial failures and side-output writes.
    """

    def __init__(self):
        self.code_cache = {}
        self.versions = {}
        self.paths = {}
        self.signatures = {}
        self.regular_files = {}

    def version_for(self, task):
        if task.name not in self.versions:
            self.versions[task.name] = implementation_signature(
                task.name, cache=self.code_cache
            )
        return self.versions[task.name]

    def observe(self, path):
        if path not in self.signatures:
            from stat import S_ISREG

            try:
                stat = path.stat()
                self.signatures[path] = repr(
                    (str(path), stat.st_size, stat.st_mtime_ns)
                )
                self.regular_files[path] = S_ISREG(stat.st_mode)
            except FileNotFoundError:
                self.signatures[path] = repr((str(path), "missing"))
                self.regular_files[path] = False
        return self.regular_files[path]

    def files(self, patterns):
        matches = set()
        for pattern in patterns:
            if pattern not in self.paths:
                self.paths[pattern] = tuple(
                    p for p in PARQUET_ROOT.glob(pattern) if self.observe(p)
                )
            matches.update(self.paths[pattern])
        return sorted(matches)

    def fingerprint(self, paths):
        digest = hashlib.sha256()
        for path in sorted(set(map(Path, paths))):
            self.observe(path)
            digest.update(self.signatures[path].encode())
        return digest.hexdigest()

    def invalidate(self):
        self.paths.clear()
        self.signatures.clear()
        self.regular_files.clear()


def build_plan(
    target_asof,
    *,
    names=None,
    force=False,
    config=None,
    codes=None,
    only_task=None,
    context=None,
):
    target = date.fromisoformat(str(target_asof))
    context = context or PlanningContext()
    planned = {}
    actions = []
    for task in select_tasks(names):
        if only_task is not None and task.name != only_task:
            continue
        state = read_json(task_state_path(STATE_ROOT, task.name, codes), {})
        inputs = context.files(task.inputs)
        outputs = (
            [CATALOG_PATH] if task.name == "catalog" else context.files(task.outputs)
        )
        scope = json.dumps(
            {
                "config": semantic_config(task.name, config or {}),
                "codes": sorted(codes or []),
            },
            sort_keys=True,
            default=str,
        )
        signature = context.version_for(task) + scope + context.fingerprint(inputs)
        intact = (
            bool(outputs)
            and all(context.observe(p) for p in outputs)
            and state.get("output") == context.fingerprint(outputs)
        )
        successful = (
            state.get("status") == "success"
            and state.get("input") == signature
            and intact
        )
        checked = (
            date.fromisoformat(state["checked_through"])
            if state.get("checked_through")
            else None
        )
        due = task.network and (
            checked is None or not 0 <= (target - checked).days < task.cadence_days
        )
        backwards = checked is not None and checked > target
        if (
            force
            or (config or {}).get("refresh_from")
            or backwards
            or not successful
            or due
        ):
            action, reason = (
                "refresh",
                "forced / changed input / missing output / source refresh due",
            )
        else:
            action, reason = "skip", "successful receipt and unchanged files"
        if action == "skip" and any(
            planned.get(d, {}).get("action", "skip") != "skip"
            for d in task.dependencies
        ):
            action, reason = (
                "check",
                "recheck input fingerprint after dependencies finish",
            )
        item = {
            "task": task.name,
            "action": action,
            "reason": reason,
            "dependencies": list(task.dependencies),
            "input": signature,
        }
        actions.append(item)
        planned[task.name] = item
    return actions
