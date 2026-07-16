# 日频持仓成本 overhang 特征候选

## 结论

下一轮只测试一个固定的二项 bundle：

1. `capital_gain_overhang_3y`：用换手后的“仍由原买方持有”概率加权历史成交价格，估计全市场持仓成本，并计算当前价格相对该成本的未实现资本利得；
2. `overhang_contrarian_5`：只有当上述资本利得与股票过去五个交易日相对沪深 300 的收益异号时，才保留五日反转分数。

这是一个整体候选，不拆成多个窗口或子组合赛马。方向固定为两项越大越偏多。研究阶段不读取本仓库 2021—2023 年的标签、分数或回测结果，也不主张公开论文保证本策略收益提高。

## 为什么它没有重复现有 40 项输入

现有输入已经覆盖动量/反转、波动与下行波动、最大单日收益、回撤、趋势质量与拟合残差、成交额和换手异常、Amihud、ADV、EP/BP/ROE、规模、隔夜跳空、收盘位置、价量相关、换手冲击及涨跌停状态。继续增加相邻收益、波动或流动性窗口，会同时提高共线性和研究自由度。

`capital_gain_overhang_3y` 的新增信息是**历史价格与随后换手路径的联合分布**。两只股票即使五日/二十日收益、波动和当前换手相同，只要历史成交发生在不同价格且后来留存概率不同，估计持仓成本就会不同。Grinblatt 与 Han 的模型和实证正是用这个量解释单纯过去收益未捕捉的参考价状态；他们报告资本利得变量控制后，过去收益的横截面预测力显著减弱。[原始 JFE 论文](https://www.sciencedirect.com/science/article/pii/S0304405X05000747)

`overhang_contrarian_5` 不再添加一个普通反转窗口。它把已有 `ret_5` 的短期价格冲击限制在一个有明确行为机制的状态：投资者账面盈亏与最近相对市场消息异号。Shen 与 Shen 在沪深 A 股采用周三到周三的周频框架，发现短期 contrarian 收益只在资本利得 overhang 与上一期超额收益异号时出现，且该条件策略在其比较的三类策略中最强。[原始 Finance Research Letters 论文](https://www.sciencedirect.com/science/article/pii/S1544612321003822)

## 一手证据及其边界

- Grinblatt 与 Han 给出 turnover-survival 参考成本和资本利得 overhang 的经济机制，并发现较高 overhang 对后续收益为正；样本不是 A 股，主要对应中期动量，不能直接当作五日收益证据。[原始 JFE 论文](https://doi.org/10.1016/j.jfineco.2004.10.006)
- Hur、Pritamani 与 Sharma 用最多三年的日价格和日换手计算同一参考成本，公式、公司行动调整和三年窗口均明确；他们的预测组合仍是月频，因此这里只用其固定日频构造，不外推其收益幅度。[原始 Financial Management 论文](https://onlinelibrary.wiley.com/doi/10.1111/j.1755-053X.2010.01107.x)
- Lei、Zhou 与 Zhu 使用沪深 A 股交易数据，发现资本利得与下月预期收益正相关，并发现资本利得和不流动性的联合项为负。这说明 A 股中的 overhang 不是普通换手或 Amihud 的替代品，但该论文是月度预测，本轮不额外加入流动性交互项。[原始 Pacific-Basin Finance Journal 论文](https://www.sciencedirect.com/science/article/pii/S0927538X13000681)
- Shen 与 Shen 直接研究沪深 A 股的周度短期反转：异号状态增强过度反应，买入“过去相对输家但仍有较大账面盈利”、回避“过去相对赢家但仍有较大账面亏损”形成最明确的短期方向。这是 bundle 与五日持有最接近的一手证据，但论文多空组合、周三固定形成日及交易成本口径与本仓库不同，仍须在既定开发边界内独立检验。[原始 Finance Research Letters 论文](https://doi.org/10.1016/j.frl.2021.102380)

## 固定定义

### 1. `capital_gain_overhang_3y`

对股票 `i` 的信号日 `T`，`P(i,s)` 是截至 `s` 可得、公司行动口径一致的前复权收盘价，`turnover_pct(i,s)` 是百分数换手率。先定义具有概率含义的换手比例：

```text
v(i,s) = clip(turnover_pct(i,s) / 100, 0, 1)
```

窗口 `S_3Y(T)` 固定为 `(T - 3 calendar years, T)` 内、截至 `T-1` 的全部市场交易日，不把 `T` 日成交写入参考成本。按日期从近到远编号为 `s_1, ..., s_N`，其中 `s_1` 是 `T` 前一市场交易日。历史价格的未换手留存权重为：

```text
w(i,T,s_n) = v(i,s_n) * product(1 - v(i,s_j), j = 1, ..., n-1)
k(i,T)     = sum(w(i,T,s_n), n = 1, ..., N)
RP(i,T)    = sum(w(i,T,s_n) * P(i,s_n), n = 1, ..., N) / k(i,T)
capital_gain_overhang_3y(i,T) = (P(i,T) - RP(i,T)) / P(i,T)
```

公式中的乘积表示在 `s_n` 买入且此后至 `T-1` 尚未因换手离开原持有人手中的概率。三年是唯一窗口：不比较 250/500/750/1,260 日，也不对各股票按“最近 N 条有效记录”补足窗口。

固定方向为正：较高值表示当前价格高于估计持仓成本，公开模型与 A 股月度证据指向较高后续收益。模型输入仍沿用仓库既有的信号日横截面缩尾/映射规则，不在本特征内部增加分位点。

### 2. `overhang_contrarian_5`

用相同前复权收盘价构造恰好五个市场交易日的股票收益，并用仓库已有 `000300` 日线构造同区间市场收益：

```text
ret5(i,T)       = P(i,T) / P(i,T-5) - 1
ret5_csi300(T)  = P300(T) / P300(T-5) - 1
excess_ret5(i,T)= ret5(i,T) - ret5_csi300(T)

mismatch(i,T) = 1[capital_gain_overhang_3y(i,T) * excess_ret5(i,T) < 0]

overhang_contrarian_5(i,T) = -excess_ret5(i,T) * mismatch(i,T)
```

固定方向为正，且没有可调阈值：

- overhang 为正、五日超额收益为负时，分数为正，对应论文中的长端；
- overhang 为负、五日超额收益为正时，分数为负，对应应回避的短端；
- 同号或任一值严格为零时，分数固定为零。

不用上证综指、中证 500、行业收益或横截面均值替换 `000300`，也不比较 1/3/10/20 日反转窗口。

## PIT、公司行动与缺失规则

以下规则属于特征定义，不得在看到开发期结果后放宽：

1. `T` 日必须已满三周年，且 `S_3Y(T)` 的市场日历、历史上市状态、交易状态、收盘和换手来源均只能使用当日可知版本。上市不足三年时两项均缺失，不缩短窗口。
2. 市场交易日不能因个股停牌而从窗口删除。已被历史 `trade_status` 明确识别的停牌日保留该日，价格沿用停牌前最后一个可靠前复权收盘且 `v=0`；若只是原始行缺失、无法证明停牌，则整个 `capital_gain_overhang_3y` 缺失。
3. `turnover_pct` 缺失、非有限或小于零时整个三年特征缺失；大于 100% 时只为概率计算固定截到 100%，原值仍保留给数据质量审计。`k<=0`、任一所需价格缺失/非正或公司行动调整无法验证时同样缺失。
4. 前复权必须由当时可得且经公司行动交叉核验的同一快照生成。已知旧 qfq 可能保留机械除权跳变；在该问题修正前，本 bundle 只能实现单元测试，不能进入正式收益比较。
5. `overhang_contrarian_5` 还要求股票和 `000300` 在 `T-5`、`T` 两端价格均完整；任一端缺失则置空，不前向填充指数、不改用相邻日期。
6. 面板中的两项连续原值保持缺失，不能把原值写成零，因为零在第二项有明确的“非异号状态”含义。进入冻结模型矩阵时，沿用仓库既有规则把缺失的横截面 rank 映射为中性 rank `0`，并为两项各配一个缺失指示器；因此真实原值零与缺失仍可由指示器区分，不新增可调插补方法。

## 唯一实验边界

后续若实现，只允许一次 `现有 PRICE_LIMIT_MODEL_INPUT_FEATURES` 基准与 `基准 + 上述二项 + 两个缺失指示器` 的成对比较；标签、五日持有/复核、股票池、LightGBM 参数、训练窗、随机种子、成本、容量、涨跌停和 T+1 执行规则全部保持不变。不得同时测试：

- 其他 CGO 历史长度、指数或反转长度；
- CGO 与 Amihud、换手、波动、MAX 或涨跌停的额外交互；
- 52 周高低点、参考价分布高阶矩或动态参考价；
- 只保留 bundle 中胜出的单项。

本文件只完成候选筛选与预注册定义，不修改策略代码、实验文档，也不运行面板构建或回测。

## 原始来源

1. Mark Grinblatt and Bing Han, “Prospect Theory, Mental Accounting, and Momentum,” *Journal of Financial Economics* 78(2), 2005: <https://doi.org/10.1016/j.jfineco.2004.10.006>
2. Jungshik Hur, Mahesh Pritamani, and Vivek Sharma, “Momentum and the Disposition Effect: The Role of Individual Investors,” *Financial Management* 39(3), 2010: <https://doi.org/10.1111/j.1755-053X.2010.01107.x>
3. Xiaoyan Lei, Yuegang Zhou, and Xiaoneng Zhu, “Capital Gains, Illiquidity, and Stock Returns,” *Pacific-Basin Finance Journal* 25, 2013: <https://doi.org/10.1016/j.pacfin.2013.10.001>
4. YuJan Shen and KuanFu Shen, “Short-Term Contrarian Profits and the Disposition Effect,” *Finance Research Letters* 46, 2022: <https://doi.org/10.1016/j.frl.2021.102380>
