# backtest/

策略无关的通用回测工具。任何策略都可以直接导入，不绑定特定 params 类或面板格式。

## 模块

### metrics.py

`compute_metrics(daily, trades, bench, initial_capital)` — 一次性产出全部指标。

输入约定：
- `daily`：DataFrame，必须含 `date` / `equity` / `nav` / `daily_ret` / `cash` / `market_value` / `n_positions` / `cash_ratio` / `daily_pnl`
- `trades`：DataFrame，必须含 `side` / `amount` / `fee` / `realized_pnl` / `hold_days`
- `bench`：DataFrame，必须含 `date` / `ret`（基准日收益率）

输出 dict 结构：

| 分组 | 字段 |
|------|------|
| `capital` | initial_capital / ending_equity / total_pnl / total_return / total_fees / realized_pnl / unrealized_pnl |
| `returns` | ann_return / bench_total_return / bench_ann_return / excess_ann / excess_nav_total |
| `risk` | ann_vol / downside_dev / max_drawdown / max_dd_duration / calmar / sortino / sharpe / bench_* |
| `relative` | beta / alpha_ann / corr / tracking_error / information_ratio / excess_win_rate / excess_max_drawdown |
| `trading` | n_buys / n_sells / win_rate / avg_win / avg_loss / payoff_ratio / profit_factor / avg_hold_days / turnover_ann / avg_n_positions / avg_cash_ratio |
| `meta` | start / end / days / n_years / benchmark |
| `yearly` | 逐年 strategy / benchmark / excess 收益率列表 |
| `nav_monthly` | 月末 NAV 序列 |

### costs.py

A 股执行成本计算，通过鸭子类型接受任何 params 对象。

```python
from backtest.costs import trade_fee, apply_slippage, slippage_bps_for_row, lot_floor

# 买入费用（佣金 + 过户费）
fee = trade_fee(notional, "BUY", params)

# 卖出费用（佣金 + 过户费 + 印花税）
fee = trade_fee(notional, "SELL", params)

# 信号日流动性 → 开盘滑点 bps
slip_bps = slippage_bps_for_row(signal_row, params)

# 应用滑点得到实际成交价
fill_price = apply_slippage(open_price, "BUY", slip_bps)

# A 股 100 股取整
shares = lot_floor(raw_shares, 100)
```

params 对象需要具备的属性（当对应功能启用时）：

| 属性 | 类型 | 用途 |
|------|------|------|
| `enable_realistic_costs` | bool | False 时退化为 flat fee |
| `cost_bps` | float | flat 模式费率 |
| `commission_bps` | float | 佣金费率（bp） |
| `min_commission` | float | 佣金下限（元） |
| `transfer_fee_bps` | float | 过户费率（bp） |
| `stamp_tax_bps` | float | 印花税率（bp，仅卖出） |
| `enable_open_slippage` | bool | 是否启用分档滑点 |
| `slippage_bps_large/mid/small` | float | 大/中/小盘滑点（bp） |
| `slippage_mcap_large/mid` | float | 市值分档阈值（亿） |
| `slippage_amt_large/mid` | float | 成交额分档阈值（元，回退） |

## 与策略引擎的关系

```
backtest/                    ← 通用层（本目录）
├── metrics.py               ← 任何策略: from backtest.metrics import compute_metrics
└── costs.py                 ← 任何策略: from backtest.costs import trade_fee

strategy/<name>/engine/      ← 策略层（各策略自有）
├── portfolio.py             ← 组合回测循环（调用 backtest.costs）
├── entry.py                 ← 入场候选 enrichment
└── walkforward.py           ← 滚动 OOS（调用 backtest.metrics）
```

策略引擎负责信号处理、仓位管理、出场逻辑；通用层负责指标计算和费用扣减。两者通过 DataFrame 和鸭子类型 params 解耦，不共享任何 import。
