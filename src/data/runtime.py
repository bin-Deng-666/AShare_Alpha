"""runtime: data acquisition and normalization."""

from __future__ import annotations
import json
import logging
import random
import threading
import time
from typing import Callable
from ashare_alpha.data.paths import CONFIG_PATH

UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"


def retry(
    fn: Callable,
    label: str,
    attempts: int = 3,
    base_sleep: float = 1.0,
):
    last_error = None
    for attempt in range(1, attempts + 1):
        try:
            return fn()
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            if attempt < attempts:
                wait = base_sleep * (2 ** (attempt - 1)) + random.uniform(0, 0.4)
                logging.warning("%s failed (%s/%s): %s", label, attempt, attempts, exc)
                time.sleep(wait)
    raise RuntimeError(f"{label} failed after {attempts} attempts: {last_error}")


class Throttle:
    def __init__(self, min_interval: float):
        self.min_interval = min_interval
        self.last_call = None
        self._lock = threading.Lock()

    def wait(self) -> None:
        with self._lock:
            if self.last_call is not None:
                remaining = self.min_interval - (time.monotonic() - self.last_call)
                if remaining > 0:
                    time.sleep(remaining)
            self.last_call = time.monotonic()


def load_config() -> dict:
    return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
