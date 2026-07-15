# 策略效果指标体系（对沪深300）

本文件定义 **必须产出** 的指标；实现见 `backtest/metrics.py`（仓库根），汇总进 `reports/PERFORMANCE.*`。

口径约定：

- 基准：沪深300（`000300`），同日开盘→次日开盘（与组合执行一致）
- 资金：显式初始本金（见 [`capital_ledger.md`](capital_ledger.md)），净值 = 权益 / 初始本金
- 成本：实测默认用 A 股拆分费用（佣金+印花税+过户）与开盘分档滑点；归档版本复现用统一单边 `cost_bps`（见 [`capital_ledger.md`](capital_ledger.md)）
- 样本：主评估 2020-01-01 起

---

## A. 资金与盈亏（账本层）

| 指标 | 定义 |
|------|------|
| `initial_capital` | 回测起始可用现金 |
| `ending_equity` | 末日现金 + 持仓市值（收盘或约定计价） |
| `ending_cash` | 末日现金 |
| `ending_market_value` | 末日持仓市值 |
| `total_pnl` | `ending_equity - initial_capital` |
| `total_return` | `total_pnl / initial_capital` |
| `total_fees` | 全程手续费合计 |
| `realized_pnl` | 已平仓实现盈亏合计 |
| `unrealized_pnl` | 末日浮动盈亏 |

---

## B. 收益（相对与绝对）

| 指标 | 定义 |
|------|------|
| `ann_return` / CAGR | 按区间年化的权益收益 |
| `bench_total_return` | 同期沪深300总收益（同本金假想买入指数） |
| `bench_ann_return` | 沪深300年化 |
| `excess_total` | 策略总收益 − 基准总收益（简单差） |
| `excess_ann` | 年化超额（对日超额复利净值年化） |
| `monthly_returns` / `yearly_returns` | 分月、分年收益与超额 |

---

## C. 风险

| 指标 | 定义 |
|------|------|
| `ann_vol` | 日收益标准差 × √242 |
| `downside_dev` | 负收益部分标准差 × √242 |
| `max_drawdown` | 权益峰值回撤最小值 |
| `max_dd_duration_days` | 最长水下期（峰→再创新高）交易日数 |
| `calmar` | `ann_return / \|max_drawdown\|` |
| `sortino` | `ann_return / downside_dev` |
| `bench_max_drawdown` | 基准最大回撤 |

---

## D. 相对基准（Alpha 质量）

| 指标 | 定义 |
|------|------|
| `beta` | 对沪深300日收益回归斜率 |
| `alpha_ann` | 回归截距 × 242（CAPM 风格） |
| `corr` | 与基准日收益相关 |
| `tracking_error` | 日超额收益标准差 × √242 |
| `information_ratio` | `excess_ann / tracking_error` |
| `excess_win_rate` | 日超额 > 0 的比例 |
| `excess_max_drawdown` | 超额净值曲线最大回撤 |

---

## E. 交易与持仓行为

| 指标 | 定义 |
|------|------|
| `n_buys` / `n_sells` | 买入 / 卖出笔数 |
| `n_round_trips` | 完整开平次数（按标的配对） |
| `win_rate` | 盈利 round-trip 占比 |
| `avg_win` / `avg_loss` | 单笔实现盈亏均值（亏为负） |
| `payoff_ratio` | `\|avg_win / avg_loss\|` |
| `profit_factor` | 盈利合计 / \|亏损合计\| |
| `avg_hold_days` / `median_hold_days` | 持仓天数 |
| `turnover_ann` | 年化换手：双边成交额 / 年均权益 / 年数 |
| `avg_n_positions` / `max_n_positions` | 日均 / 最大持股数 |
| `avg_cash_ratio` | 日均现金 / 权益 |
| `exposure_avg` | 日均市值 / 权益 |

---

## F. 过程记录（强制落盘）

每日至少：

- `date, cash, market_value, equity, nav, bench_nav, n_positions, daily_pnl, daily_ret`

每笔成交至少：

- `trade_id, date, side, code, name, shares, price, amount, fee, realized_pnl, reason, cash_after, position_shares`

详见 [`capital_ledger.md`](capital_ledger.md)。

---

## G. 报告呈现（PERFORMANCE 必含）

1. 本金与期末权益、总盈亏  
2. 策略 vs 沪深300：收益 / 年化 / 回撤 / Sharpe / Sortino / Calmar  
3. 超额：IR、TE、胜率、分年超额表  
4. 交易：笔数、胜率、盈亏比、换手、平均持仓  
5. 链接：日账本 CSV、成交流水 CSV  

**不做**为抬好看曲线而省略回撤、换手或交易成本。
