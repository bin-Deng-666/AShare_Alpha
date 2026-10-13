# 单标的买入并持有

`BuyAndHoldStrategy` 是唯一公开策略示例：等待指定标的有可交易收盘数据，使用模型账户现金发出买入请求，成交后持续持有。没有择时、止损、止盈、调仓或模型训练。

默认 `code="000001"` 只是示例。参数只接受一个六位代码，例 `{"code":"600000"}`。账户、费用、板块股数、T+1、公司行为和未成交请求由通用回测引擎处理。

## 使用

无需行情的合成演示：

```bash
ashare-alpha demo --output /tmp/hold-demo
```

已更新并审计真实数据后：

```bash
ashare-alpha strategy backtest demo --start 2024-01-02 --end 2024-01-31 \
  --save-inputs --output /tmp/hold-run
ashare-alpha plan demo --panel /tmp/hold-run/inputs
```

可通过 `--params <json文件>` 选择另一个单一标的。日计划在已成交持仓下正常返回无买入、无卖出，并显示真实模型股数。

示例为教学用模型账户。公司行为默认使用明确的除权日到账代理政策，不能代表完整实盘结算；发生不支持的权益时公共引擎失败并保留诊断。质量 review 与未知执行日持续可见。

## 代码

- `params.py`：严格的单标的参数。
- `strategy.py`：买入并持有决策。
- `build.py`：从已审计 canonical 数据冻结单标的输入。
- `run.py`：同一策略类的回测和日计划。
- `tests/`：只交易选定标的、跌价不卖出、暂停交易和计划/持仓一致性。

策略代码不维护现金、数量或成交账本，模型结果由标准 `BacktestResult` 保存。
