# regime_sparse

双层量能择时壳 + 稀疏事件账本。基准沪深300；信号日终 → **T+1 开盘**成交。

活版本 / 冻结：**v0.1** → [`versions/v0.1/`](versions/v0.1/)

## 阅读顺序

1. [`docs/STRATEGY.md`](docs/STRATEGY.md)  
2. [`docs/specs/execution.md`](docs/specs/execution.md)  
3. [`docs/CHANGELOG.md`](docs/CHANGELOG.md)  
4. [`versions/v0.1/PERFORMANCE.md`](versions/v0.1/PERFORMANCE.md) 或 [`reports/PERFORMANCE.md`](reports/PERFORMANCE.md)  
5. [`../LESSONS.md`](../LESSONS.md)（可选）

## 回测

```bash
cd strategy/regime_sparse
python3 cli/rebuild_panel_cache.py
python3 cli/run_backtest.py
```

前置：`data/catalog/quant.duckdb`。通用工具：仓库根 `backtest/`。

## v0.1 表现

| 指标 | 数值 |
|------|------|
| 总收益 | +141.5% |
| 年化 | 14.5% |
| Sharpe | 1.05 |
| 最大回撤 | -14.9% |
| 买入 / 卖出 | 210 / 254 |

## 目录

```text
docs/  config/  features/  engine/  cli/  lib/
cache/  reports/  versions/v0.1/  experiments/
```

相对 `demo`：继承四层 + 账本 + 出场 + Risk-On；不搬 enrichment / 假牛 / MFE / B/C。

## 状态

| 项 | 状态 |
|----|------|
| v0.1 冻结 | [`versions/v0.1/`](versions/v0.1/) |
| adapter / 日荐 | 未注册 |
