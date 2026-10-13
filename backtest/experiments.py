"""Isolated strategy comparisons and parameter batches on shared frozen inputs."""

from dataclasses import dataclass
from itertools import product
import pandas as pd
from .engine import BacktestEngine
from .results import BacktestError


@dataclass(frozen=True)
class RunSpec:
    name: str
    strategy_class: type
    params: object = None

    def __post_init__(self):
        if not isinstance(self.name, str) or not self.name.strip():
            raise ValueError("RunSpec needs a nonempty name")
        if not isinstance(self.strategy_class, type):
            raise TypeError("RunSpec requires a strategy class")


@dataclass
class ExperimentResult:
    results: dict
    summary: pd.DataFrame

    def save(self, path, *, inputs=None):
        from pathlib import Path
        from tempfile import TemporaryDirectory
        import os

        path = Path(path)
        if path.exists():
            raise FileExistsError(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with TemporaryDirectory(
            prefix="." + path.name + "-", dir=path.parent
        ) as directory:
            root = Path(directory)
            if inputs is not None:
                if any(
                    result.metadata["data_fingerprint"] != inputs.fingerprint
                    for result in self.results.values()
                ):
                    raise ValueError("Comparison and input fingerprints differ")
                inputs.save_snapshot(root / "inputs")
            for i, result in enumerate(self.results.values()):
                result.save(root / f'{i:04d}-{result.metadata["run_id"]}')
            self.summary.to_csv(root / "comparison.csv", index=False)
            os.rename(root, path)
        return path


def compare(data, config, specs):
    specs = list(specs)
    if not specs or len({s.name for s in specs}) != len(specs):
        raise ValueError("Unique, nonempty run labels required")
    results = {}
    rows = []
    for spec in specs:
        try:
            result = BacktestEngine(data).run(
                spec.strategy_class, strategy_params=spec.params, config=config
            )
        except BacktestError as exc:
            result = exc.result
        results[spec.name] = result
        metrics = result.metrics
        rows.append(
            {
                "name": spec.name,
                "status": result.status,
                "run_id": result.metadata["run_id"],
                "total_return": metrics.get("capital", {}).get("total_return"),
                "annual_return": metrics.get("returns", {}).get("ann_return"),
                "max_drawdown": metrics.get("risk", {}).get("max_drawdown"),
                "sharpe": metrics.get("risk", {}).get("sharpe"),
                "total_fees": metrics.get("capital", {}).get("total_fees"),
                "buy_fills": metrics.get("trading", {}).get("n_buys"),
                "sell_fills": metrics.get("trading", {}).get("n_sells"),
                "error": result.error,
            }
        )
    return ExperimentResult(results, pd.DataFrame(rows))


def parameter_grid(strategy_class, grid, *, params_factory=dict):
    keys = sorted(grid)
    dimensions = [tuple(grid[k]) for k in keys]
    if any(not values for values in dimensions):
        raise ValueError("Empty parameter dimension")
    return [
        RunSpec(
            f"grid_{i:04d}", strategy_class, params_factory(**dict(zip(keys, values)))
        )
        for i, values in enumerate(product(*dimensions))
    ]
