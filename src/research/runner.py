"""One engine invocation and one result format for strategy research."""

from pathlib import Path
from ashare_alpha.backtest import (
    BacktestConfig,
    CapacityConfig,
    BacktestEngine,
    BacktestError,
    CostConfig,
    SlippageConfig,
)
from .artifacts import ROOT, write_json


def execution_config(start, end, overrides=None):
    values = dict(overrides or {})
    if isinstance(values.get("capacity"), dict):
        values["capacity"] = CapacityConfig(**values["capacity"])
    if isinstance(values.get("costs"), dict):
        values["costs"] = CostConfig(**values["costs"])
    if isinstance(values.get("slippage"), dict):
        values["slippage"] = SlippageConfig(**values["slippage"])
    return BacktestConfig(**{**values, "start": start, "end": end})


def run(
    strategy_id,
    strategy_class,
    data,
    params,
    config,
    *,
    output=None,
    save_inputs=False,
    publish=True,
):
    if output is not None and Path(output).exists():
        raise FileExistsError(output)
    try:
        result = BacktestEngine(data).run(
            strategy_class, strategy_params=params, config=config
        )
    except BacktestError as exc:
        failed_path = (
            Path(output)
            if output is not None
            else ROOT
            / strategy_id
            / "runs"
            / ("failed-" + exc.result.metadata["run_id"])
        )
        if not failed_path.exists():
            exc.result.save(failed_path, inputs=data if save_inputs else None)
        print(f"[strategy] failed result {failed_path}", flush=True)
        raise
    path = (
        Path(output)
        if output is not None
        else ROOT / strategy_id / "runs" / result.metadata["run_id"]
    )
    if output is not None or not path.exists():
        result.save(path, inputs=data if save_inputs else None)
    if publish:
        write_json(
            ROOT / strategy_id / "run.json",
            {
                "path": str(path),
                "run_id": result.metadata["run_id"],
                "data_fingerprint": data.fingerprint,
                "start": config.start,
                "end": config.end,
            },
        )
    return result, path
