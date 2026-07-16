# A 股日频 rotating 策略：多视距标签与市场状态候选比较

核验日期：2026-07-16（Asia/Shanghai）

## 最终建议

下一轮只实验一个候选：**保留现有五日端点标签和 GBRT，不做 5/10/20 日多视距模型；新增一个固定的沪深 300 月度趋势门控**。

固定规则是：用 `000300` 最近一个完整月末收盘价与包含该月在内的 10 个月月末收盘简单平均线比较；高于均线时，次月允许按原策略新开五日复核 sleeve，否则该 sleeve 留在现金。已有持仓不在月末强平，仍按现有五交易日复核和 top-25% 退出缓冲执行；待卖订单继续处理。当前成交台账的实际卖出持有期中位数约 25 个交易日、均值约 38 个交易日，因此“五日”描述的是最短复核周期，不是强制退出期限。

这一建议来自[仓库当前开发期报告](../../strategy/rotating/reports/development/PERFORMANCE.md)与[结构化结果](../../strategy/rotating/reports/development/PERFORMANCE.json)：v0.2 GBRT 的五日端点 Rank IC 约 0.087、正 IC 日约 84.6%，策略年化约 -0.98%，同期沪深 300 年化约 -13.26%。这与“横截面排序有预测力、但 long-only 组合的绝对收益仍被市场方向拖累”相一致。后一句是对现有结果的**诊断假设**，不是因果证明；它需要下面的单一开发期实验验证。

不推荐先做多视距，是因为它会新增两个重叠标签、两个模型和融合层，但未直接控制当前最明显的长仓市场暴露；而月度状态候选不改标签、特征、模型或五日持仓，只在组合入口增加一个固定的 0/1 状态，改动更小且与瓶颈更对齐。

## 两个方向的证据比较

| 维度 | 固定 5/10/20 日模型融合 | 固定 10 个月市场状态门控 |
|---|---|---|
| 监督目标 | 新增 10、20 日端点标签和两个模型 | 保持当前 `T+1 open -> T+6 open` 标签与 GBRT |
| 组合变化 | 三个预测横截面排名固定等权 | 只决定次月能否新开五日 sleeve |
| 针对的假设 | 多尺度收益信息能改善股票排序 | 市场下行期的 long beta 是绝对收益瓶颈 |
| 对当前证据的贴合 | 当前五日 IC 已较高，新增排序信息的边际价值未知 | 当前相对指数抗跌但绝对收益仍负，直接控制暴露 |
| 新工程面 | 3 套标签可得日、HAC/gap、缺失样本与评分规格 | 1 条月末指数序列和 1 个固定布尔状态 |
| 选择风险 | 容易滑向选窗口、选头、调权重 | 固定 `000300 / 10个月 / 0或1 / 月更`，无参数网格 |
| 最终交易 | 仍需回到五日组合验证 | 天然保持五日组合 |
| 本轮决策 | 不实施、不运行 | **唯一候选** |

一手资料对多视距方向只提供一般依据，不能证明 5/10/20 日在本策略上更好：

