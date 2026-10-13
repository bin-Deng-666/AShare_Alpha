"""Atomic publication, change detection and the data writer lock."""
from __future__ import annotations

from contextlib import contextmanager
import json
import os
from pathlib import Path
import tempfile
from typing import Any


def atomic_bytes(data: bytes, path: Path) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        Path(name).unlink(missing_ok=True)


def atomic_json(data: Any, path: Path) -> None:
    atomic_bytes(json.dumps(data, ensure_ascii=False, indent=2, default=str).encode(), path)


def read_json(path: Path, default: Any = None) -> Any:
    if not path.exists():
        return default
    # A corrupt checkpoint is an error, never a successful empty checkpoint.
    return json.loads(path.read_text(encoding='utf-8'))


def atomic_parquet(frame: Any, path: Path) -> bool:
    """Keep identical files unchanged so downstream fingerprints stay valid."""
    import pandas as pd
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    frame = frame.reset_index(drop=True)
    if path.exists():
        existing = pd.read_parquet(path)
        if frame.equals(existing):
            return False
    fd, name = tempfile.mkstemp(prefix=f'.{path.name}.', suffix='.tmp', dir=path.parent)
    os.close(fd)
    try:
        frame.to_parquet(name, index=False)
        with open(name, 'rb') as stream:
            os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        Path(name).unlink(missing_ok=True)
    return True


@contextmanager
def writer_lock(path: Path):
    """Nonblocking OS lock; released even when the process is killed (macOS/Linux)."""
    import fcntl
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('a+') as stream:
        try:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RuntimeError('另一个数据更新进程正在运行') from exc
        try:
            stream.seek(0)
            stream.truncate()
            stream.write(str(os.getpid()))
            stream.flush()
            yield
        finally:
            fcntl.flock(stream, fcntl.LOCK_UN)
