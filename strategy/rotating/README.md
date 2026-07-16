# rotating

日频横截面轮动策略：固定因子、Elastic Net 与浅层 GBRT 三方赛马，五日分片持有，T+1 开盘执行。

当前状态：三个公共 seam 已按 TDD 实现；正式全市场赛马选择浅层 GBRT 作为默认研究信号。v0.2 使用 `T+6 open / T+1 open - 1` 的五日端点区间标签，而不是下一日涨幅，并使用八项日线增强特征。路径均值标签参与了同口径开发期比较，但没有通过跨年度稳定性与回撤门槛。后续五日涨跌停状态 bundle 通过全部开发数据与表现门槛，登记为等待全新前向验证的开发集胜者；由于历史成本和 20bp 口径的绝对收益仍为负，它不自动替换默认 v0.2。流水线按年构建 PIT 面板、按季度冻结模型，并使用 T+1 开盘、历史时变成本和公司行为感知执行回测。

```powershell
# 首次按 data/README.md 构建最小 PIT 数据：
.venv\Scripts\python.exe data\scripts\build_dataset.py universe
.venv\Scripts\python.exe data\scripts\build_dataset.py bars
.venv\Scripts\python.exe data\scripts\build_dataset.py indicators
.venv\Scripts\python.exe data\scripts\build_dataset.py reference
.venv\Scripts\python.exe data\scripts\build_dataset.py reference-intervals
.venv\Scripts\python.exe data\scripts\build_dataset.py corporate-action-events --force --data-cutoff 2023-12-31 --workers 4
.venv\Scripts\python.exe data\scripts\build_dataset.py qfq-corporate-actions --force --data-cutoff 2023-12-31
.venv\Scripts\python.exe data\scripts\build_dataset.py limit-prices --force --data-cutoff 2023-12-31
.venv\Scripts\python.exe data\scripts\build_dataset.py catalog --data-cutoff 2023-12-31

# BaoStock 登录不可用时，可下载 SimTradeData 的公开 CN release，解压其中
# valuation、metadata 和 manifest 后统一覆盖指标口径：
New-Item -ItemType Directory -Force data\raw\simtradelab\cn | Out-Null
curl.exe -L --fail `
  https://github.com/kay-ou/SimTradeData/releases/download/data-cn-2026-05-26/simtradelab-data-cn-2026-05-26.tar.gz `
  -o data\raw\simtradelab\simtradelab-data-cn-2026-05-26.tar.gz
tar -xzf data\raw\simtradelab\simtradelab-data-cn-2026-05-26.tar.gz `
  -C data\raw\simtradelab\cn ./valuation ./metadata ./manifest.json
.venv\Scripts\python.exe data\scripts\build_dataset.py import-simtradedata-indicators `
  --source-root data\raw\simtradelab\cn --force

# 自动补三年训练面板，生成日度分数和全区间报告：
.venv\Scripts\python.exe -m strategy.rotating.cli.run_pipeline all `
  --start 2021-01-01 --end 2026-05-22

# 严格复现开发期标签赛马（物理禁止读取 2024 行情）：
.venv\Scripts\python.exe -m strategy.rotating.cli.run_pipeline build-panel `
  --start 2018-01-01 --end 2023-12-31 --data-cutoff 2023-12-31 `
  --panel-root strategy\rotating\cache\experiments\v2_dev_panel
.venv\Scripts\python.exe -m strategy.rotating.cli.run_pipeline score `
  --start 2021-01-01 --end 2023-12-31 --feature-set enhanced `
  --target-column target_rank `
  --panel-root strategy\rotating\cache\experiments\v2_dev_panel `
  --score-root strategy\rotating\cache\experiments\v2_endpoint_scores
.venv\Scripts\python.exe -m strategy.rotating.cli.run_pipeline score `
  --start 2021-01-01 --end 2023-12-31 --feature-set enhanced `
  --target-column target_path_rank `
  --panel-root strategy\rotating\cache\experiments\v2_dev_panel `
  --score-root strategy\rotating\cache\experiments\v2_path_scores
.venv\Scripts\python.exe -m strategy.rotating.cli.run_pipeline backtest `
  --start 2021-01-01 --end 2023-12-31 `
  --score-root strategy\rotating\cache\experiments\v2_endpoint_scores `
  --report-root strategy\rotating\reports\experiments\v2_endpoint
