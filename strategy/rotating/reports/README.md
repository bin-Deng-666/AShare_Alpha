# rotating reports

`run_pipeline backtest` 在此生成真实历史费用，以及固定零成本成交路径上 20/40/80bp 往返成本压力下的净值、成交簿与 `PERFORMANCE.json`。Git 保留紧凑的 Markdown/JSON 汇总；根目录的规范化 `trades.csv` 和 `ledger_daily.csv` 可由本地缓存复现，并在冻结版本目录留存快照。

- `development/`：2021—2023，只用于模型和粗粒度组合参数选择；
- `holdout/`：2024—2026-05-22，打开后不再调参；
- 根目录：两段合并的诊断，不用于选模。

这些结果只用于研究，不构成未来收益承诺。
