# rotating experiments

- [`20260716_market_gate_feature_race.md`](20260716_market_gate_feature_race.md)：v0.2 后续固定沪深 300 月度趋势门控；开发期未通过收益、Sharpe 与跨年度门槛，默认策略不变。
- [`20260716_frozen_forward_extension.md`](20260716_frozen_forward_extension.md)：冻结 v0.2 在 2026-05-25 之后的首次未调参前向延伸诊断。
- [`20260716_industry_size_value_quality_race.md`](20260716_industry_size_value_quality_race.md)：只用 2021—2023 开发期比较固定行业规模残差价值—质量 bundle；2024 年以后禁止用于选择。
- [`20260717_price_limit_state_feature_race.md`](20260717_price_limit_state_feature_race.md)：先用服务端截止日公司行为与逐日 `preclose` 修正并核验除权参考价，再只用 2021—2023 开发期比较固定五日涨跌停触及—封板状态 bundle；候选通过全部开发门槛，但绝对收益仍为负，只登记为等待全新前向验证的开发集胜者，默认策略不变。
- [`20260717_price_limit_path_label_race.md`](20260717_price_limit_path_label_race.md)：在同一修正数据与固定涨跌停状态特征上，只比较五日端点监督目标和 `T+2...T+6` 五个开盘累计收益的路径均值监督目标；路径标签改善全期聚合表现但只取得一个年度双胜，按事前门槛在开发期淘汰，未读取 2024+。
- [`20260717_capital_gain_overhang_feature_race.md`](20260717_capital_gain_overhang_feature_race.md)：固定比较现有 40 项输入与“严格三年 turnover-survival 持仓成本 overhang + 五日相对沪深 300 异号反转 + 两个缺失指示器”；20bp 聚合口径改善但历史费用和跨年度门槛失败，开发期淘汰，未读取 2024+。

每次模型、特征、组合或成本假设的尝试都应在本目录登记，包括失败结果。最终测试段打开后不得继续按其结果调参并再次宣称样本外。

模板至少记录：数据快照、股票池是否 PIT、训练/验证/测试日期、模型参数、成本、容量、结果、是否采纳及原因。
