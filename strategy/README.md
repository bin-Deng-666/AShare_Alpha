# 具体策略

每个子目录代表一个策略，包含自身的参数、信号、构建与运行入口。公开版本仅包含 [demo](demo/README.md)：单标的买入后持续持有。

通用执行配置、内容身份与结果发布在 `src/research/`，撮合和账户在 `src/backtest/`，不与具体策略并列。策略通过 `ashare_alpha.backtest.Strategy` 接口提交目标或订单，不能自行改写现金、成交和权益。

本地研究策略可以放在同层独立目录，并自带 `tests/` 和必要的维护脚本；它们默认被 Git 与安装包排除，不会自动注册到公开 CLI 或 WebUI。历史策略归档位于本地 `.local_research/archive/`。

运行产物写入工作区的 `artifacts/strategies/`，行情和任务状态分别位于 `data/`、`jobs/`。更多说明见[策略架构](../docs/strategy.md)和[回测接口](../docs/backtest/README.md)。
