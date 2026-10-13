# 策略示例

公开仓库只注册 [单标的买入并持有 demo](demo/README.md)。其他研究策略、模型和历史实验留在本地，不进入 Git 或安装包。

策略类通过 `backtest.Strategy` 返回订单，由公共引擎维护成交、费用、账户和公司行为。demo 买入指定标的后持续持有，不做选股、择时、止损或换仓。

`research/` 提供通用的已审计输入读取、快照身份和标准运行发布工具，不包含策略因子或训练模型。`ASHARE_WORKSPACE` 下的 `strategy/artifacts/` 保存本地产物。

新策略可以遵循 [回测接口](../backtest/README.md)，公开示例和能力在 `ashare_alpha/strategies.py` 唯一声明。默认发布范围仍限制为 demo；添加私人研究目录不会自动注册到 CLI/WebUI。
