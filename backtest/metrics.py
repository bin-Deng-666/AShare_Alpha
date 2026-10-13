"""Performance from the entire account curve; benchmarks never remove dates."""

from __future__ import annotations
import math
import numpy as np
import pandas as pd
from .models import finite, integer, timestamp


def _risk(returns, nav, trading_days):
    returns = pd.Series(returns, dtype=float)
    deviation = float(returns.std(ddof=1)) if len(returns) > 1 else float("nan")
    vol = deviation * math.sqrt(trading_days)
    downside = math.sqrt(float(np.minimum(returns, 0).pow(2).mean())) * math.sqrt(
        trading_days
    )
    high = 1.0
    duration = best = 0
    drawdowns = []
    for value in nav:
        high = max(high, float(value))
        drawdowns.append(float(value) / high - 1)
        duration = duration + 1 if value < high - 1e-12 else 0
        best = max(best, duration)
    return vol, downside, min(drawdowns, default=0.0), best


def _ratio(a, b):
    return float(a / b) if math.isfinite(b) and b > 1e-12 else float("nan")


def compute_metrics(
    daily,
    trades=None,
    bench=None,
    initial_capital=1_000_000,
    *,
    benchmark_name="沪深300",
    benchmark_code="000300",
    trading_days=242,
):
    capital = finite(initial_capital, "initial_capital")
    if capital <= 0:
        raise ValueError("Capital must be positive")
    integer(trading_days, "trading_days", minimum=1)
    d = daily.copy(deep=True)
    if d.empty or not {"date", "equity"} <= set(d):
        raise ValueError("Nonempty date/equity account curve required")
    d["date"] = d.date.map(lambda x: timestamp(x).normalize())
    if d.date.duplicated().any():
        raise ValueError("Duplicate account dates")
    d = d.sort_values("date").reset_index(drop=True)
    equity = pd.to_numeric(d.equity, errors="coerce")
    if not np.isfinite(equity).all() or equity.lt(0).any():
        raise ValueError("Invalid account equity")
    previous = equity.shift(1, fill_value=capital)
    if (previous.eq(0) & equity.gt(0)).any():
        raise ValueError(
            "Recovery from zero equity requires cash-flow adjusted metrics"
        )
    d["daily_ret"] = (equity / previous.where(previous.ne(0)) - 1).fillna(0.0)
    d["strategy_nav"] = equity / capital
    for name, default in [
        ("cash", 0.0),
        ("market_value", 0.0),
        ("receivables", 0.0),
        ("n_positions", 0),
        ("income_pnl", 0.0),
    ]:
        if name not in d:
            d[name] = default
    d["cash_ratio"] = d.cash / divide_safe(equity)
    if {"cash", "market_value"} <= set(daily):
        if not np.allclose(
            d.cash + d.market_value + d.receivables, equity, atol=0.011, rtol=1e-10
        ):
            raise ValueError(
                "Account equity does not reconcile to cash, positions and receivables"
            )
    years = len(d) / trading_days
    total_return = float(equity.iloc[-1] / capital - 1)
    ann = float((1 + total_return) ** (1 / years) - 1)
    vol, downside, max_dd, duration = _risk(d.daily_ret, d.strategy_nav, trading_days)
    sharpe = _ratio(
        float(d.daily_ret.mean()) * math.sqrt(trading_days),
        float(d.daily_ret.std(ddof=1)),
    )
    sortino = _ratio(float(d.daily_ret.mean()) * trading_days, downside)
    b = None
    if bench is not None:
        if not {"date", "ret"} <= set(bench):
            raise ValueError("Benchmark requires date and ret")
        b = bench[["date", "ret"]].copy()
        b["date"] = b.date.map(lambda x: timestamp(x).normalize())
        if b.date.duplicated().any():
            raise ValueError("Duplicate benchmark dates")
        b["ret"] = pd.to_numeric(b.ret, errors="coerce")
        if ((b.ret.notna()) & (~np.isfinite(b.ret) | b.ret.lt(-1))).any():
            raise ValueError("Invalid benchmark returns")
        d = d.merge(
            b.rename(columns={"ret": "bench_ret"}),
            on="date",
            how="left",
            validate="one_to_one",
        )
    else:
        d["bench_ret"] = np.nan
    complete = bool(b is not None and d.bench_ret.notna().all())
    paired = d.dropna(subset=["bench_ret"])
    pairs = len(paired)
    nan = float("nan")
    b_total = b_ann = b_vol = b_dd = b_sharpe = excess_ann = relative_total = (
        excess_dd
    ) = nan
    d["bench_nav"] = np.nan
    d["excess_nav"] = np.nan
    if complete:
        d["bench_nav"] = (1 + d.bench_ret).cumprod()
        d["excess_nav"] = d.strategy_nav / divide_safe(d.bench_nav)
        b_total = float(d.bench_nav.iloc[-1] - 1)
        b_ann = float((1 + b_total) ** (1 / years) - 1)
        b_vol, _, b_dd, _ = _risk(d.bench_ret, d.bench_nav, trading_days)
        b_sharpe = _ratio(
            float(d.bench_ret.mean()) * math.sqrt(trading_days),
            float(d.bench_ret.std(ddof=1)),
        )
        excess_ann = ann - b_ann
        relative_total = float(d.excess_nav.iloc[-1] - 1)
        _, _, excess_dd, _ = _risk(
            d.daily_ret - d.bench_ret, d.excess_nav.fillna(0), trading_days
        )
    beta = alpha = corr = te = ir = win = nan
    if pairs:
        x = paired.bench_ret
        y = paired.daily_ret
        active = y - x
        win = float(active.gt(0).mean())
        te = float(active.std(ddof=1) * math.sqrt(trading_days)) if pairs > 1 else nan
        ir = _ratio(float(active.mean()) * trading_days, te)
        if pairs > 1 and x.var(ddof=1) > 1e-12:
            beta = float(y.cov(x) / x.var(ddof=1))
            alpha = float((y.mean() - beta * x.mean()) * trading_days)
            corr = float(y.corr(x)) if y.std(ddof=1) > 1e-12 else nan
    t = pd.DataFrame() if trades is None else trades.copy(deep=True)
    if len(t):
        if not {"side", "amount", "fee", "realized_pnl"} <= set(t):
            raise ValueError("Incomplete trade schema")
        t["side"] = t.side.astype(str).str.upper()
        if not t.side.isin(["BUY", "SELL"]).all():
            raise ValueError("Unknown trade side")
        for col in ["amount", "fee", "realized_pnl"]:
            t[col] = pd.to_numeric(t[col], errors="coerce")
            if not np.isfinite(t[col]).all():
                raise ValueError(f"Invalid trade {col}")
        if t.amount.lt(0).any() or t.fee.lt(0).any():
            raise ValueError("Negative trade amount or fee")
    sells = t[t.side.eq("SELL")] if len(t) else t
    profits = sells.realized_pnl if len(sells) else pd.Series(dtype=float)
    if "matched_lots" in sells:
        closed_lots = sum(
            int(lot.get("closed", False))
            for matched in sells.matched_lots
            if isinstance(matched, list)
            for lot in matched
        )
    elif "position_shares" in sells:
        closed_lots = int(sells.position_shares.eq(0).sum())
    else:
        closed_lots = float("nan") if len(sells) else 0
    wins = profits[profits > 0]
    losses = profits[profits < 0]
    total_pnl = float(equity.iloc[-1] - capital)
    realized = float(profits.sum() + d.income_pnl.sum())
    yearly = []
    for year, group in d.groupby(d.date.dt.year):
        sret = float((1 + group.daily_ret).prod() - 1)
        valid = group.bench_ret.notna().all() and b is not None
        bret = float((1 + group.bench_ret).prod() - 1) if valid else nan
        yearly.append(
            {
                "year": int(year),
                "strategy": sret,
                "benchmark": bret,
                "excess": sret - bret,
                "strategy_sharpe": _ratio(
                    float(group.daily_ret.mean()) * math.sqrt(trading_days),
                    float(group.daily_ret.std(ddof=1)),
                ),
            }
        )
    monthly = []
    for date, row in (
        d.set_index("date")
        .resample("ME")
        .last()
        .dropna(subset=["strategy_nav"])
        .iterrows()
    ):
        monthly.append(
            {
                "date": str(date.date()),
                "strategy": float(row.strategy_nav),
                "benchmark": float(row.bench_nav),
                "excess": float(row.excess_nav),
                "equity": float(row.equity),
            }
        )
    return {
        "capital": {
            "initial_capital": capital,
            "ending_equity": float(equity.iloc[-1]),
            "ending_cash": float(d.cash.iloc[-1]),
            "ending_market_value": float(d.market_value.iloc[-1]),
            "total_pnl": total_pnl,
            "total_return": total_return,
            "total_fees": float(t.fee.sum()) if len(t) else 0.0,
            "trading_realized_pnl": float(profits.sum()),
            "income_pnl": float(d.income_pnl.sum()),
            "realized_pnl": realized,
            "unrealized_pnl": total_pnl - realized,
        },
        "returns": {
            "ann_return": ann,
            "bench_total_return": b_total,
            "bench_ann_return": b_ann,
            "excess_total": total_return - b_total,
            "excess_ann": excess_ann,
            "excess_nav_total": relative_total,
        },
        "risk": {
            "ann_vol": vol,
            "downside_dev": downside,
            "max_drawdown": max_dd,
            "max_dd_duration_days": duration,
            "calmar": _ratio(ann, abs(max_dd)),
            "sharpe": sharpe,
            "sortino": sortino,
            "bench_max_drawdown": b_dd,
            "bench_ann_vol": b_vol,
            "bench_sharpe": b_sharpe,
        },
        "relative": {
            "beta": beta,
            "alpha_ann": alpha,
            "corr": corr,
            "tracking_error": te,
            "information_ratio": ir,
            "excess_win_rate": win,
            "excess_max_drawdown": excess_dd,
        },
        "trading": {
            "n_buys": int(t.side.eq("BUY").sum()) if len(t) else 0,
            "n_sells": len(sells),
            "win_rate": float(profits.gt(0).mean()) if len(profits) else nan,
            "avg_win": float(wins.mean()) if len(wins) else 0.0,
            "avg_loss": float(losses.mean()) if len(losses) else 0.0,
            "payoff_ratio": (
                _ratio(float(wins.mean()), abs(float(losses.mean())))
                if len(wins) and len(losses)
                else nan
            ),
            "profit_factor": _ratio(float(wins.sum()), abs(float(losses.sum()))),
            "avg_hold_days": (
                float(sells.hold_days.mean())
                if "hold_days" in sells and len(sells)
                else nan
            ),
            "median_hold_days": (
                float(sells.hold_days.median())
                if "hold_days" in sells and len(sells)
                else nan
            ),
            "n_sell_fills": len(sells),
            "n_round_trips": closed_lots,
            "turnover_ann": (
                float(t.amount.sum() / equity.mean() / years)
                if len(t) and equity.mean() > 0
                else 0.0
            ),
            "avg_n_positions": float(d.n_positions.mean()),
            "max_n_positions": int(d.n_positions.max()),
            "avg_cash_ratio": float(d.cash_ratio.mean()),
            "exposure_avg": float((d.market_value / equity.replace(0, np.nan)).mean()),
        },
        "meta": {
            "start": str(d.date.iloc[0].date()),
            "end": str(d.date.iloc[-1].date()),
            "days": len(d),
            "n_years": years,
            "benchmark": benchmark_name,
            "benchmark_code": benchmark_code,
            "benchmark_complete": complete,
            "benchmark_paired_days": pairs,
            "definitions": {
                "annualization": "sessions/trading_days",
                "sharpe": "daily mean/std, risk-free zero",
                "drawdown_duration": "sessions below prior high including initial capital",
                "relative_nav": "strategy_nav/benchmark_nav",
            },
        },
        "yearly": yearly,
        "nav_monthly": monthly,
        "merged_daily": d,
    }


def divide_safe(series):
    return series.where(series.ne(0))
