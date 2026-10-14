"""The published catalog contains one single-security buy-and-hold example."""

from dataclasses import dataclass
from importlib import import_module


@dataclass(frozen=True)
class StrategySpec:
    id: str
    name: str
    predictor: str
    runner: str | None = None
    web: bool = False
    class_name: str | None = None
    resources: tuple[str, ...] = ()

    def load_predictor(self):
        module, name = self.predictor.split(":")
        return getattr(import_module(module), name)


STRATEGIES = {
    "demo": StrategySpec(
        "demo",
        "Buy & Hold · 单标的持有",
        "ashare_alpha.cli.utils.adapters.demo:predict",
        "strategy.demo.run",
        True,
        "BuyAndHoldStrategy",
        ("presets/execution.json",),
    )
}


def class_strategies():
    return sorted(name for name, spec in STRATEGIES.items() if spec.runner)


def web_strategies():
    return sorted(name for name, spec in STRATEGIES.items() if spec.web)
