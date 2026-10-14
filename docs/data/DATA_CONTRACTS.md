# 数据查询契约

研究通过工作区内的 `data/catalog/quant.duckdb` 查询 canonical Parquet（源码检出默认 `workspace/data/catalog/quant.duckdb`）。代码中的实际视图定义位于 `src/data/research/catalog.py`，以下说明关键字段和时点语义；可用 `DESCRIBE <view>` 获取本地完整字段。

## 通用约定

- 证券和指数代码为字符串，保留前导零，不用整数表示。
- `is_limit_up`、`is_limit_down` 在边界不可用时为 NULL，不能转成“没有触及涨跌停”。`limit_validation_status` 区分 `valid`、`not_applicable`（规则不限幅）、`missing_reference_price`、`unknown_st_status`、`unsupported_corporate_action` 和 `raw_ohlc_outside_limit`。不能把缺参考价或未知 ST 当作不限幅。
- `total_shares`、`float_shares` 及市值按源生效日期向后对齐；首次可信源记录之前保留日期键及 NULL，不用未来快照回填。总市值不使用流通市值替代，缺少股本数据时不以成交额/换手率冒充市值。
- 交易日、公告日、报告期为 DATE；抓取时间为 TIMESTAMP。
- OHLC、金额及比率使用 DOUBLE；股数使用 BIGINT；交易/ST 状态为 BOOLEAN。
- 股票日度百分比字段保存百分数值，如 `turnover_pct=5.2` 表示 5.2%。现金利率 `rate` 为年化小数，`0.016` 表示 1.6%。
- 行业/指数有效区间为 `valid_from <= date < valid_to`，空 valid_to 表示当前开放区间。
- `is_st`、`trade_status` 以及估值等指标，在源缺失时为 NULL；NULL 不能推断为非 ST、正常交易或零估值。已实际回查而源仍缺失的日期，记录在 `state/indicator_source_gaps.json`，并在质量报告中显示；不补造观测行。
- `security_master.status`：`listed` 为当前名单确认上市，`delisted` 为已知退市，`inactive` 为当前名单已不包含、但尚无可靠退市日期的历史证券。后两类历史资产保留；默认源增量任务只抓取 `listed`，显式证券修复可选择其他状态。
- 源日期覆盖、源检查截止日、财报可得日是不同概念，不能用 `max(date)` 代替完整性校验。

## 当前视图

| 视图 | 主键/粒度 | 关键字段与用途 |
|---|---|---|
| `security_master` | code | name、exchange、status、list_date、delist_date、org_id、source |
| `trade_calendar` | calendar_date | is_trading_day |
| `stock_daily` | code + date | qfq OHLCV、amount、估值、换手、交易状态、股本/市值、涨跌停及推导质量 |
| `stock_daily_raw` | code + date | 相同研究接口，价格为未复权行情 |
| `financial_statements` | code + report_date + statement_type + item_code | value、value_raw、year_on_year、available_date、availability_quality、source |
| `financial_availability` | code + report_date | 首次/最新公告日、availability_anchor_date、available_date、quality、matched_title |
| `financial_pit` | 已对齐可得日的财务记录 | 排除 available_date 为空或质量不符合视图规则的记录；使用时仍需按决策日期筛选 |
| `corporate_actions` | code + 报告期 + 事件日 + 方案 | record_date、ex_date、分红送转、披露日期、方案状态 |
| `industry_membership` | code + 分类/层级/行业 + valid_from | 分类体系、行业名称、有效区间、interval_quality |
| `index_membership` | index_code + code + valid_from | 指数成员的有效区间和质量 |
| `index_daily_bars` | index_code + date | index_name、OHLC、preclose、volume、amount、pct_change、source |
| `northbound_market_daily` | date | north/hgt/sgt/south 汇总金额（字段 `_yi` 为亿元）；部分北向字段 2024-08-19 后无披露 |
| `margin_market_daily` | market + date | SSE 两融汇总，余额/金额人民币元；不代表深沪全市场 |
| `lhb_detail` | date + code + reason | 买卖额、净额、原因；去掉上榜后收益等未来字段 |
| `block_trade` | date + code | 日度汇总成交量、金额、折溢价及笔数 |
| `holder_change` | code + 股东 + 方向 + 变动区间 + 公告日 | 公告日是可得性锚点，变动起止日不能作为提前可知日期 |
| `cash_yield_daily` | date | rate：Shibor O/N 年化小数利率。历史文件名 `gc001.parquet`，实际源不是 GC001 |

可选源文件不存在时，对应视图不创建。首次增加可选数据后执行 `python -m ashare_alpha.data catalog` 发布视图。

## 价格和公司行为

- raw 价格用于实际成交价格研究；qfq 用于收益和技术序列，但未来公司行为会改变历史前复权值。
- 复权、涨跌停共同依赖除权参考价；`limit_validation_status`、`corporate_action_formula_status` 表示推导质量，不应忽略。
- `corporate_actions/` 保存源方案记录；`corporate_action_events/` 保存校验后的可计算事件。两者职责不同，不是同一数据的无意义副本。
- 日线 volume 的供应商单位存在差异，当前没有把所有源的股/手口径统一验证完；金额字段的元口径优先使用，不能把 volume 默认当成已统一股数。
- 现有公司行为严格参考价校验覆盖沪深股票。北交所 qfq 来自供应商，重叠价格修订会触发历史重拉；其涨跌停由披露分红送转和 30% 规则计算，首个上市交易日不限幅，`reference_price_source=declared_distribution_formula` 标记披露公式推导。OHLC 越界记录保留但涨跌停值置空，不能把这类推导当作交易所参考价实测。

## 财务与时点

原始三表类型 `fzb/lrb/llb` 在查询层映射为 balance_sheet / income_statement / cash_flow。科目用 item_code 标识；中文名称可能重复，不能作为主键。value 采用源规范化后的人民币元数值。

没有日内发布时间的公告，保守地取公告日之后首个交易日为 available_date。匹配质量包括 exact、statutory_deadline、conservative_revision、heuristic_multiple、out_of_scope 等；严格回测应按所需质量筛选。

更新三表时保存最新成功观测值，同主键修订替换旧值。这是**最新修订数据集**，没有完整保存“每个历史时刻当时看到的财报版本”，不能把它等同于完整 PIT 版本库。行业/指数部分区间也来自历史快照推导，带 snapshot_derived 标记。

## 回测使用

```python
import duckdb
con = duckdb.connect('workspace/data/catalog/quant.duckdb', read_only=True)
print(con.execute('DESCRIBE stock_daily').fetchdf())
print(con.execute('SELECT code, date, close, pe_ttm FROM stock_daily WHERE date >= ? LIMIT 10', ['2024-01-01']).fetchdf())
con.close()
```

回测前先完成补数和 `python -m ashare_alpha.data validate`，记录使用的数据范围、质量结果和版本。`python -m ashare_alpha.data status --json` 的 `quality.current_for_latest_run` 表示审计是否晚于最新批次且当前没有写入；历史 `pass` 不能代表正在更新的数据已通过验收。完整验收结果另见 `state/acceptance.json`。

catalog 文件只保存视图定义，不冻结 Parquet；跨次更新精确复现旧结果需要另行保存实际输入数据，不能依赖已退役的旧 catalog 副本。
