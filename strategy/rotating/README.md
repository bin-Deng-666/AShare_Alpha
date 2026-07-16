# rotating

日频横截面轮动策略：固定因子、Elastic Net 与浅层 GBRT 三方赛马，五日分片持有，T+1 开盘执行。

当前状态：三个公共 seam 已按 TDD 实现；正式全市场赛马选择浅层 GBRT 作为默认研究信号。流水线按年构建 PIT 面板、按季度冻结模型，并使用 T+1 开盘与历史时变成本回测。

```powershell
# 首次按 data/README.md 构建最小 PIT 数据：
.venv\Scripts\python.exe data\scripts\build_dataset.py universe
.venv\Scripts\python.exe data\scripts\build_dataset.py bars
.venv\Scripts\python.exe data\scripts\build_dataset.py indicators
.venv\Scripts\python.exe data\scripts\build_dataset.py reference
.venv\Scripts\python.exe data\scripts\build_dataset.py reference-intervals
.venv\Scripts\python.exe data\scripts\build_dataset.py limit-prices
.venv\Scripts\python.exe data\scripts\build_dataset.py catalog

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

# 分别保存开发期与封存期报告：
.venv\Scripts\python.exe -m strategy.rotating.cli.run_pipeline backtest `
  --start 2021-01-01 --end 2023-12-31 `
  --report-root strategy\rotating\reports\development
.venv\Scripts\python.exe -m strategy.rotating.cli.run_pipeline backtest `
  --start 2024-01-01 --end 2026-05-22 `
  --report-root strategy\rotating\reports\holdout

# 统一预测入口：
.venv\Scripts\python.exe run.py rotating --date 2026-05-22
```

当前正式结果见 [`reports/development/PERFORMANCE.md`](reports/development/PERFORMANCE.md) 与 [`reports/holdout/PERFORMANCE.md`](reports/holdout/PERFORMANCE.md)。结果用于研究，不构成收益承诺；封存期打开后不再调参。

详见 [`docs/STRATEGY.md`](docs/STRATEGY.md)。