.venv\Scripts\python.exe -m strategy.rotating.cli.run_pipeline backtest `
  --start 2021-01-01 --end 2023-12-31 `
  --score-root strategy\rotating\cache\experiments\v2_path_scores `
  --report-root strategy\rotating\reports\experiments\v2_path

# v0.2 冻结后的唯一固定市场门控候选（开发期失败，不进入默认策略）：
.venv\Scripts\python.exe -m strategy.rotating.cli.run_pipeline backtest `
  --start 2021-01-01 --end 2023-12-31 `
  --score-root strategy\rotating\cache\experiments\v2_endpoint_scores `
  --report-root strategy\rotating\reports\experiments\v3_market_gate `
  --market-gate-candidate

# 从冻结 v0.2 面板复现行业/规模残差 bundle（开发期失败，不进入默认策略）：
.venv\Scripts\python.exe -m strategy.rotating.cli.run_pipeline augment-frozen-panel `
  --start 2018-01-01 --end 2023-12-31 `
  --frozen-panel-root strategy\rotating\cache\experiments\v2_dev_panel `
  --panel-root strategy\rotating\cache\experiments\v3_industry_size_dev_panel `
  --feature-set industry_size
.venv\Scripts\python.exe -m strategy.rotating.cli.run_pipeline score `
  --start 2021-01-01 --end 2023-12-31 --feature-set industry_size `
  --target-column target_rank `
  --panel-root strategy\rotating\cache\experiments\v3_industry_size_dev_panel `
  --score-root strategy\rotating\cache\experiments\v3_industry_size_scores
.venv\Scripts\python.exe -m strategy.rotating.cli.run_pipeline backtest `
  --start 2021-01-01 --end 2023-12-31 --feature-set industry_size `
  --score-root strategy\rotating\cache\experiments\v3_industry_size_scores `
  --report-root strategy\rotating\reports\experiments\v3_industry_size_dev `
  --experiment-decision rejected_on_development

# 五日涨跌停状态 bundle（开发期胜出，待全新前向验证，不进入默认策略）：
.venv\Scripts\python.exe data\scripts\build_dataset.py corporate-action-events --force --data-cutoff 2023-12-31 --workers 4
.venv\Scripts\python.exe data\scripts\build_dataset.py qfq-corporate-actions --force --data-cutoff 2023-12-31
.venv\Scripts\python.exe data\scripts\build_dataset.py limit-prices --force --data-cutoff 2023-12-31
.venv\Scripts\python.exe data\scripts\build_dataset.py catalog
.venv\Scripts\python.exe -m strategy.rotating.cli.run_pipeline build-panel `
  --start 2018-01-01 --end 2023-12-31 --data-cutoff 2023-12-31 `
  --panel-root strategy\rotating\cache\experiments\v4_corrected_limit_dev_panel
.venv\Scripts\python.exe -m strategy.rotating.cli.run_pipeline augment-price-limit-panel `
  --start 2018-01-01 --end 2023-12-31 --feature-set price_limit `
  --frozen-panel-root strategy\rotating\cache\experiments\v4_corrected_limit_dev_panel `
  --panel-root strategy\rotating\cache\experiments\v4_price_limit_dev_panel
.venv\Scripts\python.exe -m strategy.rotating.cli.run_pipeline score `
  --start 2021-01-01 --end 2023-12-31 --feature-set enhanced `
  --panel-root strategy\rotating\cache\experiments\v4_corrected_limit_dev_panel `
  --score-root strategy\rotating\cache\experiments\v4_corrected_limit_baseline_scores
.venv\Scripts\python.exe -m strategy.rotating.cli.run_pipeline score `
  --start 2021-01-01 --end 2023-12-31 --feature-set price_limit `
  --panel-root strategy\rotating\cache\experiments\v4_price_limit_dev_panel `
  --score-root strategy\rotating\cache\experiments\v4_price_limit_scores
.venv\Scripts\python.exe -m strategy.rotating.cli.run_pipeline backtest `
  --start 2021-01-01 --end 2023-12-31 --feature-set enhanced `
  --score-root strategy\rotating\cache\experiments\v4_corrected_limit_baseline_scores `
  --report-root strategy\rotating\reports\experiments\v4_corrected_limit_baseline_dev
.venv\Scripts\python.exe -m strategy.rotating.cli.run_pipeline backtest `
  --start 2021-01-01 --end 2023-12-31 --feature-set price_limit `
  --score-root strategy\rotating\cache\experiments\v4_price_limit_scores `
  --report-root strategy\rotating\reports\experiments\v4_price_limit_dev `
  --experiment-decision development_winner_pending_fresh_forward_validation

# 只在上述开发期结果冻结端点标签后，构建并评分一次留出期：
.venv\Scripts\python.exe -m strategy.rotating.cli.run_pipeline build-panel `
  --start 2021-01-01 --end 2026-05-22 --data-cutoff 2026-05-22 `
  --panel-root strategy\rotating\cache\experiments\v2_holdout_panel
Copy-Item -Recurse `
  strategy\rotating\cache\experiments\v2_endpoint_scores `
  strategy\rotating\cache\experiments\v2_selected_endpoint_scores
.venv\Scripts\python.exe -m strategy.rotating.cli.run_pipeline score `
  --start 2024-01-01 --end 2026-05-22 --feature-set enhanced `
  --target-column target_rank `
  --panel-root strategy\rotating\cache\experiments\v2_holdout_panel `
  --score-root strategy\rotating\cache\experiments\v2_selected_endpoint_scores
.venv\Scripts\python.exe -m strategy.rotating.cli.run_pipeline backtest `
  --start 2024-01-01 --end 2026-05-22 `
  --score-root strategy\rotating\cache\experiments\v2_selected_endpoint_scores `
  --report-root strategy\rotating\reports\experiments\v2_selected_holdout

# 统一预测入口：
.venv\Scripts\python.exe run.py rotating --date 2026-05-22
```

当前正式结果见 [`reports/development/PERFORMANCE.md`](reports/development/PERFORMANCE.md) 与 [`reports/holdout/PERFORMANCE.md`](reports/holdout/PERFORMANCE.md)，标签/特征选择过程见 [`experiments/20260716_interval_label_feature_race.md`](experiments/20260716_interval_label_feature_race.md)。后续固定市场门控和行业规模残差价值—质量 bundle 均在开发期失败；五日涨跌停状态 bundle 则只成为等待全新前向验证的开发集胜者，详见 [`experiments/20260716_market_gate_feature_race.md`](experiments/20260716_market_gate_feature_race.md)、[`experiments/20260716_industry_size_value_quality_race.md`](experiments/20260716_industry_size_value_quality_race.md) 与 [`experiments/20260717_price_limit_state_feature_race.md`](experiments/20260717_price_limit_state_feature_race.md)。结果用于研究，不构成收益承诺；既有封存期已打开，不能用于继续调参。

详见 [`docs/STRATEGY.md`](docs/STRATEGY.md)。
