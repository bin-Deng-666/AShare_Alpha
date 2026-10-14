# 策略回测

`src/backtest/` 提供策略类可复用的 A 股日频多头现金账户引擎。策略产生目标持仓或明确订单；引擎统一处理交易日、次日开盘执行、T+1、板块股数、费用、持仓批次、公司行为与绩效。

`trade_fee`、`apply_slippage`、`slippage_bps_for_row`、`lot_floor`、`compute_metrics` 保留公共入口。唯一公开策略为 `strategy/demo` 的单标的买入并持有示例。

## 运行

Python 3.10+，依赖 pandas、numpy、pyarrow；从 data 查询库加载时需要 duckdb。仓库根目录执行：

```bash
python -m ashare_alpha.backtest --help
python -m ashare_alpha.backtest \
  --strategy strategy.demo:BuyAndHoldStrategy \
  --start 2024-01-02 --end 2024-01-12 \
  --codes 000001 600519 \
  --save-inputs \
  --output /tmp/backtest-equal
```

策略通过显式 `module:Class` 导入，不扫描策略目录。`--params` 读取策略参数 JSON；`--config` 读取执行配置 JSON。输出目录必须不存在，失败结果也保留执行到失败处的记录。

已保存的输入可以独立于更新后的 data 重跑：

```bash
python -m ashare_alpha.backtest \
  --strategy strategy.demo:BuyAndHoldStrategy \
  --start 2024-01-02 --end 2024-01-12 \
  --snapshot /tmp/backtest-equal/inputs \
  --output /tmp/backtest-replay
```

安装后可从任意工作目录调用 `ashare-alpha` 或已有模块入口。支持 macOS/Linux、Python 3.10–3.13；`ASHARE_WORKSPACE` 定位 canonical 数据。离线示例可用 `ashare-alpha demo --output /tmp/ashare-demo`。

## 编写策略类

```python
from ashare_alpha.backtest import Strategy, DataRequirements, TargetPortfolio

class MyStrategy(Strategy):
    def requirements(self):
        return DataRequirements(fields=("close", "trade_status"))

    def on_close(self, ctx):
        if ctx.session_index != 0:
            return None
        day = ctx.data.cross_section()
        codes = day.loc[day.trade_status.eq(True), "code"].head(10)
        return TargetPortfolio(
            weights={code: 0.8 / len(codes) for code in codes},
            reason="initial_selection",
        )
```

策略实例在每次测试中重新建立。`self.params` 是本次策略参数的独立副本；运行配置不混入策略参数。

| 方法 | 责任 |
|---|---|
| `requirements()` | 声明模式、可读取行情字段、特征表、预热交易日及额外代码依赖 |
| `on_start(ctx)` | 初始化私有状态，不能提交订单 |
| `on_close(ctx)` | 返回 `None`、目标持仓或订单批次 |
| `on_execution(ctx, events)` | 根据成交、拒单、到期、撤单、权益及结算更新私有状态；不能即时提交交易 |
| `on_end(result_view)` | 返回诊断字典，不能改写标准结果 |

只有 `on_close` 必须实现，其他方法有默认行为。`ctx` 包含时点、相对交易日序号、只读日历、数据视图、账户快照、待处理订单、账户收益历史副本及独立 RNG。

策略决定排名、仓位、调仓、止损和冷却期；它不直接改现金、股数、可卖数量、费用或成交记录。

## 决策模式

**目标模式**为默认。`TargetPortfolio` 权重非负且总和不超过 1，余量保留为现金。完整目标中未列出的已持有证券目标为零；`None` 表示不产生新决策；空权重表示请求清仓。目标只生成订单，不能覆盖真实持仓。新目标撤销未完成旧目标，再按实际账户重算。

计划数量按信号收盘权益和价格换算，执行仍受开盘价格、费用、现金和实际成交约束，可能偏离目标。

**订单模式**使用 `DataRequirements(mode="orders")` 与 `OrderBatch`：

