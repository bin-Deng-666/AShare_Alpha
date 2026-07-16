# rotating reports

`run_pipeline backtest` 在此生成真实历史费用，以及固定零成本成交路径上 20/40/80bp 往返成本压力下的净值、成交簿与 `PERFORMANCE.json`。Git 保留紧凑的 Markdown/JSON 汇总；根目录的规范化 `trades.csv` 和 `ledger_daily.csv` 可由本地缓存复现，并在冻结版本目录留存快照。

- `development/`：2021—2023，只用于模型和粗粒度组合参数选择；
- `holdout/`：2024—2026-05-22，打开后不再调参；
- `experiments/v2_endpoint/` 与 `experiments/v2_path/`：相同增强特征下的端点/路径标签开发期对照；
- `experiments/v2_selected_holdout/`：开发期选择冻结后的一次性增强版封存结果；
- 根目录：两段合并的诊断，不用于选模。

JSON 同时记录 `score_diagnostics`（相对实际五日端点收益）和 `model_target_diagnostics`（相对本次训练标签）；两者都包含 HAC(4)、逐年与五 cohort 统计。`score_specification` 保存完整特征列表、目标列、训练窗、最小样本和随机种子，并拒绝在同一评分根目录混写不同配置；场景诊断另存换手与期末未成交积压。

这些结果只用于研究，不构成未来收益承诺。
