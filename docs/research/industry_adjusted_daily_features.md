# A 股五日区间收益：行业与规模调整的价值—质量特征

研究日期：2026-07-16。本文是事前特征定义，不读取或引用仓库中的 2024 年以后留出期、2026 年前向期结果。公开开发选择只允许使用 2021-01-01 至 2023-12-31，且行情与标签物理读取上限为 2023-12-31。

## 唯一建议

下一轮只登记一个不可拆分的三项 bundle：

1. `ep_industry_size_residual`；
2. `bp_industry_size_residual`；
3. `roe_industry_size_residual`。

三项分别把已有 EP、BP、ROE 变成信号日横截面内的“同业、同规模相对值”。它们不会增加新的原始财务数据，而是增加当前模型无法从单只股票的全市场排名中恢复的条件信息。三项必须一起与当前 v0.2 比较，不能在看到开发结果后挑单项、改窗口或改中性化方法。

不建议加入 `mom_20_5` 的行业调整版。中国一手证据更支持反转而非传统动量；美国的行业调整动量证据和 A 股月频机器学习结果都不能直接证明 15 日跳过最近 5 日的动量能预测 `T+1 open -> T+6 open`。当前模型也已经含有原始 `mom_20_5`，新增残差版的边际依据弱于价值—质量三项。

## 固定公式

信号日为 `T`。先按当前 v0.2 的 PIT 股票池确定当日合格样本，再计算横截面变换。原始量定义为：

```text
x_ep(i,T)  = log(ep(i,T)),  ep > 0
x_bp(i,T)  = log(bp(i,T)),  bp > 0
x_roe(i,T) = implied_roe(i,T) = pb_mrq(i,T) / pe_ttm(i,T)
size(i,T)  = log(float_market_cap(i,T))
```

每个 `x` 单独执行以下固定步骤：

1. 只对当日非缺失值做 1%/99% 横截面缩尾；
2. 行业使用 `T` 日可得的历史行业区间；行业未知或该行业当日有效样本少于 5 只时，该特征为空；
3. 在有效样本上把 `size` 标准化为零均值、单位标准差；
4. 用无全局截距的行业 one-hot 和标准化规模做当日横截面最小二乘；
5. 将回归残差缩尾后排名到 `[-1, 1]`，作为模型输入。

公式为：

```text
X_T = [industry_one_hot_T, zscore(size_T)]
beta_x,T = argmin_beta || winsor(x_T) - X_T beta ||^2
resid_x,T = winsor(x_T) - X_T beta_x,T
feature_x,T = rank_cs(winsor(resid_x,T), range=[-1,1])
```

行业 one-hot 不另加全局截距，避免完全共线。若当日有效 `size` 没有横截面方差，则只做行业调整。缺失 EP/BP/ROE 保持为空，继续使用现有 `ep_missing / bp_missing / roe_missing` 控制列；禁止把缺失值当作最便宜或最低盈利。

选择联合行业与 `log(float_market_cap)`，是为了与当前标签的行业、规模中性语义一致，同时把价值和质量信号解释为同业同规模偏离。它是基于两类原始论文做的工程化组合，不是任何一篇论文的逐字复制，因此必须作为新候选接受开发期验证。

## 一手证据与候选取舍

### EP、BP、ROE

