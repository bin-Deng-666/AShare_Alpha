"""Validate installed strategy resources and runtime contracts without production data."""

import argparse
import json
from importlib import import_module
from pathlib import Path
from .strategies import STRATEGIES, class_strategies


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("strategy", choices=class_strategies())
    args = parser.parse_args(argv)
    module = import_module(STRATEGIES[args.strategy].runner)
    params = module.Params.parse(None)
    from strategy.research.runner import execution_config

    execution_config(
        "2024-01-02", "2024-01-03", json.loads(module.DEFAULT_EXECUTION.read_text())
    )
    package = Path(module.__file__).parent
    for resource in STRATEGIES[args.strategy].resources:
        if not (package / resource).is_file():
            raise ValueError(f"Installed strategy resource is missing: {resource}")
    strategy = getattr(module, STRATEGIES[args.strategy].class_name)(params=params)
    needs = strategy.requirements()
    for resource in needs.code_dependencies:
        if not Path(resource).is_file():
            raise ValueError(f"Missing strategy dependency: {resource}")
    print(
        json.dumps(
            {"strategy": args.strategy, "status": "pass", "scope": "runtime_contract"}
        )
    )
    return 0
