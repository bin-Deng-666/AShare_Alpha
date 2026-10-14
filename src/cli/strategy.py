"""Research commands with explicit module loading and one shared backtest engine."""

import argparse
import importlib
import json
from pathlib import Path
from ashare_alpha.backtest.results import json_value
from ashare_alpha.strategies import STRATEGIES, class_strategies


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("build", "backtest", "daily"):
        command = sub.add_parser(name)
        command.add_argument("strategy", choices=class_strategies())
        command.add_argument("--start", default="2020-01-01")
        command.add_argument("--end")
        command.add_argument("--params", type=Path)
        command.add_argument("--force", action="store_true")
        if name in ("backtest", "daily"):
            command.add_argument("--snapshot", type=Path)
            command.add_argument("--capital", type=float)
        if name == "daily":
            command.add_argument(
                "--csv", action="store_true", help="export optional plan CSV files"
            )
        if name == "backtest":
            command.add_argument("--config", type=Path)
            command.add_argument("--output", type=Path)
            command.add_argument("--save-inputs", action="store_true")
    args = parser.parse_args(argv)
    module = importlib.import_module(STRATEGIES[args.strategy].runner)
    params = json.loads(args.params.read_text()) if args.params else None
    if args.command == "build":
        data, path = module.build(args.start, args.end, params=params, force=args.force)
        print(
            json.dumps(
                {
                    "status": "success",
                    "inputs": str(path),
                    "fingerprint": data.fingerprint,
                },
                indent=2,
            )
        )
    elif args.command == "backtest":
        overrides = (
            json.loads(args.config.read_text())
            if args.config
            else json.loads(module.DEFAULT_EXECUTION.read_text())
        )
        if args.capital is not None:
            overrides["initial_capital"] = args.capital
        result, path, _ = module.backtest(
            args.start,
            args.end,
            params=params,
            config=overrides,
            snapshot=args.snapshot,
            output=args.output,
            save_inputs=args.save_inputs,
            force=args.force,
        )
        print(
            json.dumps(
                json_value(
                    {
                        "status": result.status,
                        "run_id": result.metadata["run_id"],
                        "output": str(path),
                        "capital": result.metrics["capital"],
                    }
                ),
                ensure_ascii=False,
                indent=2,
            )
        )
    else:
        if params is not None:
            parser.error(
                "Daily plans use the parameters in their prepared snapshot; build it with --params"
            )
        result = module.daily(
            args.end,
            capital=args.capital,
            snapshot=args.snapshot,
            rebuild=args.force,
            start=args.start,
        )
        from ashare_alpha.research.artifacts import ROOT
        from ashare_alpha.cli.utils.sink import write_run_bundle

        date = str(result["exec_date"] or result["asof"]).replace("-", "")
        write_run_bundle(ROOT / args.strategy / "plans" / date, result, csv=args.csv)
        print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
