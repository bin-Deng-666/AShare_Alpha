"""A single calendar-driven loop for every strategy and decision mode."""

from __future__ import annotations
from copy import deepcopy
from collections.abc import Mapping
from dataclasses import fields, is_dataclass
from hashlib import sha256
from pathlib import Path
import inspect
import json
import numpy as np
import pandas as pd
from .account import Account
from .actions import apply_actions
from .config import BacktestConfig
from .data import MarketData, DataView
from .execution import Broker
from .metrics import compute_metrics
from .models import DataRequirements, OrderBatch, TargetPortfolio
from .planning import plan_target
from .results import BacktestResult, BacktestError, json_value
from .strategy import StrategyContext
from ashare_alpha.paths import CODE_ROOT

ENGINE_VERSION = "1.0"


def _code_identity(strategy_class):
    try:
        path = Path(inspect.getfile(strategy_class))
        source = (
            path.read_bytes()
            if path.is_file()
            else inspect.getsource(strategy_class).encode()
        )
        return sha256(source).hexdigest()
    except (TypeError, OSError):
        return None


def _copy_parameters(value):
    if is_dataclass(value):
        return type(value)(
            **{
                f.name: _copy_parameters(getattr(value, f.name))
                for f in fields(value)
                if f.init
            }
        )
    if isinstance(value, Mapping):
        return {k: _copy_parameters(v) for k, v in value.items()}
    if isinstance(value, tuple):
        return tuple(_copy_parameters(v) for v in value)
    if isinstance(value, list):
        return [_copy_parameters(v) for v in value]
    return deepcopy(value)


