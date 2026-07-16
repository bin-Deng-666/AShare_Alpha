"""Strategy name → predict() registry."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

PredictFn = Callable[..., dict[str, Any]]

_REGISTRY: dict[str, PredictFn] = {}


def register(name: str, fn: PredictFn) -> None:
    _REGISTRY[name] = fn


def get_predictor(name: str) -> PredictFn:
    if name not in _REGISTRY:
        known = ", ".join(sorted(_REGISTRY)) or "(none)"
        raise KeyError(f"unknown strategy '{name}'. known: {known}")
    return _REGISTRY[name]


def list_registered() -> list[str]:
    return sorted(_REGISTRY)


def _load_builtin() -> None:
    from utils.adapters import crowding_price_volume as _cpv  # noqa: F401
    from utils.adapters import rotating as _rot  # noqa: F401


_load_builtin()
