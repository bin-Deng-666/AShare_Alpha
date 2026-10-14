"""Backtest and next-session plan for the single-security hold example."""

import json
from pathlib import Path
import pandas as pd
from backtest import MarketData
from backtest.results import json_value
from strategy.research.runner import run, execution_config
from .build import build, latest_observed, STRATEGY_ID
from .params import Params
from .strategy import BuyAndHoldStrategy

DEFAULT_START = "2020-01-01"
DEFAULT_EXECUTION = Path(__file__).parent / "presets/execution.json"


def backtest(
    start=DEFAULT_START,
    end=None,
    *,
    params=None,
    config=None,
    snapshot=None,
    output=None,
    save_inputs=False,
    force=False,
    publish=True,
):
    if snapshot is None:
        p = Params.parse(params)
        data, input_path = build(start, end, params=p, force=force, publish=publish)
    else:
        input_path = Path(snapshot)
        data = MarketData.load_snapshot(input_path)
        p = Params.parse(
            params if params is not None else data.metadata.get("research_params")
        )
        if p.code not in set(data._master.code):
            raise ValueError("Selected security is not in the frozen input")
    end = end or data.metadata.get("research_end", str(data.calendar[-1].date()))
    overrides = json.loads(DEFAULT_EXECUTION.read_text()) if config is None else config
    cfg = execution_config(start, end, overrides)
    result, path = run(
        STRATEGY_ID,
        BuyAndHoldStrategy,
        data,
        p,
        cfg,
        output=output,
        save_inputs=save_inputs,
        publish=publish,
    )
    return result, path, input_path


def daily(
    asof=None, *, capital=None, snapshot=None, rebuild=False, start=DEFAULT_START
):
    if snapshot is not None:
        inputs = MarketData.load_snapshot(snapshot)
        asof = asof or inputs.metadata.get(
            "research_end", str(inputs.calendar[-1].date())
        )
        start = inputs.metadata.get("research_start", str(inputs.calendar[0].date()))
    else:
        asof = asof or latest_observed()
    config = json.loads(DEFAULT_EXECUTION.read_text())
    if capital is not None:
        config["initial_capital"] = capital
    result, path, input_path = backtest(
        start, asof, config=config, snapshot=snapshot, force=rebuild
    )
    plan = result.diagnostics["plan"]
    buys = []
    for _, order in (
        result.orders.loc[result.orders.status.eq("open_at_end")].iterrows()
        if not result.orders.empty
        else []
    ):
        request = order.request
        if request["side"] != "BUY":
            raise ValueError("Buy-and-hold demo cannot emit sell orders")
        buys.append(
            {
                "code": request["code"],
                "name": request["code"],
                "budget": order.remaining_budget,
                "reason": "buy_and_hold",
                "action": "buy",
                "status": "pending",
                "order_id": order.order_id,
            }
        )
    manifest = json.loads((Path(input_path) / "manifest.json").read_text())
    signal = pd.Timestamp(plan["asof"])
    dates = pd.read_parquet(Path(input_path) / "calendar.parquet").date
    later = dates.loc[dates.gt(signal)]
    execution = (
        str(later.iloc[0].date())
        if len(later)
        else manifest["metadata"].get("next_session")
    )
    quality = manifest["metadata"].get("quality", {})
    warnings = (
        ["数据质量为 review：" + "；".join(quality.get("issues", []))]
        if quality.get("status") == "review"
        else []
    )
    if execution is None:
        warnings.append("交易日历尚未确认下一交易日。")
    return json_value(
        {
            "format_version": 1,
            "strategy": STRATEGY_ID,
            "asof": plan["asof"],
            "exec_date": execution,
            "exec_hint": "T+1_open" if execution else "next_session_unconfirmed",
            "warnings": warnings,
            "input_fingerprint": result.metadata["data_fingerprint"],
            "run_id": result.metadata["run_id"],
            "run_path": str(path),
            "panel": str(input_path),
            "account_basis": "model_replay",
            "buy": buys,
            "reduce": [],
            "positions": plan["positions"],
            "portfolio": plan["portfolio"],
            "meta": {
                "code": result.metadata["strategy_params"]["code"],
                "n_positions": len(plan["positions"]),
            },
            "orders": {
                "source": "strategy_class_model_account",
                "capital": plan["portfolio"]["equity"],
                "buy": buys,
                "sell": [],
            },
            "note": "买入一个标的后持续持有的模型账户示例；没有择时、止损或换仓。",
        }
    )
