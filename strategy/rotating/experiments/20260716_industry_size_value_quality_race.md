# 2026-07-16 行业规模残差价值—质量特征赛马

## 预注册边界

本文件在读取任何候选结果前写入。研究依据和固定公式见 [`industry_adjusted_daily_features.md`](../../../docs/research/industry_adjusted_daily_features.md)。

- 基准 `B`：冻结 v0.2 的全部输入、五日端点区间标签和浅层 GBRT；
- 候选 `I`：`B` 加入 `ep_industry_size_residual`、`bp_industry_size_residual`、`roe_industry_size_residual` 三项固定 bundle；
- 三项必须整体接受或整体否决，不做用于选择的单项 ablation，不改变对数、缩尾、行业最小样本数或规模控制；
- 标签保持 `T+6 open / T+1 open - 1`，`label_available_date=T+6`；模型、随机种子、季度重训、三年训练窗、股票池、五日 sleeve、容量、成本和成交规则全部不变；
- 唯一选择区间为 2021-01-01—2023-12-31，物理面板与标签读取上限为 2023-12-31；2024-01-01 以后的旧留出和 2026 前向数据不得读取、汇总或用于打破平局。

候选面板从冻结的 `v2_dev_panel` 逐年增加三项当日横截面变换，不能重新拉取或重建历史源数据。增强前后共同 `(code, date)` 键、既有模型输入、目标和 `label_available_date` 必须逐项一致；否则先解释漂移并停止赛马。

## 固定特征口径

对当日合格股票的正 EP、正 BP 分别取对数；估值隐含 ROE 固定为当日 `pb_mrq / pe_ttm`。每项先做当日 1%/99% 缩尾，剔除 `UNKNOWN` 和当日有效样本少于 5 只的行业，再对无全局截距的行业 one-hot 与标准化 `log(float_market_cap)` 做最小二乘。残差再次按日缩尾并排名到 `[-1, 1]`。缺失保持缺失，模型仍按冻结规则将输入缺失填 0，并保留原有 missing indicator。

## 固定通过门槛

候选只有同时满足以下全部条件才算开发期胜出：

1. 2021—2023 全期历史费用口径的净年化收益和 Sharpe 都严格高于 `B`；
2. 三个日历年度中至少两个年度的净收益和 Sharpe 同时严格高于 `B`；
3. 固定 20bp 往返路径压力下的净年化收益和 Sharpe 都严格高于 `B`；
4. 全期最大回撤不得比 `B` 恶化超过 2 个百分点，年化换手不得增加超过 25%；
5. 对实际五日端点收益的平均 Rank IC 不得比 `B` 下降超过 0.002，五个不重叠 cohort 的候选 IC 均须为正；
6. `I-B` 配对日收益均值必须为正；同时报告 HAC(4) t，但不以某个显著性阈值追加调参。

任一条件失败、结果分裂或数据覆盖不足时保留 v0.2。即使全部通过，也只能登记为 `development_winner_pending_fresh_forward_validation`；旧 2024+ 留出已经被先前研究使用，不能再次充当未见样本，默认策略不会在本实验中自动切换。

## 必报诊断

- 三项覆盖率、每日有效股票数、有效行业数和小行业剔除数；
- 新列与全部既有输入的开发期 Spearman 相关，及三项相关矩阵；
- 逐年与全期 Rank IC、HAC(4)、五个不重叠 cohort；
- 历史费用和 20/40/80bp 压力的收益、Sharpe、最大回撤、换手、未成交积压；
- 三项残差在每个日期—行业内的均值，以及与当日 `log(float_market_cap)` 的正交误差；
- 明确记录成功或失败，不因结果不佳拆包、换方向或读取 2024 年以后数据。

## 固定复现命令与输入指纹

以下命令是本实验唯一复现路径；增量命令把日期过滤下推到 Parquet 读取，且输出目录不能与冻结输入相同。

```powershell
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
.venv\Scripts\python.exe -m strategy.rotating.experiments.build_industry_size_diagnostics `
  --frozen-panel-root strategy\rotating\cache\experiments\v2_dev_panel `
  --candidate-panel-root strategy\rotating\cache\experiments\v3_industry_size_dev_panel `
  --baseline-score-root strategy\rotating\cache\experiments\v2_endpoint_scores `
  --candidate-score-root strategy\rotating\cache\experiments\v3_industry_size_scores `
  --baseline-report-root strategy\rotating\reports\experiments\v2_endpoint `
  --candidate-report-root strategy\rotating\reports\experiments\v3_industry_size_dev `
  --output strategy\rotating\reports\experiments\v3_industry_size_dev\FEATURE_DIAGNOSTICS.json `
  --start 2021-01-01 --end 2023-12-31
```

冻结输入的六个 `panel.parquet` SHA-256（2018 至 2023）依次为：

```text
02fe5d8b8bba92af5e078dcab332b378f97dbe14a30bda25649c54fa86e50de3
be1788534a737f560dcd0fb8c3956bdd70c57519795272396de4440076aee9a0
975c2b085e6d9e240d8ae42d6932489b6cb8460a57daa6a03bf34d67d23e5523
a2a96965de557a3a454466744973dff378e820445eab60623161956eff15d9c1
9d4503e335e518bf4e2efe537161b1d466837e4a2f0915d3b522c1abc6ed0071
14ec6ebb5854853585ba8a53f7d024368f759a265c58b0ba5a1154c712cdfc6f
```

