"""Atomic file publication and nonblocking locks shared by application and data."""

from contextlib import contextmanager
import json
import os
from pathlib import Path
import tempfile


def atomic_bytes(path, payload):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix="." + path.name + "-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        Path(name).unlink(missing_ok=True)


def atomic_json(path, value, *, default=None, allow_nan=False):
    payload = json.dumps(
        value, ensure_ascii=False, indent=2, default=default, allow_nan=allow_nan
    )
    atomic_bytes(path, payload.encode("utf-8"))


@contextmanager
def publication_lock(path, *, message="Another publication is in progress"):
    import fcntl

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+") as stream:
        try:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RuntimeError(message) from exc
        try:
            yield stream
        finally:
            fcntl.flock(stream, fcntl.LOCK_UN)