```python
from ashare_alpha.backtest import OrderBatch, OrderRequest, Side

orders = OrderBatch(orders=(
    OrderRequest("000001", Side.BUY, budget=10_000,
                 valid_sessions=3, client_id="entry-1",
                 tags={"sleeve": "A"}),
))
```

`quantity` 与 `budget` 必须二选一。预算只用于买入，包含费用上限；明确股数必须合法，不静默修正非法数量。卖出可指定 `lot_ids`。`OrderBatch(cancel=("O000001",))` 请求撤销有效订单。

有效期按交易日计数，从最早执行日开始。已用 `client_id` 不再接受重复提交。结束时未完成订单标记 `open_at_end`，保留原始请求、剩余量和原因。

## Python 接入与比较

```python
from ashare_alpha.backtest import MarketData, BacktestConfig, BacktestEngine
from strategy.demo import BuyAndHoldStrategy

config = BacktestConfig(start="2024-01-02", end="2024-01-12")
data = MarketData.from_catalog(
    "workspace/data/catalog/quant.duckdb",
    start=config.start, end=config.end,
    codes=["000001"],
)
result = BacktestEngine(data).run(
    BuyAndHoldStrategy,
    strategy_params={"code": "000001"},
    config=config,
)
result.save("/tmp/my-backtest", inputs=data)
```

`MarketData(bars, calendar=..., master=..., features=..., benchmark=...)` 支持合成测试和自有已规范化输入。行情至少包含唯一 `code/date`、`open/close`；执行还需要明确交易状态、边界质量及公司行为能力。不能用缺字段表示“正常交易”或“没有事件”。

特征表必须有唯一 `code/date`（也接受 `asof` 代替 date）及 `available_at`。通过 `DataRequirements(features=("scores",))` 声明后，`ctx.data.cross_section("scores")` 只返回当前决策日期、已可得的行；可额外检查 `model_available_at`、`labels_available_at`。CLI 的 `--features` 是名称到 Parquet 路径的 JSON。

`compare` 与 `parameter_grid` 可在共享固定输入上组织独立运行。比如在包含两个标的的输入中，用同一个 demo 分别指定两个 `code`，每次账户仍然只买入一个标的。策略比较的输入和参数须明确记录。

共享的是复制后固定的市场输入，每次仍有新策略实例、账户、订单与 RNG。失败运行不会冒充有效收益，批量比较可以继续收集其他运行。不同样本外区间使用独立 `BacktestConfig`；默认不会继承上一区间持仓。

## 执行与记账

- 完整交易日历驱动账户，缺信号或个股缺行情不会删除账户日期。
- 收盘信号最早下个交易日开盘执行；卖单先于买单，再按优先级、证券和订单 ID 稳定排序。
- 未完成卖单不产生现金。明确股数可部分成交，剩余量继续受有效期约束；预算买单默认产生一次按预算确定的合法成交，剩余零钱不代表额外信号；开启容量约束后，被额度截断的预算订单保留未用预算供有效期内后续执行。
- 原始开盘价加声明滑点后按人民币分取整，不使用当天收盘触板标记或当天高低价优化成交。有效边界的不利方向触边不成交。
- 普通板股数为 100 步长；科创板/CDR 买入至少 200、步长 1；北交所至少 100、步长 1。零股卖出只允许符合整仓退出规则的请求。
- 每笔买入生成独立 lot，普通新买入在下一交易日可卖；FIFO 为默认成本和持仓匹配方法，可指定待售 lot。
- 现金、费用与成本以整数分记账；策略看到的是不可修改的快照。
- 缺估值价采用最后可信价并标记 `stale_codes`，不产生可交易价格，也不会自动按最后价变现退市持仓。

## 执行配置与边界

`BacktestConfig` 的初始资金必须是整分，默认 100 万。`CostConfig` 默认佣金 2.5bp、最低 5 元，转让/印花税按成交日期适用；历史费率覆盖 2015-08-01 起。更早日期需明确设置 `historical=False` 及静态费率，不能把当前模型当成全历史税费复现。

