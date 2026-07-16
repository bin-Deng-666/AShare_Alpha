"""Five-sleeve, next-open portfolio execution for the rotating strategy."""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from backtest.costs import apply_slippage, lot_floor, slippage_bps_for_row, trade_fee
from strategy.rotating.config.params import DEFAULT_PARAMS, Params
from strategy.rotating.features.market_regime import monthly_gate_state_by_month


def run_portfolio(
    scores: pd.DataFrame,
    bars: pd.DataFrame,
    *,
    params: Params = DEFAULT_PARAMS,
    initial_capital: float = 1_000_000.0,
    entry_gate: pd.DataFrame | None = None,
) -> dict[str, Any]:
    """Turn close-of-day scores into auditable next-open fills."""
    score_frame = scores.copy()
    bar_frame = bars.copy()
    required_scores = {"code", "date", "ensemble_score"}
    required_bars = {"code", "date", "open", "close"}
    if missing := required_scores.difference(score_frame.columns):
        raise ValueError(f"score frame missing columns: {', '.join(sorted(missing))}")
    if missing := required_bars.difference(bar_frame.columns):
        raise ValueError(f"bar frame missing columns: {', '.join(sorted(missing))}")

    for frame in (score_frame, bar_frame):
        frame["code"] = frame["code"].astype(str).str.zfill(6)
        frame["date"] = pd.to_datetime(frame["date"]).dt.normalize()
    score_frame = score_frame.sort_values(["date", "ensemble_score"], ascending=[True, False])
    scores_by_date = {
        pd.Timestamp(date): group
        for date, group in score_frame.groupby("date", sort=False)
    }
    bar_frame = bar_frame.sort_values(["date", "code"])
    bar_lookup = bar_frame.set_index(["date", "code"], drop=False)
    trading_dates = list(pd.DatetimeIndex(bar_frame["date"].drop_duplicates()).sort_values())

    gate_by_month = (
        monthly_gate_state_by_month(entry_gate) if entry_gate is not None else None
    )

    def allows_new_buys(execution_date: pd.Timestamp) -> bool:
        if gate_by_month is None:
            return True
        return bool(
            gate_by_month.get(pd.Timestamp(execution_date).to_period("M"), False)
        )

    cash = float(initial_capital)
    positions: dict[str, dict[str, Any]] = {}
    pending: list[dict[str, Any]] = []
    trades: list[dict[str, Any]] = []
    equity_rows: list[dict[str, Any]] = []

    for date_index, trade_date in enumerate(trading_dates):
        still_pending: list[dict[str, Any]] = []
        orders_to_process = sorted(
            pending,
            key=lambda order: 0
            if str(order.get("side", "BUY")).upper() == "SELL"
            else 1,
        )
        for order in orders_to_process:
            if trade_date <= order["signal_date"]:
                still_pending.append(order)
                continue
            side = str(order.get("side", "BUY")).upper()
            if side == "SELL" and order["code"] not in positions:
                continue
            if side == "BUY" and not allows_new_buys(trade_date):
                # A delayed buy must not cross into a newly risk-off month.
                continue
            key = (trade_date, order["code"])
            if key not in bar_lookup.index:
                still_pending.append(order)
                continue
            bar = bar_lookup.loc[key]
            if isinstance(bar, pd.DataFrame):
                bar = bar.iloc[-1]
            if not bool(bar.get("trade_status", True)):
                still_pending.append(order)
                continue
            raw_open = float(bar["open"])
            if not np.isfinite(raw_open) or raw_open <= 0.0:
                still_pending.append(order)
                continue
            high = float(bar.get("high", np.nan))
            low = float(bar.get("low", np.nan))
            limit_up = float(bar.get("limit_up", np.nan))
            one_price = (
                np.isfinite(high)
                and np.isfinite(low)
                and np.isclose(high, low)
                and np.isclose(raw_open, high)
            )
            at_limit_up = bool(bar.get("is_limit_up", False)) or (
                np.isfinite(limit_up) and np.isclose(raw_open, limit_up)
            )
            limit_down = float(bar.get("limit_down", np.nan))
            at_limit_down = bool(bar.get("is_limit_down", False)) or (
                np.isfinite(limit_down) and np.isclose(raw_open, limit_down)
            )
            if one_price and (
                (side == "BUY" and at_limit_up)
                or (side == "SELL" and at_limit_down)
            ):
                still_pending.append(order)
                continue

            slip_bps = slippage_bps_for_row(order, params)
            price = apply_slippage(raw_open, side, slip_bps)
            if side == "SELL":
                position = positions[order["code"]]
                shares = int(position["shares"])
                notional = shares * price
                fee = trade_fee(notional, "SELL", params, trade_date)
                cash += notional - fee
                trades.append(
                    {
                        "signal_date": order["signal_date"],
                        "date": trade_date,
                        "code": order["code"],
                        "side": "SELL",
                        "shares": shares,
                        "price": price,
                        "amount": notional,
                        "fee": fee,
                        "slip_bps": slip_bps,
                        "sleeve": position["sleeve"],
                        "industry": position["industry"],
                        "adv_20": position["adv_20"],
                        "float_market_cap": position["float_market_cap"],
                        "realized_pnl": (
                            (price - float(position["entry_price"])) * shares
                            - fee
                            - float(position["entry_fee"])
                        ),
                        "hold_days": date_index - int(position["entry_session_index"]),
                    }
                )
                del positions[order["code"]]
                continue

            sleeve_positions = sum(
                1
                for position in positions.values()
                if int(position["sleeve"]) == int(order["sleeve"])
            )
            if sleeve_positions >= params.names_per_sleeve:
                still_pending.append(order)
                continue

            market_value_at_open = 0.0
            industry_value_at_open = 0.0
            order_industry = str(order.get("industry", "UNKNOWN"))
            for held_code, held in positions.items():
                held_price = float(held["entry_price"])
                held_key = (trade_date, held_code)
                if held_key in bar_lookup.index:
                    held_bar = bar_lookup.loc[held_key]
                    if isinstance(held_bar, pd.DataFrame):
                        held_bar = held_bar.iloc[-1]
                    candidate_price = float(held_bar["open"])
                    if np.isfinite(candidate_price) and candidate_price > 0.0:
                        held_price = candidate_price
                held_value = int(held["shares"]) * held_price
                market_value_at_open += held_value
                if str(held["industry"]) == order_industry:
                    industry_value_at_open += held_value
            equity_at_open = cash + market_value_at_open
            industry_room = max(
                0.0,
                equity_at_open * params.max_industry_weight - industry_value_at_open,
            )
            target_notional = min(
                equity_at_open / params.sleeves / params.names_per_sleeve,
                equity_at_open * params.max_stock_weight,
                industry_room,
                float(order.get("adv_20", np.inf)) * params.max_adv_fraction,
            )
            shares = lot_floor(int(target_notional // price), params.lot_size)
            shares = min(shares, lot_floor(int(cash // price), params.lot_size))
            while shares >= params.lot_size:
                notional = shares * price
                fee = trade_fee(notional, "BUY", params, trade_date)
                if notional + fee <= cash:
                    break
                shares -= params.lot_size
            if shares < params.lot_size:
                continue
            notional = shares * price
            fee = trade_fee(notional, "BUY", params, trade_date)
            cash -= notional + fee
            positions[order["code"]] = {
                "code": order["code"],
                "shares": shares,
                "entry_price": price,
                "entry_date": trade_date,
                "entry_session_index": date_index,
                "review_session_index": date_index,
                "entry_fee": fee,
                "sleeve": order["sleeve"],
                "industry": order.get("industry", "UNKNOWN"),
                "adv_20": order.get("adv_20", np.inf),
                "float_market_cap": order.get("float_market_cap", np.nan),
                "amount": order.get("amount", np.nan),
            }
            trades.append(
                {
                    "signal_date": order["signal_date"],
                    "date": trade_date,
                    "code": order["code"],
                    "side": "BUY",
                    "shares": shares,
                    "price": price,
                    "amount": notional,
                    "fee": fee,
                    "slip_bps": slip_bps,
                    "sleeve": order["sleeve"],
                    "industry": order.get("industry", "UNKNOWN"),
                    "adv_20": order.get("adv_20", np.nan),
                    "float_market_cap": order.get("float_market_cap", np.nan),
                    "realized_pnl": 0.0,
                    "hold_days": 0,
                }
            )
        pending = still_pending

        day_scores = scores_by_date.get(trade_date, score_frame.iloc[0:0]).copy()
        if "eligible" in day_scores:
            day_scores = day_scores.loc[day_scores["eligible"].fillna(False).astype(bool)]
        day_scores = day_scores.sort_values("ensemble_score", ascending=False)
        exit_count = max(1, int(np.ceil(len(day_scores) * params.exit_quantile)))
        exit_buffer = set(day_scores.head(exit_count)["code"]) if len(day_scores) else set()
        pending_sells = {
            order["code"]
            for order in pending
            if str(order.get("side", "BUY")).upper() == "SELL"
        }
        for code, position in positions.items():
            sessions_held = date_index - int(position["review_session_index"]) + 1
            if sessions_held >= params.holding_sessions and code not in pending_sells:
                if code in exit_buffer:
                    position["review_session_index"] = date_index + 1
                    continue
                pending.append(
                    {
                        "signal_date": trade_date,
                        "code": code,
                        "side": "SELL",
                        "sleeve": position["sleeve"],
                        "industry": position["industry"],
                        "adv_20": position["adv_20"],
                        "float_market_cap": position["float_market_cap"],
                        "amount": position["amount"],
                    }
                )
                pending_sells.add(code)

        sleeve = date_index % params.sleeves
        held_in_sleeve = {
            code
            for code, position in positions.items()
            if int(position["sleeve"]) == sleeve and code not in pending_sells
        }
        pending_buys = {
            order["code"]
            for order in pending
            if str(order.get("side", "BUY")).upper() == "BUY"
        }
        pending_buys_in_sleeve = sum(
            1
            for order in pending
            if str(order.get("side", "BUY")).upper() == "BUY"
            and int(order["sleeve"]) == sleeve
        )
        open_slots = max(
            0,
            params.names_per_sleeve - len(held_in_sleeve) - pending_buys_in_sleeve,
        )
        planned_entry_date = (
            trading_dates[date_index + 1]
            if date_index + 1 < len(trading_dates)
            else None
        )
        entry_is_allowed = gate_by_month is None or (
            planned_entry_date is not None
            and allows_new_buys(planned_entry_date)
        )
        if not day_scores.empty and open_slots > 0 and entry_is_allowed:
            quantile_count = max(1, int(np.ceil(len(day_scores) * params.enter_quantile)))
            candidates = day_scores.head(quantile_count)
            candidates = candidates.loc[
                ~candidates["code"].isin(set(positions) | pending_buys)
            ].head(open_slots)
            for _, candidate in candidates.iterrows():
                pending.append(
                    {
                        "signal_date": trade_date,
                        "code": candidate["code"],
                        "side": "BUY",
                        "sleeve": sleeve,
                        "industry": candidate.get("industry", "UNKNOWN"),
                        "adv_20": candidate.get("adv_20", np.inf),
                        "float_market_cap": candidate.get("float_market_cap", np.nan),
                        "amount": candidate.get("amount", candidate.get("adv_20", np.nan)),
                    }
                )

        market_value = 0.0
        for code, position in positions.items():
            key = (trade_date, code)
            close = position["entry_price"]
            if key in bar_lookup.index:
                close_row = bar_lookup.loc[key]
                if isinstance(close_row, pd.DataFrame):
                    close_row = close_row.iloc[-1]
                close = float(close_row["close"])
            market_value += position["shares"] * close
        equity_rows.append(
            {
                "date": trade_date,
                "cash": cash,
                "market_value": market_value,
                "equity": cash + market_value,
                "n_positions": len(positions),
                "cash_ratio": cash / (cash + market_value) if cash + market_value else 0.0,
                "allow_new_buys": allows_new_buys(trade_date),
            }
        )

    equity = pd.DataFrame(equity_rows)
    if not equity.empty:
        equity["daily_ret"] = equity["equity"].pct_change().fillna(0.0)
        equity["daily_pnl"] = equity["equity"].diff().fillna(
            equity["equity"].iloc[0] - float(initial_capital)
        )
        equity["nav"] = equity["equity"] / float(initial_capital)
    return {
        "equity": equity,
        "trades": pd.DataFrame(trades),
        "positions": pd.DataFrame(positions.values()),
        "pending_orders": pd.DataFrame(pending),
        "cash": cash,
    }