Liu、Stambaugh 与 Yuan 在 A 股中比较多种价值指标，发现 EP 在联合比较中压过 BM；他们也报告 EP、BM 和 ROE 的异常组合，并发现 size-neutral 排序通常降低组合波动、提高检验精度。这为 EP、BP、ROE 和规模控制提供直接 A 股依据，但论文使用月频异常组合，并未证明本文件的联合行业+规模残差能提高五日收益。[Liu, Stambaugh & Yuan, *Size and Value in China*](https://www.nber.org/system/files/working_papers/w24458/w24458.pdf)

Novy-Marx 在美国样本中发现，控制行业能明显改善价值与盈利能力策略，并构造行业调整的价值、动量和毛盈利能力因子。该结果直接支持“同业相对值”这一变换；但它的盈利能力是毛利润/资产，不是 ROE，且市场和频率均不同。因此这里只把它当作变换依据，ROE 的 A 股依据仍来自 Liu 等人的论文。[Novy-Marx, *The Other Side of Value*](https://www.nber.org/system/files/working_papers/w15940/w15940.pdf)

Leippold、Wang 与 Zhou 的 A 股机器学习研究把 `bm_ia`（行业调整 BM）和行业哑变量纳入预测集合，并发现流动性是月度预测中最重要的变量组；`bm_ia` 并非所有子样本中最重要。该结果说明行业调整账面市值比是已有的一手 A 股候选，但不支持把它当成确定赢家，也不支持脱离交易成本和开发边界直接迁移到五日策略。[Leippold, Wang & Zhou, *Machine Learning in the Chinese Stock Market*](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=3754339)

因此三项的证据排序为 `EP > BP > ROE`，但实验中不按该排序选子项；整个 bundle 要么通过，要么否决。保留 BP 是因为它有直接 `bm_ia` 实现先例，保留 ROE 是因为它给纯估值组合增加质量维度。仓库当前的 ROE 是当日 `PB/PE` 推导的估值隐含 ROE，而不是财报原始 ROE；实验必须沿用并明确这一口径，不能与论文中的会计 ROE 混称为同一字段。

### 为什么排除动量残差

Liu 等人的 A 股样本明确报告没有传统动量效应，过去多个收益窗口更接近未来反转；其日频五日持有实验使用的是过去 20 日反转。Novy-Marx 的行业调整动量来自美国月频因子。Leippold 等人发现动量类变量对 A 股月度非线性预测有作用，尤其在小股票中，但其设计不是 `mom_20_5 -> 未来五日`，且小股票同时受壳价值、涨跌停和成本影响。

所以 `mom_20_5_industry_size_residual` 只可列为将来的独立假设，不能作为第四项顺手加入本轮 bundle。当前已有 `ret_5`、`mom_20_5`、`mom_120_20`、`reversal_20`，继续添加近似收益窗口会提高选择自由度而没有相称的一手五日证据。

## 与当前特征的非重复性

当前面板已经包含 `ep`、`bp`、`roe`、`log_float_market_cap` 的全市场横截面排名，但模型输入没有历史行业 one-hot，也没有上述逐日条件残差。当前标签按行业和规模中性化，并不等于输入特征已经中性化；标签变换无法让模型从单股原始排名恢复当日同业基准。仓库现状见 [build_panel.py](../../strategy/rotating/features/build_panel.py)。

因此新三项在定义上不重复当前八项增强特征：它们不使用隔夜、收盘位置、趋势回归、下行波动、日内区间、价量相关或换手冲击。它们与已有 EP/BP/ROE 会高度相关，这是预期现象。开发报告必须给出新增列与全部现有列的 Spearman 相关、有效覆盖率、条件数或 VIF，以及 Elastic Net/GBRT 中的稳定性；若只是重复权重而没有独立增量，bundle 应被否决。

## PIT 与泄漏风险

- 行业必须来自 `T` 日有效的历史区间，禁止用当前行业分类回填历史。仓库行业区间由年度快照派生，边界日和临时调整可能不完整；行业未知时保留缺失，并报告覆盖率。
- EP、BP 只能使用 `T` 日 daily indicator 中当时可得的估值。负或零分母不做对数，也不以未来恢复的正值回填。
- ROE 沿用 v0.2 的当日估值隐含口径 `pb_mrq / pe_ttm`；两个输入都必须来自 `T` 日 daily indicator，且仅在 PE、PB 为正时有效。禁止改用当前财务快照或事后修订值补覆盖，也不得把该代理描述成财报原始 ROE。
- 浮动市值必须是 `T` 日值。若只能由当日成交额/换手推导，则保留来源标记并做覆盖诊断；禁止把后来股本向前 ASOF 到历史日期。
- 所有缩尾、行业/规模回归和排名都只使用 `T` 日当时合格横截面。股票池、ST、停牌、上市时长均按历史状态判定。
- 信号在 `T` 收盘后生成，最早于 `T+1 open` 成交。标签仍为 `T+1 open -> T+6 open`，训练时必须满足 `label_available_date <= prediction_date`。
- 行业分类覆盖变化会同时改变残差和横截面样本。报告每日有效股票数、行业数、小行业剔除数，不允许将覆盖变化误判为 alpha。

## 唯一开发实验与公共边界

只比较两个规格：

```text
B: 当前冻结 v0.2 输入
I: 当前冻结 v0.2 输入 + 固定三项行业规模残差 bundle
```

标签、股票池、Elastic Net/GBRT 参数、随机种子、训练窗口、五日 sleeve、成本、容量、涨跌停与未成交订单处理全部保持不变。三项不做用于选择的单项 ablation；单项统计只用于重复性和缺失诊断。

边界固定如下：

| 项目 | 规则 |
|---|---|
| 训练暖身 | 沿用当前冻结方案，且每条训练样本满足标签已可得 |
| 候选选择 | 仅 2021-01-01 至 2023-12-31 |
| 物理读取上限 | 2023-12-31 |
| 开发期末标签 | 只保留 `T+6 <= 2023-12-31` |
| 2024-01-01 以后 | 禁止用于选择、方向、阈值、窗口、缺失规则或 bundle 拆分 |

评价仍以五日真实组合的净年化、Sharpe、最大回撤、换手和成本压力为主，并报告五日重叠标签 Rank IC 的 HAC(4)、五个不重叠 cohort、逐年结果、行业暴露和 `I-B` 配对日收益。只有固定 bundle 在既有开发门槛下整体胜出才可冻结；失败、分裂或数据不足都保留 v0.2，不用 2024 年以后结果补救。

## 一手来源

1. Liu, Stambaugh & Yuan, *Size and Value in China*: <https://www.nber.org/system/files/working_papers/w24458/w24458.pdf>
2. Novy-Marx, *The Other Side of Value: Good Growth and the Gross Profitability Premium*: <https://www.nber.org/system/files/working_papers/w15940/w15940.pdf>
3. Leippold, Wang & Zhou, *Machine Learning in the Chinese Stock Market*: <https://papers.ssrn.com/sol3/papers.cfm?abstract_id=3754339>