## 结果

### 冻结身份与覆盖

候选从 `v2_dev_panel` 的 2018—2023 六个物理分区逐年派生，共 6,060,964 行。增强前后每年行数、`(code, date)` 唯一键和全部 112 个既有字段的行哈希完全一致，单边键和既有字段差异均为 0；只新增三项残差及其三项排名。候选评分有 1,751,687 个唯一键，日期为 2021-01-04—2023-12-29；`train_max_label_available_date > date` 为 0。候选与冻结基准的评分键、实际五日端点标签、标签可得日和固定因子分数逐项一致。

| 开发期合格行覆盖 | EP 残差 | BP 残差 | 隐含 ROE 残差 |
|---|---:|---:|---:|
| 有效行 / 1,751,687 | 1,424,610 | 1,713,418 | 1,424,260 |
| 覆盖率 | 81.33% | 97.82% | 81.31% |
| 每日有效股票数（最小 / 中位 / 最大） | 1,469 / 1,983 / 2,341 | 1,707 / 2,424 / 2,822 | 1,469 / 1,982 / 2,339 |
| 每日有效行业数（最小 / 中位 / 最大） | 46 / 53 / 61 | 50 / 60 / 66 | 46 / 53 / 61 |
| 小行业剔除行数 | 38,494 | 31,010 | 38,494 |

原始残差在所有日期—行业组内的最大绝对均值为 `1.53e-15 / 1.05e-15 / 1.14e-16`；逐日与 `log(float_market_cap)` 的最大绝对内积为 `1.30e-11 / 1.39e-11 / 1.47e-12`，符合固定中性化口径。

### 重复性与模型稳定性诊断

对已按日排名的输入逐日计算横截面 Spearman 再取日期均值，新三项与对应既有排名分别为 0.8279、0.8326、0.8315；六个原始/残差价值—质量排名的合并相关矩阵条件数为 193.04，新三项 VIF 分别为 12.67、11.26、8.71。新三项内部的非对角相关为 EP/BP 0.4952、EP/ROE 0.5442、BP/ROE -0.3271。完整的“3 个新特征 × 35 个既有输入”矩阵、3×3 矩阵和六特征矩阵见 [`FEATURE_DIAGNOSTICS.json`](../reports/experiments/v3_industry_size_dev/FEATURE_DIAGNOSTICS.json)。

加入 bundle 前后，Elastic Net 分数的日均横截面 Spearman 为 0.9971、Top-25 日均重合率为 87.61%；GBRT 分别为 0.9909 和 71.37%。这说明总体排序很稳定，但浅层 GBRT 的头部持仓仍有足够变化，不能仅凭高全局相关跳过真实组合回测。

### 统计与经济结果

| GBRT 全期 | 基准 `B` | 候选 `I` | 候选减基准 |
|---|---:|---:|---:|
| 历史费用年化 | -0.98% | -4.82% | -3.83pp |
| 历史费用 Sharpe | -0.064 | -0.314 | -0.250 |
| 最大回撤 | -25.16% | -26.14% | -0.98pp |
| 年化换手 | 9.85 | 8.78 | -10.89% |
| 20bp 压力年化 / Sharpe | -2.80% / -0.176 | -5.32% / -0.339 | -2.52pp / -0.163 |
| 40bp 压力年化 / Sharpe | -3.83% / -0.238 | -6.26% / -0.393 | -2.42pp / -0.156 |
| 80bp 压力年化 / Sharpe | -5.98% / -0.358 | -8.20% / -0.501 | -2.23pp / -0.143 |
| 平均 Rank IC | 0.08719 | 0.08763 | +0.00044 |
| Rank IC HAC(4) t | 14.15 | 14.17 | +0.02 |

| 年度 | 基准收益 / Sharpe | 候选收益 / Sharpe | 候选是否双胜 |
|---|---:|---:|---:|
| 2021 | **5.77% / 0.394** | -0.03% / -0.002 | 否 |
| 2022 | **-7.90% / -0.418** | -8.08% / -0.439 | 否 |
| 2023 | **-0.32% / -0.028** | -6.08% / -0.556 | 否 |

候选五个不重叠 cohort 的平均 IC 为 0.08826 / 0.08780 / 0.08639 / 0.08826 / 0.08742，均为正。候选减基准的配对日收益均值为 `-1.62e-4`，简单年化为 -3.92 个百分点，HAC(4) t 为 -1.18。最大行业已实现绝对损益占比从 3.72% 降至 3.68%，微盘占比从 9.57% 升至 10.59%；两者期末未成交积压均为 0。

### 决策

候选通过了 IC 容忍、五个 cohort 为正、回撤不恶化超过 2pp 和换手约束；但没有通过全期收益/Sharpe、年度双胜、20bp 压力或配对日收益门槛，三个年度均未双胜。固定三项 bundle 因此整体否决，默认继续保持 v0.2 的五日区间标签、八项增强特征和浅层 GBRT。

没有运行单项 ablation，没有改变残差定义，也没有读取 2024 年以后结果寻找翻盘。平均 IC 的微小提升没有转化成更好的可交易组合收益，且高相关/VIF 表明新增投影主要重复已有价值—质量信息。`PERFORMANCE.json` 与 `FEATURE_DIAGNOSTICS.json` 均把本实验状态记录为 `rejected_on_development`。