- Microsoft Qlib 官方 Alpha158/Alpha360 默认使用下一期收益标签，并同时以未来收益相关性和组合收益评价信号，说明标签可替换、但统计预测不能代替经济回测。[Qlib 官方标签源码](https://github.com/microsoft/qlib/blob/main/qlib/contrib/data/handler.py)；[Qlib 官方 A 股基准](https://github.com/microsoft/qlib/blob/main/examples/benchmarks/README.md)
- Lim 等人的原始论文把 multi-horizon forecasting 定义为同时预测多个未来步，从而给出未来路径估计；其数据与模型不是股票横截面，不能外推为 A 股收益保证。[Temporal Fusion Transformer 原始论文](https://arxiv.org/abs/1912.09363)
- Caruana 的原始多任务论文说明相关任务可通过共享表示形成归纳偏置，但“不同收益视距确实相关且有帮助”仍需实证，且当前 GBRT 没有共享表示。[Multitask Learning 原文](https://doi.org/10.1023/A:1007379606734)
- Bates 与 Granger 证明预测合成在一定条件下可降低同一预测量的均方误差；不同持有期股票排序的等权合成不完全满足这一设定。[The Combination of Forecasts 原文](https://doi.org/10.1057/jors.1969.103)
- Leippold、Wang、Zhou 的 A 股原始研究使用一个月前瞻收益并考虑交易成本与涨跌停，只能支持“约 20 个交易日标签值得受控研究”，不能证明它优于当前五日标签。[Machine Learning in the Chinese Stock Market 原文](https://doi.org/10.1016/j.jfineco.2021.08.017)

市场状态候选有更直接、但仍有限的原始依据。Faber 的原始 TAA 论文规定：月度价格高于 10 个月 SMA 时持有风险资产，低于时转入现金；每月最后一天更新一次，月内忽略价格波动。论文明确把该规则定位为风险管理，并跨多个资产类别检验；它还展示 3—12 个月参数稳定性，但本轮**不借此搜索窗口**，只照搬原文主规则的 10 个月。[Faber 原文 PDF](https://mebfaber.com/wp-content/uploads/2016/05/SSRN-id962461.pdf)

市场代理固定为 `000300`，因为中证指数有限公司的官方编制方案将沪深 300 定义为由沪深市场中市值和流动性较大的 300 只证券组成、用于衡量沪深证券表现的指数；这只说明它是预先存在的市场代理，不证明该门控有效。[沪深 300 官方编制方案](https://oss-ch.csindex.com.cn/static/html/csindex/public/uploads/indices/detail/files/zh_CN/000300_Index_Methodology_cn.pdf)

Faber 原文不是 A 股日频 rotating 策略：原模型使用月度总回报序列、信号日收盘成交、风险关闭后持有美国短期国债，并在资产层直接进出。下面的规则是对它做 `000300 + T+1 执行 + 五日 sleeve` 的 A 股适配，不是复现原文收益，更不是 A 股收益保证。

## 本轮否决但保留审计定义的 5/10/20 方案

为了使“不实施多视距”可复核，而不是口头放弃，原候选固定为三个独立、参数完全相同的浅层 GBRT。对 `H ∈ {5, 10, 20}`：

```text
y_H(T, i) = Oq_i(T+H+1) / Oq_i(T+1) - 1
label_available_date_H = T+H+1

r_H(T, i) = 当日横截面内把 GBRT_H 原始预测映射到 [-1, 1] 的平均秩
s_mh(T, i) = (r_5(T, i) + r_10(T, i) + r_20(T, i)) / 3
```

三个收益分别执行当前的行业与 `log(float_market_cap)` 中性化和横截面排名。每个视距的 `T+1` 必须可买，`T+2 ... T+H+1` 必须都有前复权/原始开盘且按现有一字跌停规则可卖；缺失或不可执行即令该视距标签为空，不前向填充。不能为了取得共同样本而要求五日头额外满足 `T+7 ... T+21` 的未来状态。

若未来单独登记该实验，标签可得日必须分别为 `T+6 / T+11 / T+21`；日度训练标签 IC 至少分别使用 HAC(4)/HAC(9)/HAC(19) 和 5/10/20 个不重叠 cohort。块状验证由最长头决定统一 `gap=20` 个信号交易日，并逐头再次检查 `label_available_date_H <= prediction_date`。只允许向前滚动训练，所以同一 fold 的后侧训练 embargo 为 0；若改成验证块之后的样本也进入同一 fold，则验证标签结束后必须另加 20 个交易日 embargo。

该候选固定等权，不允许选最佳单头或调权重；最终仍必须用真实五日组合评价。它本轮被**事前否决且不运行**，原因是新增监督尺度没有直接对应已观察到的绝对收益瓶颈，工程面和潜在选择自由度又显著大于单一市场门控。保留上述公式、`T+21`、`gap=20`、HAC(19) 与 embargo 规则，供以后在新的、独立登记的实验中使用。

## 唯一候选的精确定义

### 月度状态

令 `P_M` 为沪深 300（固定代码 `000300`）在日历月 `M` 最后一个实际交易日的收盘价。只使用已完整结束月份的收盘，不把后来月份的数据前向或后向填入。定义：

```text
SMA10_M = mean(P_M, P_{M-1}, ..., P_{M-9})
gate_{M+1} = 1[P_M > SMA10_M]
```

规则全部冻结：

- 市场代理只用 `000300`，不比较中证 500、全 A、上证综指或模型组合自身净值；
- 均线只用 10 个完整月末收盘，不比较 6/9/12 个月、200/250 日或指数收益阈值；
- 状态只取 `0/1`，不做连续仓位、波动率缩放或迟滞带；相等时归入 `0`；
- 状态每月只更新一次，不因月内穿越均线改变；
- 现金按当前回测引擎的现金口径处理，不额外加入货币基金或国债收益。

`gate_{M+1}` 在 `M` 最后一个交易日收盘后才可得。它控制计划在 `M+1` 入场的 sleeve；尤其 `T` 为月末最后一个交易日、计划 `T+1` 在次月开盘成交时，可以使用刚完成的 `gate_{M+1}`，不存在同收盘成交。

### 对五日 sleeve 的动作

```text
gate = 1:
  按现有 GBRT 分数、股票池、容量、100 股取整和 T+1 开盘规则新开 sleeve

gate = 0:
  不为该日新开 sleeve，对应资金留现金
  未成交的待买订单取消，不跨到 risk-off 月继续等待

无论 gate:
  已持有 sleeve 继续按五交易日复核与 top-25% 退出缓冲处理
  停牌/一字跌停导致的待卖订单继续处理，不能因为状态关闭而虚构成交
```

因此月度状态由 1 变 0 时，组合会在最多五个交易日左右自然降到现金，而不是在月末收盘强制平仓。这是与 Faber 原规则最重要的执行差异：它保留 A 股 T+1、涨跌停和现有五日轮换语义。

## 标签、可得日和完整可执行路径保持不变

本轮不增加 10/20 日标签。监督目标继续是：

```text
y_5(T, i) = Oq_i(T+6) / Oq_i(T+1) - 1
label_available_date(T) = T+6
```

`Oq` 为前复权开盘价。标签仍按信号日行业和 `log(float_market_cap)` 中性化，再映射为横截面 `[-1, 1]` 排名。

完整可执行路径与 v0.2 相同：`T+1` 有前复权/原始开盘、可交易且不是无法买入的一字涨停；`T+2 ... T+6` 每日有完整价格、可交易且不是无法卖出的一字跌停。收益使用前复权价格，可执行性使用原始 OHLC 和原始涨跌停价；普通非一字涨跌停按现有引擎允许成交；缺失或不可执行时标签为空，禁止前向填充。

市场状态只控制未来下单，不能写进标签有效性或训练股票池。否则模型训练样本会被后来观察到的指数状态选择，基准标签也会改变。

## 重叠、gap、HAC 与 embargo

因为标签仍为五日端点收益，现有防泄漏边界不扩大：

- 任一训练行只有在 `label_available_date <= prediction_date` 时才能拟合；
- 同一天所有股票属于同一时间 fold；任何块状训练/验证切分保持 `gap=5` 个信号交易日；
- 只做向前扩展/滚动训练，同一 fold 不使用验证块之后的数据，所以不需要另设后侧训练集 embargo；旧验证样本只能等到 `T+6` 标签可得后进入后来日期的训练；
- 标签日度 Rank IC 继续报告 HAC(4) 与五个不重叠 cohort。

相邻五日标签共享四段 open-to-open 收益。Hansen 与 Hodrick 的原始论文处理“采样频率高于 k 步预测区间”的推断；Newey 与 West 提供异方差、自相关稳健的协方差估计。因此普通 iid 标准误不能用于标签 IC。[Hansen–Hodrick 原文及作者页面](https://larspeterhansen.org/lph_research/forward-exchange-rates-as-optimal-predictors-of-future-spot-rates-an-econometric-analysis/)；[Newey–West 原文](https://doi.org/10.2307/1913610)

市场门控按月持续，候选与基准的日收益差会出现约一个月的状态聚类。除原有组合指标外，必须对配对差值

```text
delta_t = net_return_gate_t - net_return_baseline_t
```

另报均值、HAC(20) 标准误和 t 值。HAC(20) 是针对约一个交易月状态持续性的预注册稳健带宽，不是 Faber 原文给出的显著性规则，也不替代逐年收益、Sharpe 和最大回撤。若观察到更长自相关，可以附加更长带宽诊断，但不得据此改均线或选择候选。

## 公共开发边界与禁止旧留出期选择

唯一候选只在物理截止于 2023-12-31 的开发数据上比较：

| 项目 | 固定边界 |
|---|---|
| 特征/模型训练暖身 | 2018-01-01—2020-12-31，逐日 PIT 且受标签可得日约束 |
| 候选递归评分与选择 | 2021-01-01—2023-12-31 |
| 行情与指数物理读取上限 | 2023-12-31 |
| 标签 | 只保留 `T+6 <= 2023-12-31` 的开发期末标签 |
| 组合经济回测 | 成交、估值和未成交订单严格截到 2023-12-31，边界外行情读取为 0 |
| 2024-01-01—2026-05-22 | 完全不用于本轮选择、否决、阈值或规则修改 |

月度状态的最初 10 个月历史只能作为过去数据热身；任何开发期日 `T` 的 gate 都只能由 `T` 当时已经完成的月末收盘生成。开发面板应保存 `gate_source_month_end`、10 个实际月末日期和数值，以便审计无未来月数据。

仓库已在 v0.1/v0.2 打开过 2024-01-01—2026-05-22。它对新候选不再是独立留出期；冻结后若运行，只能标成“受污染历史诊断”，不能用来打破平局或宣称新的严格样本外收益。真正外部确认应从第一个**严格晚于 2026-05-22、且冻结前从未被本研究读取**的交易日开始前瞻累计；若 2026-05-22 之后某段数据已在冻结前被查看，外部验证起点必须相应后移。

White 把 data snooping 定义为同一数据被重复用于推断或模型选择；Bailey 等人的原始研究专门讨论投资策略回测选择导致的过拟合。因此“冻结后再看已经打开的旧留出期”不会恢复其独立性。[White, A Reality Check for Data Snooping](https://doi.org/10.1111/1468-0262.00152)；[Bailey et al., The Probability of Backtest Overfitting](https://papers.ssrn.com/sol3/Papers.cfm?abstract_id=2326253)

## 单一选择自由度与预注册门槛

本轮只有一个布尔选择：

- 基准 `B`：每天都允许按现有 v0.2 新开五日 sleeve；
- 候选 `G`：只在固定月度 `gate=1` 时允许新开同一个 sleeve。

不得同时运行多视距候选，不得换指数、均线、阈值、更新频率、现金替代物、标签、特征、GBRT 参数、训练窗、随机种子、股票池、成本、容量或持仓期。

只有 `G` 同时满足以下全部开发期门槛才胜出：

1. 2021、2022、2023 三个日历年度中，至少两个年度的历史时变成本后净收益和 Sharpe 同时高于 `B`；
2. 2021—2023 全期历史时变成本后年化收益和 Sharpe 同时高于 `B`；
3. 全期最大回撤不大于 `B`；门控的研究目的就是降低风险，回撤恶化不接受；
4. 固定成交路径、20bp 往返成本压力下，全期年化收益仍高于 `B`；
5. T+1、停牌、一字涨跌停、100 股取整、五日持有、费用、容量和未成交订单不变量全部通过；risk-off 月的待买取消和待卖延续必须单独断言；
6. 报告 `gate=1` 月份/交易日占比、实际资金暴露、进入/退出月份、留现金收益、错过上涨收益、配对日收益差的 HAC(20)，以及逐年结果；这些是审计输出，不产生新的选择规则。

任何一条失败、结果分裂或数据不足都保留现有五日基准。不得用旧留出期、另一条均线或多视距模型补救。实验台账应把该固定门控登记为一次完整策略尝试，无论它是否胜出。Harvey、Liu、Zhu 的原始横截面研究说明大量尝试会抬高虚假发现风险；本轮限制为一个候选正是为了控制这一自由度。[Harvey, Liu & Zhu 原文](https://www.nber.org/papers/w20592)

## 最终经济评价仍是五日实际组合

无论状态开启或关闭，最终评价对象始终是同一套五日可交易组合：

```text
T close 生成原 GBRT 分数
  -> 查看计划入场月的固定 gate
  -> gate=1 时 T+1 open 按原规则买入；gate=0 时该 sleeve 留现金
  -> 已持仓 sleeve 继续持有并按五交易日周期复核
  -> 跌出 top 25% 后下一开盘卖出，失败则进入待卖
  -> 相同历史费率、分档开盘滑点、容量和 100 股取整
```

主表比较净年化收益、Sharpe、最大回撤、换手、容量、现金占比、未成交积压和 20/40/80bp 固定路径压力。标签 IC 仍是原 GBRT 分数对实际五日端点收益的 Rank IC/HAC(4)；市场门控不应改变模型分数，预期两组 IC 应逐字节一致。收益改善如果只来自删除亏损市场暴露而不是更高 IC，是候选设计本身，不是异常。

## 实现前审计清单

- [ ] 固定读取 `000300`，从真实月末交易日构造 `P_M` 与包含当月的 `SMA10_M`；
- [ ] 测试 `>` 为 1、`<=` 为 0，且状态只在下一个日历月生效；
- [ ] 测试月末信号的计划 `T+1` 入场使用刚完成月份的 gate，不发生同收盘成交；
- [ ] 测试 risk-off 不新开/取消待买，已有持仓按原复核与退出缓冲继续，待卖继续；
- [ ] 证明标签、`label_available_date=T+6`、完整可执行路径、GBRT 分数与 v0.2 基准逐字节一致；
- [ ] 保持 `gap=5`、标签 IC HAC(4)，新增配对经济差 HAC(20)；
- [ ] 开发面板物理截止 2023-12-31，边界之后指数、标签和经济退出读取计数均为 0；
- [ ] 只生成基准和一个固定门控候选，不运行 5/10/20、多指数或多均线赛马；
- [ ] 保存失败结果和尝试编号，2024—2026-05-22 只允许受污染诊断；
- [ ] 真正状态继续为 research-only，进入严格晚于 2026-05-22 的未见数据前瞻纸面交易。

## 一手来源索引

1. Faber，A Quantitative Approach to Tactical Asset Allocation：<https://mebfaber.com/wp-content/uploads/2016/05/SSRN-id962461.pdf>
2. Microsoft Qlib，官方标签实现：<https://github.com/microsoft/qlib/blob/main/qlib/contrib/data/handler.py>
3. Microsoft Qlib，官方 A 股 benchmark：<https://github.com/microsoft/qlib/blob/main/examples/benchmarks/README.md>
4. Lim, Arik, Loeff & Pfister，Temporal Fusion Transformers：<https://arxiv.org/abs/1912.09363>
5. Caruana，Multitask Learning：<https://doi.org/10.1023/A:1007379606734>
6. Bates & Granger，The Combination of Forecasts：<https://doi.org/10.1057/jors.1969.103>
7. Leippold, Wang & Zhou，Machine Learning in the Chinese Stock Market：<https://doi.org/10.1016/j.jfineco.2021.08.017>
8. Hansen & Hodrick，Forward Exchange Rates as Optimal Predictors：<https://larspeterhansen.org/lph_research/forward-exchange-rates-as-optimal-predictors-of-future-spot-rates-an-econometric-analysis/>
9. Newey & West，HAC covariance：<https://doi.org/10.2307/1913610>
10. White，A Reality Check for Data Snooping：<https://doi.org/10.1111/1468-0262.00152>
11. Bailey, Borwein, López de Prado & Zhu，The Probability of Backtest Overfitting：<https://papers.ssrn.com/sol3/Papers.cfm?abstract_id=2326253>
12. Harvey, Liu & Zhu，… and the Cross-Section of Expected Returns：<https://www.nber.org/papers/w20592>
13. 中证指数有限公司，沪深 300 指数编制方案：<https://oss-ch.csindex.com.cn/static/html/csindex/public/uploads/indices/detail/files/zh_CN/000300_Index_Methodology_cn.pdf>
