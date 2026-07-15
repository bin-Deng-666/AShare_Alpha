"""Capital-aware backtest vs 沪深300 with full metrics + trade ledger."""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import replace
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
REPO = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from backtest.metrics import compute_metrics
from engine.portfolio import run_portfolio
from config.params import (
    DEFAULT_PARAMS,
    REPORT_DIR,
    SAMPLE_START,
    Params,
)
from features.build_panel import build_signal_panel, load_signals
from lib.db import ensure_dirs
from lib.market_data import load_bars, load_hs300  # re-exported for ablations
from lib.panel_paths import default_panel_path

BENCHMARK_NAME = "沪深300"


def write_performance_hub(
    metrics: dict,
    params: Params,
    tag: str,
    out_dir: Path | None = None,
) -> Path:
    out = Path(out_dir) if out_dir is not None else REPORT_DIR
    out.mkdir(parents=True, exist_ok=True)
    cap = metrics["capital"]
    ret = metrics["returns"]
    risk = metrics["risk"]
    rel = metrics["relative"]
    tr = metrics["trading"]
    meta = metrics["meta"]

    def pct(x: float, d: int = 2) -> str:
        if x is None or (isinstance(x, float) and (x != x)):
            return "n/a"
        return f"{x * 100:.{d}f}%"

    def num(x: float, d: int = 2) -> str:
        if x is None or (isinstance(x, float) and (x != x)):
            return "n/a"
        return f"{x:.{d}f}"

    payload = {
        "params": {
            "initial_capital": params.initial_capital,
            "top_n": params.top_n,
            "max_hold_days": params.max_hold_days,
            "cost_bps": params.cost_bps,
            "enable_realistic_costs": params.enable_realistic_costs,
            "commission_bps": params.commission_bps,
            "stamp_tax_bps": params.stamp_tax_bps,
            "transfer_fee_bps": params.transfer_fee_bps,
            "enable_open_slippage": params.enable_open_slippage,
            "version": params.version,
        },
        "meta": meta,
        "capital": cap,
        "returns": ret,
        "risk": risk,
        "relative": rel,
        "trading": tr,
        "yearly": metrics["yearly"],
        "nav_monthly": metrics["nav_monthly"],
    }
    (out / "PERFORMANCE.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    lines = [
        f"# 策略表现中心 · crowding_price_volume ({params.version})",
        "",
        f"> 基准：**{meta['benchmark']}** · {meta['start']} → {meta['end']} · {meta['days']} 日",
        f"> 初始本金：**{cap['initial_capital']:,.0f} 元**（不是单位净值账户）",
    ]
    if getattr(params, "enable_realistic_costs", False) or getattr(
        params, "enable_open_slippage", False
    ):
        lines.append(
            "> 执行：**A 股拆分费用"
            + (" + 开盘分档滑点" if getattr(params, "enable_open_slippage", False) else "")
            + "**"
        )
    lines += [
        "",
        "## 1. 资金与盈亏",
        "",
        "| 指标 | 数值 |",
        "|------|------|",
        f"| 初始本金 | {cap['initial_capital']:,.2f} |",
        f"| 期末权益 | {cap['ending_equity']:,.2f} |",
        f"| 期末现金 | {cap['ending_cash']:,.2f} |",
        f"| 期末市值 | {cap['ending_market_value']:,.2f} |",
        f"| 总盈亏 | {cap['total_pnl']:,.2f} |",
        f"| 总收益率 | {pct(cap['total_return'])} |",
        f"| 已实现盈亏 | {cap['realized_pnl']:,.2f} |",
        f"| 未实现盈亏（残差） | {cap['unrealized_pnl']:,.2f} |",
        f"| 总费用 | {cap['total_fees']:,.2f} |",
        "",
        "## 2. 收益 vs 沪深300",
        "",
        "| 指标 | 策略 | 沪深300 |",
        "|------|------|---------|",
        f"| 累计收益 | {pct(cap['total_return'])} | {pct(ret['bench_total_return'])} |",
        f"| 年化收益 | {pct(ret['ann_return'])} | {pct(ret['bench_ann_return'])} |",
        f"| 年化波动 | {pct(risk['ann_vol'])} | {pct(risk['bench_ann_vol'])} |",
        f"| Sharpe | {num(risk['sharpe'])} | {num(risk['bench_sharpe'])} |",
        f"| Sortino | {num(risk['sortino'])} | — |",
        f"| Calmar | {num(risk['calmar'])} | — |",
        f"| 最大回撤 | {pct(risk['max_drawdown'])} | {pct(risk['bench_max_drawdown'])} |",
        f"| 最长回撤持续（交易日） | {risk['max_dd_duration_days']} | — |",
        "",
        "## 3. 相对基准",
        "",
        "| 指标 | 数值 |",
        "|------|------|",
        f"| 净值差（策略NAV−基准NAV） | {num(ret['excess_total'], 3)} |",
        f"| 超额年化（超额净值） | {pct(ret['excess_ann'])} |",
        f"| 跟踪误差 TE | {pct(rel['tracking_error'])} |",
        f"| 信息比率 IR | {num(rel['information_ratio'])} |",
        f"| Beta | {num(rel['beta'])} |",
        f"| Alpha 年化 | {pct(rel['alpha_ann'])} |",
        f"| 相关 | {num(rel['corr'])} |",
        f"| 日超额胜率 | {pct(rel['excess_win_rate'])} |",
        f"| 超额最大回撤 | {pct(rel['excess_max_drawdown'])} |",
        "",
        "## 4. 交易与持仓",
        "",
        "| 指标 | 数值 |",
        "|------|------|",
        f"| 买入 / 卖出笔数 | {tr['n_buys']} / {tr['n_sells']} |",
        f"| 完整开平次数 | {tr['n_round_trips']} |",
        f"| 胜率 | {pct(tr['win_rate'])} |",
        f"| 平均盈利 / 平均亏损 | {num(tr['avg_win'], 2)} / {num(tr['avg_loss'], 2)} |",
        f"| 盈亏比 | {num(tr['payoff_ratio'])} |",
        f"| 盈利因子 | {num(tr['profit_factor'])} |",
        f"| 平均 / 中位持仓天数 | {num(tr['avg_hold_days'], 1)} / {num(tr['median_hold_days'], 1)} |",
        f"| 年化换手（双边额/均权益） | {num(tr['turnover_ann'], 2)} |",
        f"| 日均 / 最大持股数 | {num(tr['avg_n_positions'], 1)} / {tr['max_n_positions']} |",
        f"| 日均现金占比 | {pct(tr['avg_cash_ratio'])} |",
        f"| 日均仓位暴露 | {pct(tr['exposure_avg'])} |",
        "",
        "## 5. 分年",
        "",
        "| 年份 | 策略 | 沪深300 | 超额 |",
        "|------|------|---------|------|",
    ]
    for row in metrics["yearly"]:
        lines.append(
            f"| {row['year']} | {pct(row['strategy'])} | {pct(row['benchmark'])} | {pct(row['excess'])} |"
        )
    if getattr(params, "enable_realistic_costs", False):
        exec_note = (
            "成交假设：T+1 开盘、100 股整数手；费用为佣金"
            f"（{params.commission_bps:g}bp/边，下限 {params.min_commission:g} 元）"
            f"+ 过户 {params.transfer_fee_bps:g}bp "
            f"+ 卖出印花税 {params.stamp_tax_bps:g}bp"
        )
        if getattr(params, "enable_open_slippage", False):
            exec_note += (
                f"；开盘分档滑点 {params.slippage_bps_large:g}/"
                f"{params.slippage_bps_mid:g}/{params.slippage_bps_small:g} bp"
                "（大/中/小，按信号日市值·成交额）。"
            )
        else:
            exec_note += "；无开盘滑点。"
    else:
        exec_note = (
            f"成交假设：T+1 开盘、100 股整数手、统一单边 {params.cost_bps:g} bp（研究对照口径）。"
        )
    lines += [
        "",
        "## 6. 过程文件",
        "",
        "- `ledger_daily.csv` — 每日现金/市值/权益/盈亏",
        "- `trades.csv` — 每笔买入卖出与实现盈亏",
        "- `PERFORMANCE.json` — 全量指标",
        "",
        "## 说明",
        "",
        "指标定义见 `docs/specs/metrics.md`；本金与流水见 `docs/specs/capital_ledger.md`。",
        exec_note,
        "",
    ]
    path = out / "PERFORMANCE.md"
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def write_report_bundle(
    port: dict,
    metrics: dict,
    params: Params,
    tag: str,
    out_dir: Path,
) -> Path:
    """Write PERFORMANCE + ledger + trades + equity into out_dir."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    port["daily"].to_csv(out_dir / "ledger_daily.csv", index=False)
    trades_out = port["trades"].copy()
    if len(trades_out) and "code" in trades_out.columns:
        trades_out["code"] = trades_out["code"].astype(str).str.zfill(6)
    trades_out.to_csv(out_dir / "trades.csv", index=False)
    metrics["merged_daily"].to_csv(out_dir / f"backtest_equity_{tag}.csv", index=False)
    return write_performance_hub(metrics, params, tag, out_dir=out_dir)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default=SAMPLE_START)
    ap.add_argument("--end", default=None)
    ap.add_argument("--rebuild", action="store_true")
    ap.add_argument("--panel", default=None)
    ap.add_argument("--capital", type=float, default=None, help="初始本金，默认 1000000")
    args = ap.parse_args()

    ensure_dirs()
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    params = DEFAULT_PARAMS
    if args.capital is not None:
        params = replace(params, initial_capital=float(args.capital))
    tag = f"{args.start}_{args.end or 'latest'}"

    if args.panel:
        panel = Path(args.panel)
    else:
        preferred = default_panel_path()
        panel = ROOT / "cache" / f"signals_{tag}.parquet"
        if (
            not args.rebuild
            and args.start == SAMPLE_START
            and args.end is None
            and preferred.exists()
        ):
            panel = preferred
        elif args.rebuild or not panel.exists():
            print("Building panel...")
            panel = build_signal_panel(start=args.start, end=args.end, params=params, out_path=panel)

    print(
        f"Backtest {panel} vs {BENCHMARK_NAME} | capital={params.initial_capital:,.0f}"
    )
    sig = load_signals(panel)
    sig["date"] = pd.to_datetime(sig["date"])
    sig = sig[sig["date"] >= pd.Timestamp(args.start)]
    if args.end:
        sig = sig[sig["date"] <= pd.Timestamp(args.end)]

    names = (
        sig.drop_duplicates("code")
        .set_index("code")["name"]
        .fillna("")
        .astype(str)
        .to_dict()
    )
    bars = load_bars(args.start, args.end)
    hs = load_hs300(args.start, args.end)
    port = run_portfolio(sig, bars, names=names, params=params, market=hs)
    if "error" in port:
        print(port)
        return

    metrics = compute_metrics(port["daily"], port["trades"], hs, params.initial_capital)
    if "error" in metrics:
        print(metrics)
        return

    hub = write_report_bundle(port, metrics, params, tag, REPORT_DIR)
    cap = metrics["capital"]
    risk = metrics["risk"]
    rel = metrics["relative"]
    tr = metrics["trading"]
    print("\n=== CAPITAL ===")
    print(f"  initial: {cap['initial_capital']:,.2f}")
    print(f"  ending:  {cap['ending_equity']:,.2f}")
    print(f"  pnl:     {cap['total_pnl']:,.2f} ({cap['total_return']:.2%})")
    print(f"  fees:    {cap['total_fees']:,.2f}")
    print("=== vs HS300 ===")
    print(f"  ann {metrics['returns']['ann_return']:.2%} vs {metrics['returns']['bench_ann_return']:.2%}")
    print(f"  maxDD {risk['max_drawdown']:.2%} | IR {rel['information_ratio']:.2f}")
    print("=== TRADES ===")
    print(f"  buys/sells {tr['n_buys']}/{tr['n_sells']} | win {tr['win_rate']:.2%} | turnover {tr['turnover_ann']:.2f}")
    print(f"Wrote {REPORT_DIR / 'ledger_daily.csv'}")
    print(f"Wrote {REPORT_DIR / 'trades.csv'}")
    print(f"Performance hub: {hub}")


if __name__ == "__main__":
    main()