class BacktestEngine:
    def __init__(self, data: MarketData):
        if not isinstance(data, MarketData):
            raise TypeError("Provide materialized MarketData")
        self.data = data

    @staticmethod
    def _runtime_versions():
        import platform
        from importlib.metadata import version, PackageNotFoundError

        result = {"python": platform.python_version(), "platform": platform.system()}
        for name in ("numpy", "pandas", "pyarrow", "duckdb", "scikit-learn"):
            try:
                result[name] = version(name)
            except PackageNotFoundError:
                continue
        return result

    def run(self, strategy_class, *, strategy_params=None, config: BacktestConfig):
        if not isinstance(strategy_class, type):
            raise TypeError("Pass a strategy class, not a reused instance")
        if not isinstance(config, BacktestConfig):
            raise TypeError("Provide BacktestConfig")
        if config.cash_yield == "input" and self.data._cash_yield is None:
            raise ValueError("Dated cash yield was requested without an input series")
        params = _copy_parameters(strategy_params)
        params_json = json_value(params)
        strategy = strategy_class(params=params)
        needs = strategy.requirements()
        if not isinstance(needs, DataRequirements):
            raise TypeError("requirements() must return DataRequirements")
        if not set(needs.fields) <= set(self.data._bars):
            raise ValueError("Requested market fields are unavailable")
        if not set(needs.features) <= set(self.data._features):
            raise ValueError("Requested features are unavailable")
        dependency_hashes = {}
        for filename in needs.code_dependencies:
            path = Path(filename).resolve()
            dependency_hashes[
                str(path.relative_to(CODE_ROOT))
                if path.is_relative_to(CODE_ROOT)
                else str(path)
            ] = sha256(path.read_bytes()).hexdigest()
        calendar = self.data.calendar.copy()
        start = pd.Timestamp(config.start)
        end = pd.Timestamp(config.end)
        indices = [i for i, date in enumerate(calendar) if start <= date <= end]
        if not indices:
            raise ValueError("No calendar sessions in the requested interval")
        first = indices[0]
        if first < needs.warmup_sessions:
            raise ValueError(
                "Insufficient warmup sessions; test interval was not shifted"
            )
        # A trading calendar supplied without prices must still be processed and audited.
        account = Account(config.initial_capital)
        broker = Broker(account, self.data, config)
        rng = np.random.default_rng(config.seed)
        daily = []
        positions = []
        decisions = []
        returns = []
        diagnostics = {}
        last_equity = account.cash / 100
        metadata = {
            "engine_version": ENGINE_VERSION,
            "strategy": strategy_class.__module__ + "." + strategy_class.__qualname__,
            "engine_code_hash": sha256(
                b"".join(
                    p.name.encode() + p.read_bytes()
                    for p in sorted(Path(__file__).parent.glob("*.py"))
                )
            ).hexdigest(),
            "strategy_code_hash": _code_identity(strategy_class),
            "strategy_code_dependencies": dependency_hashes,
            "strategy_hash_scope": "defining_module_and_declared_dependencies",
            "runtime": self._runtime_versions(),
            "strategy_params": params_json,
            "config": json_value(config),
            "requirements": json_value(needs),
            "data_fingerprint": self.data.fingerprint,
            "data": deepcopy(self.data.metadata),
            "assumptions": {
                "execution": "next_open_conservative",
                "target_sizing": "signal_close",
                "cost_basis": "FIFO",
                "dividend_tax": "fixed_configured_rate",
                "cash_yield": (
                    "dated_previous_session"
                    if config.cash_yield == "input"
                    else "fixed_configured_rate"
                ),
                "bonus_rounding": "aggregate_floor_largest_remainder",
                "settlement": config.corporate_actions,
                "missing_valuation": "last_valid_close_marked_stale",
                "delisting": config.delisting_policy,
                "rights": config.rights_policy,
            },
            "first_decision": None,
        }
        identity = {k: v for k, v in metadata.items() if k != "first_decision"}
        metadata["run_id"] = sha256(
            json.dumps(json_value(identity), sort_keys=True).encode()
        ).hexdigest()[:20]

        def ctx(date, session):
            asof = date + pd.Timedelta(hours=15)
            return StrategyContext(
                asof,
                session - first,
                DataView(self.data, asof, needs),
                account.snapshot(session),
                broker.pending(),
                rng,
                tuple(returns),
                tuple(calendar[: indices[-1] + 1]),
            )

        def decision(date, session):
            value = strategy.on_close(ctx(date, session))
            if metadata["first_decision"] is None:
                metadata["first_decision"] = str(date + pd.Timedelta(hours=15))
            if value is None:
                return
            if needs.mode == "targets":
                if not isinstance(value, TargetPortfolio):
                    raise TypeError(
                        "Target strategy must return TargetPortfolio or None"
                    )
                decisions.extend(
                    plan_target(value, account, broker, self.data, date, session)
                )
            else:
                if not isinstance(value, OrderBatch):
                    raise TypeError("Order strategy must return OrderBatch or None")
                for oid in value.cancel:
                    broker.cancel(oid, date)
                for request in value.orders:
                    decisions.append(
                        {
                            "date": date,
                            "code": request.code,
                            "reason": request.reason,
                            "request": json_value(request),
                        }
                    )
                    broker.submit(request, date, session)

        def result(status, error=None):
            order_rows = [
                {
                    "order_id": o.order_id,
                    "submitted_at": o.submitted_at,
                    "request": json_value(o.request),
                    "status": o.status,
                    "remaining_quantity": o.remaining,
                    "remaining_budget": None if o.budget is None else o.budget / 100,
                    "origin": o.origin,
                }
                for o in broker.orders
            ]
            d = pd.DataFrame(daily)
            t = pd.DataFrame(account.trades)
            bench = self.data._benchmark
            if bench is not None:
                bench = bench.loc[
                    bench.date.ge(pd.Timestamp(config.start))
                    & bench.date.le(pd.Timestamp(config.end))
                ]
            metrics = (
                compute_metrics(
                    d,
                    t,
                    bench,
                    config.initial_capital,
                    trading_days=config.trading_days,
                    benchmark_code=self.data.metadata.get("benchmark"),
                    benchmark_name=self.data.metadata.get("benchmark") or "unspecified",
                )
                if status == "success"
                else {}
            )
            return BacktestResult(
                status,
                metadata,
                metrics,
                d,
                pd.DataFrame(positions),
                t,
                pd.DataFrame(order_rows),
                pd.DataFrame(broker.attempts),
                pd.DataFrame(account.ledger),
                pd.DataFrame(account.actions),
                pd.DataFrame(decisions),
                diagnostics,
                error,
            )

        current = calendar[first]
        event_cursor = 0
        try:
            initial = calendar[first - 1] if first else calendar[first]
            # With no previous session, on_start gets an empty pre-open data view.
            start_context = ctx(initial, first - 1)
            if first == 0:
                start_context = StrategyContext(
                    initial - pd.Timedelta(seconds=1),
                    -1,
                    DataView(self.data, initial - pd.Timedelta(seconds=1), needs),
                    account.snapshot(-1),
                    (),
                    rng,
                    (),
                    tuple(calendar[: indices[-1] + 1]),
                )
            callback = getattr(strategy, "on_start", None)
            if callback and callback(start_context) is not None:
                raise TypeError("on_start cannot submit trades")
            if first:
                decision(initial, first - 1)
            previous_date = initial
            for session in indices:
                current = calendar[session]
                opening_cash = account.cash
                ledger_start = len(account.ledger)
                settled = account.settle(current, session)
                action_events = apply_actions(
                    account, current, session, self.data, config
                )
                cash_rate = (
                    self.data.cash_rate_at(previous_date)
                    if config.cash_yield == "input"
                    else config.cash_rate
                )
                account.accrue(
                    current,
                    (current - previous_date).days,
                    cash_rate if cash_rate is not None else 0.0,
                    opening_cash,
                )
                broker.run_day(current, session)
                snapshot, stale = account.mark(
                    current,
                    self.data,
                    session,
                    delisting_policy=config.delisting_policy,
                )
                ret = snapshot.equity / last_equity - 1 if last_equity else 0.0
                returns.append(ret)
                last_equity = snapshot.equity
                income = sum(
                    row.get("entitlement_cash", 0)
                    + (row["cash_delta"] if row["kind"] == "cash_interest" else 0.0)
                    for row in account.ledger[ledger_start:]
                )
                daily.append(
                    {
                        "date": current,
                        "equity": snapshot.equity,
                        "cash": snapshot.cash,
                        "market_value": snapshot.market_value,
                        "receivables": snapshot.receivables,
                        "daily_ret": ret,
                        "n_positions": len(snapshot.positions),
                        "income_pnl": income,
                        "cash_rate": cash_rate,
                        "cash_rate_missing": cash_rate is None,
                        "stale_codes": stale,
                    }
                )
                for lot in snapshot.lots:
                    positions.append(
                        {
                            "date": current,
                            **json_value(lot),
                            "market_value": lot.quantity
                            * account.last_prices[lot.code]
                            / 100,
                        }
                    )
                events = (
                    tuple(broker.events[event_cursor:])
                    + tuple(settled)
                    + tuple(action_events)
                )
                event_cursor = len(broker.events)
                callback = getattr(strategy, "on_execution", None)
                if (
                    callback
                    and events
                    and callback(ctx(current, session), events) is not None
                ):
                    raise TypeError("on_execution cannot submit immediate trades")
                decision(current, session)
                previous_date = current
            broker.finish(current)
            callback = getattr(strategy, "on_execution", None)
            final_events = tuple(broker.events[event_cursor:])
            if (
                callback
                and final_events
                and callback(ctx(current, indices[-1]), final_events) is not None
            ):
                raise TypeError("on_execution cannot submit immediate trades")
            output = result("success")
            callback = getattr(strategy, "on_end", None)
            if callback:
                value = callback(output.view())
                if value is not None:
                    if not isinstance(value, dict):
                        raise TypeError("on_end must return a diagnostic dictionary")
                    diagnostics.update(json_value(value))
            return output
        except Exception as exc:
            metadata["failed_at"] = str(current)
            output = result("failed", str(exc))
            raise BacktestError(
                f"Backtest failed at {current.date()}: {exc}", output
            ) from exc
