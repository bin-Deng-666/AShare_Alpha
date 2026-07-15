# 本金与成交账本

## 1. 当前结论：不是 10000

v1 早期回测用的是 **净值从 1 起步的单位账户**，**没有**设定 10000 元本金，也没有逐笔买入/卖出金额。

## 2. 默认本金

| 项 | 值 | 说明 |
|----|-----|------|
| `initial_capital` | **1,000,000 元**（默认） | 约可支撑 top10、A 股 100 股整数手 |
| 可配置 | `Params.initial_capital` / CLI `--capital` | 例如改成 `100000` 做小资金压力 |

**不建议用 10000 做正式回测：** 10 只股票、每手 100 股时，单票价格稍高就会买不起或严重偏离等权，指标失真。若坚持 10000，仅作「小资金可交易性」实验，须在报告标注。

## 3. 执行假设

| 项 | 约定 |
|----|------|
| 信号日 | T 日收盘后产出 |
| 成交 | **T+1 开盘价** |
| 手数 | 买入向下取整到 100 股；卖出全部可用股数 |
| 目标权重 | 等权：`equity × position_mult / top_n`（受现金约束） |
| 卖出触发 | `reduce` / `avoid` / 超过 `max_hold_days` / 不再在目标池且需腾挪 |
| 费用 | **实测默认**：佣金 `commission_bps`（双边，下限 `min_commission`）+ 卖出印花税 `stamp_tax_bps` + 过户费 `transfer_fee_bps`；`enable_realistic_costs=False` 时回退到单边 `cost_bps`（归档版本复现用） |
| 开盘滑点 | **实测默认 ON**：按信号日流通市值/成交额分大中小档（`slippage_bps_*`），买入加价、卖出减价；滑点在 T 日挂单时锁定，T+1 开盘成交，无前视 |

## 4. 落盘文件

| 文件 | 内容 |
|------|------|
| `reports/ledger_daily.csv` | 每日现金、市值、权益、净值、基准净值、持仓数、当日盈亏 |
| `reports/trades.csv` | 每笔买卖流水 |
| `reports/PERFORMANCE.md` | 汇总指标（引用本金与流水） |

## 5. 单笔流水字段

`trade_id, date, side, code, name, shares, price, amount, fee, realized_pnl, reason, cash_after, position_shares, hold_days, slip_bps`

- `side`: `BUY` / `SELL`  
- `realized_pnl`: 仅卖出填写（卖出所得 − 成本摊销 − 卖出费）  
- `reason`: `open_A`（v1.0 默认）/ `open_B`/`open_C`（默认关）/ `reduce` / `avoid` / `max_hold` / `rebalance` 等
- `slip_bps`: 该笔使用的开盘不利滑点（bp）；未启用时为 0  
