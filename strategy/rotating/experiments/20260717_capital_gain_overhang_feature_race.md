# 2026-07-17 持仓成本 overhang 特征赛马

## 研究问题与唯一比较

本实验只回答：在已修正公司行为、已冻结五日涨跌停状态特征和五日端点标签的基础上，加入公开论文定义的持仓成本 overhang 与条件反转，能否稳定改善日频长仓组合。

唯一比较固定为：

```text
B: PRICE_LIMIT_MODEL_INPUT_FEATURES（40 项）+ target_rank
O: B + rank_capital_gain_overhang_3y
     + rank_overhang_contrarian_5
     + capital_gain_overhang_3y_missing
     + overhang_contrarian_5_missing
```

两项连续特征及两个缺失指示器是不可拆分 bundle；不得按开发结果只保留单项、改变方向或增加其他交互。研究依据、原始论文、经济边界与固定公式见 [`capital_gain_overhang_daily_features.md`](../../../docs/research/capital_gain_overhang_daily_features.md)。本文和该研究文档均在读取候选面板、分数或收益前完成。

## 固定公式

对信号日 `T`，`P(i,s)` 为修正公司行为且通过交叉校验的前复权收盘价，`v(i,s)=clip(turnover_pct(i,s)/100, 0, 1)`。历史窗口是 `(T - 3 calendar years, T)` 内截至 `T-1` 的全部市场交易日，按由近到远记为 `s_1...s_N`：

```text
w(i,T,s_n) = v(i,s_n) * product(1-v(i,s_j), j=1..n-1)
k(i,T)     = sum_n w(i,T,s_n)
RP(i,T)    = sum_n w(i,T,s_n)*P(i,s_n) / k(i,T)
capital_gain_overhang_3y(i,T) = (P(i,T)-RP(i,T))/P(i,T)

ret5(i,T)        = P(i,T)/P(i,T-5)-1
ret5_csi300(T)   = P300(T)/P300(T-5)-1
excess_ret5(i,T) = ret5(i,T)-ret5_csi300(T)
overhang_contrarian_5(i,T) =
    -excess_ret5(i,T) * 1[capital_gain_overhang_3y(i,T)*excess_ret5(i,T) < 0]
```

两项方向均固定为越大越偏多，并按既有信号日 1%/99% 去极值、横截面 rank 到 `[-1,1]`。同号或任一项严格为零时，第二项为有经济含义的零；缺失不得填零。

## 事前冻结边界

| 项目 | 冻结值 |
|---|---|
| 选择区间 | 2021-01-01—2023-12-31 |
| 物理读取上限 | 2023-12-31；禁止读取、统计或按 2024+ 调整 |
| 历史面板起点 | 2015-01-01，用于让 2018—2020 训练行拥有完整三年 overhang 历史 |
| 公司行为与限制价 | 与 v4 已通过审查的修正和能力模型完全相同 |
| 基准标签 | `target_rank = rank(neutralize(O(T+6)/O(T+1)-1))` |
| 模型 | 固定因子、Elastic Net、浅层 GBRT；季度重训、三年训练窗、随机种子 17 |
| 组合 | 五个每日 sleeve、五日持有、同一缓冲/容量/历史费用/固定成本/公司行为执行 |
| 市场代理 | 只用 `000300` 前复权收盘；不比较其他指数或横截面市场收益 |
| 历史窗口 | 严格三周年；不搜索 250/500/750/1260 日或短窗替代 |
| 反转窗口 | 严格五个市场交易日；不搜索 1/3/10/20 日 |

预定产物：

| 产物 | 路径 |
|---|---|
| 修正历史面板 | `strategy/rotating/cache/experiments/v6_corrected_limit_history_panel` |
| 涨跌停历史面板 | `strategy/rotating/cache/experiments/v6_price_limit_history_panel` |
| overhang 面板 | `strategy/rotating/cache/experiments/v6_overhang_dev_panel` |
| B 分数 | `strategy/rotating/cache/experiments/v6_overhang_baseline_scores` |
| O 分数 | `strategy/rotating/cache/experiments/v6_overhang_scores` |
| B 报告 | `strategy/rotating/reports/experiments/v6_overhang_baseline_dev` |
| O 报告 | `strategy/rotating/reports/experiments/v6_overhang_dev` |

