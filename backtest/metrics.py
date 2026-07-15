"""Compute full performance metrics vs benchmark.

Pure math, zero strategy coupling. Works with any strategy that produces
daily equity logs and a trade blotter in the expected schema.
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def _max_dd_duration(nav: pd.Series) -> int:
    peak = nav.iloc[0]
    peak_i = 0
    best = 0
    for i, v in enumerate(nav.values):
        if v >= peak:
            best = max(best, i - peak_i)
            peak = v
            peak_i = i
    best = max(best, len(nav) - 1 - peak_i)
    return int(best)


def compute_metrics(
    daily: pd.DataFrame,
    trades: pd.DataFrame,
    bench: pd.DataFrame,
    initial_capital: float,
    *,
    benchmark_name: str = "沪深300",
    benchmark_code: str = "000300",
    trading_days: int = 242,
) -> dict:
    """
    daily: date, equity, nav, daily_ret, cash, market_value, n_positions, cash_ratio, daily_pnl
    trades: blotter with side, amount, fee, realized_pnl, hold_days
    bench: date, ret (open-to-open)
    """
    d = daily.copy()
    d["date"] = pd.to_datetime(d["date"])
    b = bench.copy()
    b["date"] = pd.to_datetime(b["date"])
    m = d.merge(b[["date", "ret"]].rename(columns={"ret": "bench_ret"}), on="date", how="inner")
    if m.empty:
        return {"error": "no overlap with benchmark"}

    m["bench_nav"] = (1 + m["bench_ret"]).cumprod()
    m["bench_nav"] = m["bench_nav"] / m["bench_nav"].iloc[0]
    m["strategy_nav"] = m["equity"] / initial_capital
    m["excess_ret"] = m["daily_ret"] - m["bench_ret"]
    m["excess_nav"] = (1 + m["excess_ret"]).cumprod()

    start, end = m["date"].iloc[0], m["date"].iloc[-1]
    n_years = max((end - start).days / 365.25, 1e-6)
    ending_equity = float(m["equity"].iloc[-1])
    total_pnl = ending_equity - initial_capital
    total_return = total_pnl / initial_capital
    ann_return = (1 + total_return) ** (1 / n_years) - 1

    rets = m["daily_ret"]
    ann_vol = float(rets.std() * np.sqrt(trading_days))
    downside = rets[rets < 0]
    downside_dev = float(downside.std() * np.sqrt(trading_days)) if len(downside) else float("nan")
    nav = m["strategy_nav"]
    max_dd = float((nav / nav.cummax() - 1).min())
    max_dd_dur = _max_dd_duration(nav)
    sharpe = float(ann_return / ann_vol) if ann_vol > 1e-12 else float("nan")
    sortino = float(ann_return / downside_dev) if downside_dev and downside_dev > 1e-12 else float("nan")
    calmar = float(ann_return / abs(max_dd)) if abs(max_dd) > 1e-12 else float("nan")

    b_total = float(m["bench_nav"].iloc[-1] - 1)
    b_ann = (1 + b_total) ** (1 / n_years) - 1
    b_vol = float(m["bench_ret"].std() * np.sqrt(trading_days))
    b_nav = m["bench_nav"]
    b_dd = float((b_nav / b_nav.cummax() - 1).min())
    b_sharpe = float(b_ann / b_vol) if b_vol > 1e-12 else float("nan")

    excess_total = float(m["excess_nav"].iloc[-1] - 1)
    excess_ann = (1 + excess_total) ** (1 / n_years) - 1
    te = float(m["excess_ret"].std() * np.sqrt(trading_days))
    ir = float(excess_ann / te) if te > 1e-12 else float("nan")
    excess_win = float((m["excess_ret"] > 0).mean())
    excess_dd = float((m["excess_nav"] / m["excess_nav"].cummax() - 1).min())

    x = m["bench_ret"].values
    y = m["daily_ret"].values
    if len(x) > 5 and np.std(x) > 1e-12:
        beta = float(np.cov(y, x, ddof=1)[0, 1] / np.var(x, ddof=1))
        alpha_daily = float(np.mean(y) - beta * np.mean(x))
        alpha_ann = alpha_daily * trading_days
        corr = float(np.corrcoef(y, x)[0, 1])
    else:
        beta = alpha_ann = corr = float("nan")

    t = trades.copy() if trades is not None and len(trades) else pd.DataFrame()
    n_buys = int((t["side"] == "BUY").sum()) if len(t) else 0
    n_sells = int((t["side"] == "SELL").sum()) if len(t) else 0
    total_fees = float(t["fee"].sum()) if len(t) else 0.0
    sells = t[t["side"] == "SELL"] if len(t) else t
    realized = float(sells["realized_pnl"].sum()) if len(sells) else 0.0
    ending_mv = float(m["market_value"].iloc[-1])
    ending_cash = float(m["cash"].iloc[-1])
    unrealized = total_pnl - realized

    if len(sells):
        wins = sells[sells["realized_pnl"] > 0]["realized_pnl"]
        losses = sells[sells["realized_pnl"] <= 0]["realized_pnl"]
        win_rate = float((sells["realized_pnl"] > 0).mean())
        avg_win = float(wins.mean()) if len(wins) else 0.0
        avg_loss = float(losses.mean()) if len(losses) else 0.0
        payoff = abs(avg_win / avg_loss) if avg_loss != 0 else float("nan")
        gross_profit = float(wins.sum()) if len(wins) else 0.0
        gross_loss = float(losses.sum()) if len(losses) else 0.0
        profit_factor = float(gross_profit / abs(gross_loss)) if gross_loss != 0 else float("nan")
        avg_hold = float(sells["hold_days"].mean()) if "hold_days" in sells else float("nan")
        med_hold = float(sells["hold_days"].median()) if "hold_days" in sells else float("nan")
        n_round = int(len(sells))
    else:
        win_rate = avg_win = avg_loss = payoff = profit_factor = avg_hold = med_hold = float("nan")
        n_round = 0

    traded_notional = float(t["amount"].sum()) if len(t) else 0.0
    avg_equity = float(m["equity"].mean())
    turnover_ann = (traded_notional / avg_equity / n_years) if avg_equity > 0 else float("nan")

    y_df = m.copy()
    y_df["year"] = y_df["date"].dt.year
    yearly = []
    for year, g in y_df.groupby("year"):
        s_ret = float(g["equity"].iloc[-1] / g["equity"].iloc[0] - 1)
        b_ret = float((1 + g["bench_ret"]).prod() - 1)
        yearly.append(
            {
                "year": int(year),
                "strategy": s_ret,
                "benchmark": b_ret,
                "excess": s_ret - b_ret,
            }
        )

    month_end = m.set_index("date").resample("ME").last().dropna(subset=["strategy_nav"])
    nav_monthly = [
        {
            "date": str(idx.date()),
            "strategy": float(row["strategy_nav"]),
            "benchmark": float(row["bench_nav"]),
            "excess": float(row["excess_nav"]),
            "equity": float(row["equity"]),
        }
        for idx, row in month_end.iterrows()
    ]

    return {
        "capital": {
            "initial_capital": initial_capital,
            "ending_equity": ending_equity,
            "ending_cash": ending_cash,
            "ending_market_value": ending_mv,
            "total_pnl": total_pnl,
            "total_return": total_return,
            "total_fees": total_fees,
            "realized_pnl": realized,
            "unrealized_pnl": unrealized,
        },
        "returns": {
            "ann_return": float(ann_return),
            "bench_total_return": b_total,
            "bench_ann_return": float(b_ann),
            "excess_total": float(m["strategy_nav"].iloc[-1] - m["bench_nav"].iloc[-1]),
            "excess_ann": float(excess_ann),
            "excess_nav_total": excess_total,
        },
        "risk": {
            "ann_vol": ann_vol,
            "downside_dev": downside_dev,
            "max_drawdown": max_dd,
            "max_dd_duration_days": max_dd_dur,
            "calmar": calmar,
            "sortino": sortino,
            "sharpe": sharpe,
            "bench_max_drawdown": b_dd,
            "bench_ann_vol": b_vol,
            "bench_sharpe": b_sharpe,
        },
        "relative": {
            "beta": beta,
            "alpha_ann": float(alpha_ann),
            "corr": corr,
            "tracking_error": te,
            "information_ratio": ir,
            "excess_win_rate": excess_win,
            "excess_max_drawdown": excess_dd,
        },
        "trading": {
            "n_buys": n_buys,
            "n_sells": n_sells,
            "n_round_trips": n_round,
            "win_rate": win_rate,
            "avg_win": avg_win,
            "avg_loss": avg_loss,
            "payoff_ratio": payoff,
            "profit_factor": profit_factor,
            "avg_hold_days": avg_hold,
            "median_hold_days": med_hold,
            "turnover_ann": float(turnover_ann),
            "avg_n_positions": float(m["n_positions"].mean()),
            "max_n_positions": int(m["n_positions"].max()),
            "avg_cash_ratio": float(m["cash_ratio"].mean()),
            "exposure_avg": float((1 - m["cash_ratio"]).mean()),
        },
        "meta": {
            "start": str(start.date()),
            "end": str(end.date()),
            "days": int(len(m)),
            "n_years": float(n_years),
            "benchmark": benchmark_name,
            "benchmark_code": benchmark_code,
        },
        "yearly": yearly,
        "nav_monthly": nav_monthly,
        "merged_daily": m,
    }