默认 `SlippageConfig(bps=5)` 是固定不利滑点。可保留旧 `slippage_bps_for_row` 辅助接口，其市值门槛与输入统一使用人民币元，缺失指数标签不会当作真值。

可选 `CapacityConfig(participation_rate=0.001, lookback_sessions=20)` 将每只证券每天的买卖成交总金额限制为此前 20 个完整交易日成交金额中位数的 0.1%。所有订单、方向共享额度；只读取执行日前数据，历史缺失时延后订单。数量订单及受容量约束的预算订单支持分日部分成交，仍受有效期、股数规则、费用和现金约束。默认关闭，CLI 配置使用 `"capacity": {"participation_rate": 0.001, "lookback_sessions": 20}`。这只是基于历史日成交额的保守容量代理，不能证明开盘竞价可成交，也不使用执行日全天成交额优化开盘交易。

`corporate_actions="strict"` 要求现金分红的 `cash_pay_date`、送股的 `share_available_date`，除权日先确认应收权益，再到指定交易日结算。当前 canonical data 缺少这些完整日期，遇到持有事件可能失败。

显式 `corporate_actions="ex_date_proxy"` 使用除权日到账的研究近似，记录在元数据中。可选 `holder_actions` 输入保存有来源的独立权益事件，在停牌而没有价格行的除权日仍可入账；停牌期间按除权公式估值并继续标记陈旧报价，不冒充实测成交价。`dividend_tax_rate` 为固定近似税率，默认 20%；它不是按未来卖出持有期复现的递延征税。送股按证券合计向下取整，再按最大余数分配到批次，保留成本。未知权益继续明确失败。配股默认 `rights_policy="fail"`；显式选择 `"forfeit"` 表示不认购、不获得新增股数或虚构卖权现金，承担除权损失。

退市默认保留最后报价并标记陈旧估值。研究压力设置 `delisting_policy="zero_recovery"` 可在已知退市日起把股票估值降为零，保留持仓数量、记录减值且不创造卖出现金；这是零回收假设，不是实际退市清算收益。

`cash_rate` 默认 0，可使用声明的固定年化现金收益，按自然日对上期现金计息。也可提供 `MarketData(..., cash_yield=frame)` 并设置 `cash_yield="input"`：表包含唯一 `date`、年化小数 `rate` 与 `available_at`，只使用上一交易日收盘前可得的该日观测。缺失观测不前填，日账户保留 NULL `cash_rate` 与 `cash_rate_missing`，该部分不计息。输入序列参与内容指纹和快照。现金代理收益不等同于已验证的交易所回购产品收益。

日线模型不覆盖盘中排队和精确止损，当前不支持融资、空头或期货。财报最新修订、qfq 快照及 volume 单位等 data 使用边界继续保留，日期限制不能创造完整 PIT 数据。

## 结果与验证

结果包含 `daily`、`positions`、`trades`、`orders`、`attempts`、`ledger`、`actions`、`decisions`、`metrics` 和策略诊断。保存目录包含对应 Parquet 与 `summary.json`；对象列以 JSON 字符串保存。可选 `inputs/` 保存真实输入，用内容指纹检查加载与重跑；分类列的值、dtype 与排序声明随快照保存，以恢复 Parquet 未保留的布尔分类类型。

`BacktestError.result` 是失败处的部分账本与记录，`metrics` 为空。输入/配置本身无效在启动前报错，没有虚构的运行结果。

收益和风险使用完整账户曲线；基准可选，缺口只影响相对指标。首日收益从初始资金计算，回撤也包含初始高水位。年化按交易日数/`trading_days`（默认 242），Sharpe 按日均值/样本标准差且无风险率为零；基准完整时相对净值为策略净值/基准净值。

```bash
python -m pytest tests/backtest -q
```

已有四个独立示例策略：等权、趋势退出、分批轮换、模型评分。完整验证和实测见 [验证依据](VALIDATION.md)，责任边界见 [架构](ARCHITECTURE.md)。
