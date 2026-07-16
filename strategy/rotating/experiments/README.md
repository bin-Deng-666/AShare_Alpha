# rotating experiments

- [`20260716_market_gate_feature_race.md`](20260716_market_gate_feature_race.md)：v0.2 后续固定沪深 300 月度趋势门控；开发期未通过收益、Sharpe 与跨年度门槛，默认策略不变。
- [`20260716_frozen_forward_extension.md`](20260716_frozen_forward_extension.md)：冻结 v0.2 在 2026-05-25 之后的首次未调参前向延伸诊断。
- [`20260716_industry_size_value_quality_race.md`](20260716_industry_size_value_quality_race.md)：只用 2021—2023 开发期比较固定行业规模残差价值—质量 bundle；2024 年以后禁止用于选择。

每次模型、特征、组合或成本假设的尝试都应在本目录登记，包括失败结果。最终测试段打开后不得继续按其结果调参并再次宣称样本外。

模板至少记录：数据快照、股票池是否 PIT、训练/验证/测试日期、模型参数、成本、容量、结果、是否采纳及原因。
