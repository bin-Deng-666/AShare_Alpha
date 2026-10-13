"""Lazy predictors use the common strategy catalog; optional models stay unloaded."""

from collections.abc import Callable
from typing import Any
from ashare_alpha.strategies import STRATEGIES

PredictFn = Callable[..., dict[str, Any]]
_REGISTRY: dict[str, PredictFn] = {}


def register(name: str, fn: PredictFn) -> None:
    _REGISTRY[name] = fn


def get_predictor(name: str) -> PredictFn:
    if name in _REGISTRY:
        return _REGISTRY[name]
    if name not in STRATEGIES:
        raise KeyError(
            f"Unknown strategy {name!r}; known: {', '.join(list_registered())}"
        )
    return STRATEGIES[name].load_predictor()


def list_registered() -> list[str]:
    return sorted(set(STRATEGIES) | set(_REGISTRY))
