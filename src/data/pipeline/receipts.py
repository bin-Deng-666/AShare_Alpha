"""Durable task receipts and change fingerprints for incremental builds."""

from __future__ import annotations
import hashlib
from pathlib import Path

from ..storage import atomic_json, read_json


def fingerprint(paths) -> str:
    digest = hashlib.sha256()
    for path in sorted(set(map(Path, paths))):
        try:
            stat = path.stat()
            value = (str(path), stat.st_size, stat.st_mtime_ns)
        except FileNotFoundError:
            value = (str(path), "missing")
        digest.update(repr(value).encode())
    return digest.hexdigest()


def task_state_path(root: Path, name: str, codes=None) -> Path:
    """A subset run must not replace the full-market successful receipt."""
    if codes is None:
        return root / "tasks" / f"{name}.json"
    scope = ",".join(sorted({str(code).zfill(6) for code in codes}))
    token = hashlib.sha256(scope.encode()).hexdigest()[:16]
    return root / "tasks" / "subsets" / token / f"{name}.json"


class BuildReceipts:
    """Only successful work is cached; changed inputs invalidate its receipt."""

    def __init__(self, path: Path):
        self.path = path
        self.entries = read_json(path, {})

    def current(self, key: str, signature: str, outputs) -> bool:
        outputs = list(outputs)
        old = self.entries.get(key, {})
        return (
            old.get("input") == signature
            and bool(outputs)
            and all(p.exists() for p in outputs)
            and old.get("output") == fingerprint(outputs)
        )

    def record(self, key: str, signature: str, outputs, *, flush=True):
        self.entries[key] = {"input": signature, "output": fingerprint(outputs)}
        if flush:
            self.flush()

    def flush(self):
        atomic_json(self.entries, self.path)
