# 规格：成交与防前视（T+1）

实现：`engine/portfolio.py`。

## 1. 时间线

```text
信号日 T（收盘后）
  ├─ 面板特征只用到 date≤T
  ├─ close[T] MTM
  ├─ signal[T] + close[T] 决定卖/买
  └─ 写入 pending（当日不成交）

交易日 T+1（下一信号日 / 下一 bar 日）
  ├─ open[T+1] 成交挂单（先卖后买）
  ├─ close[T+1] MTM
  └─ 再决定下一轮 pending
```

末信号日挂单：若 bars 上还有更晚开盘，循环外 flush（`flush_t1`）。

## 2. 硬约束

| 规则 | 做法 |
|------|------|
| 信号日不成交 | 当日只 enqueue |
| 成交价 | 一律 `open` |
| 滑点 | 信号日流动性锁定 `slip_bps` |
| 涨跌停 | 涨停不买、跌停不卖 |
| 入场价 | `entry_price` = 成交开盘；不用成交日 low/high/ATR |
| 面板 | 仅 base `build_panel`；无公告+1 / 北向 enrichment |
| 基准 | HS300 close-to-close，对齐权益 MTM |
| 审计 | `trades.signal_date` < `trades.date` |

## 3. 已从引擎移除（勿再假定存在）

假牛、mainline、scarcity、earnings/北向/指数分档、MFE、ATR/结构止损、金字塔、R-scale、vol targeting、open_B/C。

## 4. 自检

- [ ] 全部成交 `signal_date` < `date`
- [ ] 无同日决策同日开盘成交
- [ ] 无 earnings `date+1` 类 join
