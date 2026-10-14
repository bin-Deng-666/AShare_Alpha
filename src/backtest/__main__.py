"""Explicit strategy loading and reproducible daily runs."""

import argparse
import importlib
import json
from pathlib import Path
from . import BacktestConfig, BacktestEngine, MarketData, BacktestError
from .results import json_value


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--strategy",
        required=True,
        help="Explicit module:Class, e.g. strategy.demo:BuyAndHoldStrategy",
    )
    parser.add_argument(
        "--params", type=Path, help="JSON strategy parameter dictionary"
    )
    parser.add_argument(
        "--config",
        type=Path,
        help="JSON execution overrides (costs/slippage/capacity nested dictionaries)",
    )
    source = parser.add_mutually_exclusive_group()
    source.add_argument(
        "--catalog",
        type=Path,
        help="Canonical DuckDB catalog (default data/catalog/quant.duckdb)",
    )
    source.add_argument("--snapshot", type=Path, help="Saved MarketData input snapshot")
    parser.add_argument(
        "--features",
        type=Path,
        help="JSON mapping of declared feature names to Parquet paths",
    )
    parser.add_argument(
        "--save-inputs",
        action="store_true",
        help="Include materialized inputs in the result",
    )
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    parser.add_argument("--codes", nargs="+")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("Output already exists")
    module, separator, name = args.strategy.partition(":")
    if not separator:
        parser.error("Use module:Class")
    cls = getattr(importlib.import_module(module), name)
    params = {} if args.params is None else json.loads(args.params.read_text())
    overrides = {} if args.config is None else json.loads(args.config.read_text())
    from .config import CapacityConfig, CostConfig, SlippageConfig

    if "capacity" in overrides:
        overrides["capacity"] = CapacityConfig(**overrides["capacity"])

    if "costs" in overrides:
        overrides["costs"] = CostConfig(**overrides["costs"])
    if "slippage" in overrides:
        overrides["slippage"] = SlippageConfig(**overrides["slippage"])
    cfg = BacktestConfig(**{**overrides, "start": args.start, "end": args.end})
    needs = cls(params=params).requirements()
    if args.snapshot is not None:
        if args.codes or args.features:
            parser.error("Snapshot inputs already fix symbols and features")
        data = MarketData.load_snapshot(args.snapshot)
    else:
        import pandas as pd

        features = (
            {}
            if args.features is None
            else {
                name: pd.read_parquet(path)
                for name, path in json.loads(args.features.read_text()).items()
            }
        )
        from ashare_alpha.paths import DATA_ROOT

        data = MarketData.from_catalog(
            args.catalog or DATA_ROOT / "catalog/quant.duckdb",
            start=args.start,
            end=args.end,
            codes=args.codes,
            warmup_sessions=needs.warmup_sessions,
            features=features,
        )
    try:
        result = BacktestEngine(data).run(cls, strategy_params=params, config=cfg)
    except BacktestError as exc:
        exc.result.save(args.output, inputs=data if args.save_inputs else None)
        print(
            json.dumps(
                {"status": "failed", "error": str(exc), "output": str(args.output)},
                ensure_ascii=False,
            )
        )
        return 1
    result.save(args.output, inputs=data if args.save_inputs else None)
    print(
        json.dumps(
            json_value(
                {
                    "status": result.status,
                    "run_id": result.metadata["run_id"],
                    "capital": result.metrics["capital"],
                    "output": str(args.output),
                }
            ),
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