## 完整历史与缺失规则

1. `T` 必须已拥有严格三周年的市场日历覆盖；上市不足三周年时两项连续特征均缺失，不缩窗。
2. 市场交易日不能因个股停牌删除。明确停牌日沿用最后一个可靠前复权收盘并令 `v=0`；无法证明是停牌的原始缺行令三年特征缺失。
3. 三年窗口内换手缺失、非有限或为负，价格缺失、非正，`k<=0`，或公司行为标签能力不支持，均令 CGO 缺失。换手大于 100% 只在概率计算中截为 100%，并计入审计。
4. 条件反转还要求个股与 `000300` 的 `T-5/T` 端点完整；指数不前向填充、不改用邻近日期。
5. 两项分别提供缺失指示器；连续原值在面板中保留 NaN，横截面 rank 进入冻结模型矩阵时沿用既有中性 rank `0` 映射。缺失指示器使真实零与缺失可区分，不新增插补参数。

## 数据与身份门槛

查看收益前必须全部满足：

1. 新建历史面板在 2018—2023 的旧列、键、目标、公司行为状态与 v4 对应面板逐值一致；新建 B 分数与 v4 price-limit 分数的键、经济目标、因子分数、模型分数和训练时序一致。
2. 所有物理读取（股票、公司行为、限制价和 `000300`）的最大日期不晚于 2023-12-31；2015—2023 每个预期分区存在，2021—2023 三个开发年度和五个 cohort 完整。
3. O 的评分规格只在 B 的 40 项输入后追加固定四列；目标、训练年数、最小训练行和随机种子相同，B/O 分数键与行数一致，训练泄漏计数均为 0。
4. 2021—2023 每年两项连续特征都有非空、非零、正值和负值；完整三年历史覆盖率、CGO/反转覆盖率、四项新增输入与既有输入相关矩阵、缺失原因、超 100% 换手计数均完整报告。
5. 随机抽样与合成测试必须证明：权重按由近到远的 turnover-survival 递推；信号日 `T` 的换手不进入成本；明确停牌保留日历且 `v=0`；缺行、无效公司行为、指数端点缺失不会被填充。
6. B/O 在历史费用与 20bp 四条主执行路径的未知持仓权益事件均为 0，参考价不匹配但权益已知的事件全部入账，期末待执行订单完整报告。

任一门槛失败即记为 `insufficient_data`，停止胜负判断；不得读 2024+ 补救。

## 胜出门槛

O 只有同时满足以下全部条件才登记为开发集胜者：

1. 2021—2023 历史时变费用下净年化收益和 Sharpe 均严格高于 B；
2. 三个完整年度至少两个年度的净收益和 Sharpe 同时严格高于 B；
3. 固定 20bp 往返成本下净年化收益和 Sharpe 均严格高于 B；
4. 历史费用最大回撤相对 B 恶化不超过 2 个百分点，年化换手不高于 B 的 125%；
5. O 对实际五日端点收益的平均 Rank IC 不低于 B 减 0.002，五个端点 cohort IC 全为正；
6. O-B 配对日净收益均值为正，并报告 HAC(4) t；
7. 所有身份、公共边界、数据覆盖和公司行为执行门槛通过。

任一项失败即 `rejected_on_development`，保留 B。不得按年份、板块或单项特征挑选；不得查看旧封存期打破平局。即使胜出，因 2021—2023 已反复开发且绝对收益可能为负，也只登记为 `development_winner_pending_fresh_forward_validation`，不自动替换默认策略。

## 必报诊断

- 冻结面板和 B/v4 基准身份、物理日期上限、分区与 score spec；
- 三年窗口覆盖、两项特征与缺失指示器的逐年分布、缺失原因和公式合成样本；
- 新增输入与原 40 项输入的相关性、内部相关性、B/O 分数日度 Spearman 与 top-25% 重合率；
- 对实际端点收益的全期/逐年/HAC(4)/五 cohort Rank IC；
- 历史费用和固定 20/40/80bp 的收益、Sharpe、回撤、换手、订单积压与公司行为审计；
- 最终机器决策和全部尝试台账。

## 完成结果

尚未运行。本文在生成 v6 面板、分数、回测或读取任何候选结果之前提交到工作树。
