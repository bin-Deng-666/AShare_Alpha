# A股日频量化研究库

目标范围：全A股（含可获取的退市股），**2010 年至今**。

当前状态：**全量构建已完成**，质量校验 `pass`。日常维护走盘后 `update-daily`。

| 项 | 数值（截至 2026-07-13） |
|---|---|
| 主表证券 | 5,792（上市 5,529 / 退市 263；SH+SZ 5,465 / BJ 327） |
| 日线区间 | 2010-01-04 → 2026-07-10（约 1,403 万行） |
| 各层覆盖 | 日线 / 指标 / 财务 / 公告 / 公司行为 / 股本 / 涨跌停 **100%** 对齐主表 |
| 研究入口 | `catalog/quant.duckdb` + `warehouse/`（约 2.9GB 分区宽表） |
| 物理层 | `parquet/`（约 3.8GB，按证券拆分，便于断点续传） |

说明：

- 已从研究宇宙剔除 **67** 只在 2010+ 窗口无日线、且上市日均早于 2010 的极早退市股；名单见 `state/purged_no_bars_securities.json`，`universe` 重建时会自动排除。
- 财报公告无记录时落**空文件**（与公司行为一致），保证覆盖率对齐；新股首份定报发布后由日更填入。
- 行情最新交易日取决于源站发布；周末/盘中跑日更不会凭空多出 K 线。

## 数据组织

```text
data/
├── config/                         抓取范围和限流配置
├── scripts/
│   ├── build_dataset.py            全量构建 / CLI 入口
│   ├── update.py                   盘后增量更新管线
│   ├── research_layer.py           财务可得日 / warehouse / DuckDB catalog
│   └── view_data.py                覆盖率与样例查看
├── parquet/                        按证券拆分的物理层（断点续传）
│   ├── security_master/
│   ├── daily_bars/                 adjustment=raw|qfq
│   ├── daily_indicators/           SH/SZ=BaoStock；BJ=派生
│   ├── financials/
│   ├── announcements/              定报公告；无记录可为空文件
│   ├── corporate_actions/
│   ├── equity_structure/
│   ├── shares_daily/
│   ├── limit_prices/
│   ├── market_snapshots/
│   └── reference/
│       ├── trade_calendar.parquet
│       ├── financial_availability.parquet
│       ├── industry_membership*.parquet
│       ├── index_constituent*.parquet
│       ├── index_bars/
│       └── industry_indices/
├── warehouse/                      按 year×exchange 压实的研究宽表
├── catalog/quant.duckdb            策略查询入口（逻辑视图）
├── state/                          失败项、日更状态、清理名单
├── logs/
├── raw/                            预留源归档
├── manifest.json
└── quality_report.json
```

物理层保留小文件以便续传；研究时优先用 DuckDB 视图，不要直接拼数千个单票 Parquet。

## 快速使用

```bash
# 覆盖率总览
python3 scripts/view_data.py
python3 scripts/view_data.py --code 600519 --rows 20

# DuckDB 查询（示例）
python3 <<'PY'
import duckdb
con = duckdb.connect("catalog/quant.duckdb", read_only=True)
print(con.execute("""
  SELECT date, code, close, pe_ttm, pb_mrq
  FROM stock_daily
  WHERE code = '600519'
  ORDER BY date DESC
  LIMIT 5
""").fetchdf())
PY

# 每个交易日盘后完整增量更新（推荐）
python3 scripts/build_dataset.py update-daily

# 仅快速更新行情链路（跳过公司行为/股本/财务/公告等慢层）
python3 scripts/build_dataset.py update-daily --skip-slow

# 只更新部分股票
# python3 scripts/build_dataset.py update-daily --codes 600519 000001
```

`update-daily` 默认刷新：

1. 股票池 / 上市日 / 交易日历  
2. 日线 raw（续写）+ qfq（回看刷新）  
3. SH/SZ 每日指标；BJ 派生指标与 pe/pb  
4. 公司行为、股本结构（强制刷新上市股）  
5. 财务三表（近几期合并）、财报公告（近 180 天合并）  
6. 财务可得日、涨跌停、每日股本  
7. 行业/指数参考与行情快照  
8. 当年 warehouse + catalog + validate  

全市场完整日更通常需数小时（东财/巨潮/新浪限流），适合盘后过夜。`--skip-slow` 只做行情链路。

**不要**用初始 `bars` / `indicators` 当日常更新——已有文件会整票跳过。

研究层若需单独重建：

```bash
python3 scripts/build_dataset.py financial-availability
python3 scripts/build_dataset.py bj-valuation
python3 scripts/build_dataset.py warehouse
python3 scripts/build_dataset.py catalog
python3 scripts/build_dataset.py validate
```

catalog 暴露的稳定视图：`security_master`、`trade_calendar`、`stock_daily`、
`financial_statements`、`financial_pit`、`financial_availability`、
`corporate_actions`、`industry_membership`、`index_membership`。

## 最终逻辑表契约

以下定义是 `catalog/quant.duckdb` 必须暴露的稳定接口。Parquet是物理存储，
DuckDB视图负责统一字段名和类型。策略代码只依赖本节的逻辑表，不依赖单个
Parquet文件推断出的类型。

统一约定：

- 股票代码和指数代码一律使用 `VARCHAR`，不得转为整数，避免丢失前导零。
- 交易日、报告期和生效日使用 `DATE`；精确抓取时点使用 `TIMESTAMP`。
- 金额默认人民币元，比例字段统一保存为“百分数数值”，例如 `5.2` 表示 `5.2%`。
- 价格、比率、金额使用 `DOUBLE`；股数使用 `BIGINT`；状态使用 `BOOLEAN`。
- `valid_to` 为开区间终点：`valid_from <= date < valid_to`；当前有效时为 `NULL`。
- 下列主键是逻辑约束。DuckDB读取视图不会自动强制唯一，构建时必须做唯一性检查。

### `security_master`

来源：`parquet/security_master/security_master.parquet`

逻辑主键：`code`

```sql
code          VARCHAR NOT NULL
name          VARCHAR
exchange      VARCHAR NOT NULL  -- SH、SZ、BJ
status        VARCHAR NOT NULL  -- listed、delisted
list_date     DATE
delist_date   DATE
org_id        VARCHAR           -- 巨潮机构ID
cninfo_name   VARCHAR
source        VARCHAR NOT NULL
```

`bj_list_date`只是当前抓取过程的临时源字段，最终逻辑表不保留；其有效值合并到
`list_date`。

### `daily_bars`

来源：

```text
parquet/daily_bars/adjustment=raw/{code}.parquet
parquet/daily_bars/adjustment=qfq/{code}.parquet
```

逻辑主键：`code + date + adjustment`

```sql
code          VARCHAR NOT NULL
date          DATE NOT NULL
adjustment    VARCHAR NOT NULL  -- raw、qfq
exchange      VARCHAR NOT NULL
open          DOUBLE
high          DOUBLE
low           DOUBLE
close         DOUBLE
volume        DOUBLE            -- 沿用源口径；不同源可能为股或手，入库前需校验
amount        DOUBLE            -- 人民币元
source        VARCHAR NOT NULL
```

`raw`永久保留，用于复权校验和公司行为研究；`qfq`用于多数收益率和技术指标研究。

### `daily_indicators`

来源：`parquet/daily_indicators/{code}.parquet`

逻辑主键：`code + date`

```sql
code             VARCHAR NOT NULL
date             DATE NOT NULL
turnover_pct     DOUBLE          -- 当前Parquet字段 turn
trade_status     BOOLEAN         -- 当前Parquet字段 tradestatus
pct_change       DOUBLE          -- 当前Parquet字段 pctChg
pe_ttm           DOUBLE          -- 当前Parquet字段 peTTM
pb_mrq           DOUBLE          -- 当前Parquet字段 pbMRQ
ps_ttm           DOUBLE          -- 当前Parquet字段 psTTM
pcf_ncf_ttm      DOUBLE          -- 当前Parquet字段 pcfNcfTTM
is_st            BOOLEAN         -- 当前Parquet字段 isST
source           VARCHAR NOT NULL
```

每日指标与日线按 `code + date` 左连接，不重复写入两套复权行情。

### `financial_statements`

来源：`parquet/financials/{code}.parquet`

可得日映射：`parquet/reference/financial_availability.parquet`

逻辑主键：`code + report_date + statement_type + item_code`

```sql
code             VARCHAR NOT NULL
report_date      DATE NOT NULL
statement_type   VARCHAR NOT NULL  -- balance_sheet、income_statement、cash_flow
item_code        VARCHAR NOT NULL  -- 新浪item_field；稳定科目标识
item             VARCHAR NOT NULL
item_display     VARCHAR
item_group_no    DOUBLE
value            DOUBLE
value_raw        VARCHAR
year_on_year     DOUBLE
available_date   DATE              -- 首次可用于回测的公告日，整合阶段生成
availability_quality VARCHAR        -- exact、heuristic、unmatched
source           VARCHAR NOT NULL
```

当前物理值 `fzb`、`lrb`、`llb` 分别映射为 `balance_sheet`、
`income_statement`、`cash_flow`。`available_date` 未可靠匹配前，该记录不得进入
严格时点回测。`value`为人民币元，`year_on_year`为百分数数值。由于当前公告数据
没有保留日内时刻，保守口径下 `available_date` 取公告日后的首个交易日。
中文 `item` 可能重名，因此不能作为唯一科目标识，必须使用 `item_code`。

### `announcements`

来源：`parquet/announcements/{code}.parquet`

逻辑主键：`code + announcement_id`

```sql
code                 VARCHAR NOT NULL
announcement_id      VARCHAR NOT NULL
announcement_date    DATE NOT NULL
title                VARCHAR
category             VARCHAR
adjunct_url          VARCHAR
source               VARCHAR NOT NULL
```

公告时间按中国标准时间转换后取自然日。财务数据与公告的匹配不能只依赖标题，
需要在整合阶段保留匹配规则和质量标记。

### `corporate_actions`

来源：`parquet/corporate_actions/{code}.parquet`

逻辑主键：`code + action_id`

```sql
code                       VARCHAR NOT NULL
action_id                  VARCHAR NOT NULL  -- 稳定自然键哈希，整合阶段生成
name                       VARCHAR
report_date                DATE
plan_notice_date           DATE
publish_date               DATE
notice_date                DATE
record_date                DATE
ex_date                    DATE
cash_dividend_per_10       DOUBLE
bonus_share_per_10         DOUBLE
transfer_share_per_10      DOUBLE
bonus_transfer_per_10      DOUBLE
total_shares               BIGINT
progress                   VARCHAR
plan_description           VARCHAR
source                     VARCHAR NOT NULL
```

`action_id`固定为以下四段标准化文本以 `|` 拼接后的SHA-256十六进制值：
`code`、`report_date`、`ex_date`、`trim(plan_description)`。日期使用
`YYYY-MM-DD`，空值使用空字符串。事件研究通常按 `ex_date` 连接，信息可得性研究
按最早有效公告日连接。

### `market_snapshots`

来源：`parquet/market_snapshots/{snapshot_date}.parquet`

逻辑主键：`snapshot_time + code`

```sql
snapshot_time         TIMESTAMP NOT NULL
code                  VARCHAR NOT NULL
name                  VARCHAR
price                 DOUBLE
last_close            DOUBLE
open                  DOUBLE
high                  DOUBLE
low                   DOUBLE
change_pct            DOUBLE
amount_wan            DOUBLE     -- 万元，保留腾讯源口径
turnover_pct          DOUBLE
pe_ttm                DOUBLE
pe_static             DOUBLE
pb                    DOUBLE
amplitude_pct         DOUBLE
market_cap_yi         DOUBLE     -- 亿元
float_market_cap_yi   DOUBLE     -- 亿元
limit_up              DOUBLE
limit_down            DOUBLE
volume_ratio          DOUBLE
source                VARCHAR NOT NULL
```

这是抓取时点快照，不能向历史日期回填。

### `trade_calendar`

来源：`parquet/reference/trade_calendar.parquet`

逻辑主键：`calendar_date`

```sql
calendar_date    DATE NOT NULL
is_trading_day   BOOLEAN NOT NULL
```

### `industry_membership`

来源：

```text
parquet/reference/industry_membership.parquet
parquet/reference/industry_indices/current_stock_membership.parquet
parquet/reference/industry_membership_intervals.parquet
```

逻辑主键：
`code + classification_system + industry_level + industry_code + valid_from`

```sql
code                     VARCHAR NOT NULL
classification_system    VARCHAR NOT NULL  -- CSRC、SW
industry_level           TINYINT NOT NULL
industry_code            VARCHAR NOT NULL
industry_name            VARCHAR
valid_from               DATE NOT NULL
valid_to                 DATE
snapshot_date            DATE NOT NULL
is_current_snapshot      BOOLEAN NOT NULL
interval_quality         VARCHAR NOT NULL  -- exact、snapshot_derived、current_only
source                   VARCHAR NOT NULL
```

证监会年度快照在整合阶段转换为有效区间。当前申万表只代表抓取日仍在成分中的股票，
不能据此恢复历史上已被移出的股票；它不得用于无幸存者偏差的历史回测。
当源没有正式行业代码时，`industry_code`固定生成为
`CSRC:{industry_classification}:{标准化行业名}`。

### `index_constituents`

来源：`parquet/reference/index_constituents.parquet`

区间表：`parquet/reference/index_constituent_intervals.parquet`

逻辑主键：`index_code + code + valid_from`

```sql
index_code       VARCHAR NOT NULL
code             VARCHAR NOT NULL
valid_from       DATE NOT NULL
valid_to         DATE
snapshot_date    DATE NOT NULL
interval_quality VARCHAR NOT NULL  -- exact、snapshot_derived
source           VARCHAR NOT NULL
```

当前源中的 `hs300`、`zz500` 统一映射为正式指数代码 `000300`、`000905`。
年度快照在整合阶段转换为有效区间。

### `index_daily_bars`

来源：

```text
parquet/reference/index_bars/{index_code}.parquet
parquet/reference/industry_indices/daily_bars/{index_code}.parquet
```

逻辑主键：`index_code + date`

```sql
index_code      VARCHAR NOT NULL
date            DATE NOT NULL
index_name      VARCHAR
index_type      VARCHAR NOT NULL  -- broad_based、industry
open            DOUBLE
high            DOUBLE
low             DOUBLE
close           DOUBLE
preclose        DOUBLE
volume          DOUBLE
amount          DOUBLE
pct_change      DOUBLE
source          VARCHAR NOT NULL
```

申万行业指数源没有 `preclose` 和 `pct_change` 时，可分别由前一交易日收盘价和
收盘收益率派生，但必须标记为派生字段。

### `industry_index_master`

来源：`parquet/reference/industry_indices/sw_level1_master.parquet`

逻辑主键：`index_code + snapshot_date`

```sql
index_code          VARCHAR NOT NULL
industry_name       VARCHAR NOT NULL
industry_level      TINYINT NOT NULL  -- 当前固定为1
constituent_count   BIGINT
pe_static           DOUBLE
pe_ttm              DOUBLE
pb                   DOUBLE
dividend_yield      DOUBLE
snapshot_date       DATE NOT NULL
source              VARCHAR NOT NULL
```

估值和成分数量是 `snapshot_date` 当日快照，不是历史序列。

### `equity_structure`

来源：`parquet/equity_structure/{code}.parquet`

东财 `RPT_F10_EH_EQUITY` 股本变动事件。`float_shares` 优先取流通A股
（`LISTED_A_SHARES`），否则回退无限售股份。

逻辑主键：`code + end_date`

```sql
code                VARCHAR NOT NULL
end_date            DATE NOT NULL
notice_date         DATE
change_reason       VARCHAR
total_shares        BIGINT
float_shares        BIGINT
unlimited_shares    BIGINT
limited_shares      BIGINT
listed_a_shares     BIGINT
free_shares         BIGINT
source              VARCHAR NOT NULL
```

### `shares_daily`

来源：`parquet/shares_daily/{code}.parquet`

优先由 `equity_structure` 按 `end_date` 向前填充总股本与流通股；若无股本变动史，
再回退到 `corporate_actions.total_shares`（此时流通股可能为空）。市值用同日未复权收盘价。

逻辑主键：`code + date`

```sql
code                VARCHAR NOT NULL
date                DATE NOT NULL
total_shares        BIGINT
float_shares        BIGINT
total_market_cap    DOUBLE
float_market_cap    DOUBLE
source              VARCHAR NOT NULL
```

### `limit_prices`

来源：`parquet/limit_prices/{code}.parquet`

由未复权昨收与板块/ST规则派生：主板10%、创业板注册制后与科创板20%、北交所30%、
ST 5%。首日或无昨收时涨跌停价为空。`is_limit_up` / `is_limit_down` 按收盘价与涨跌停价
（±0.01 容差）标记。

逻辑主键：`code + date`

```sql
code                VARCHAR NOT NULL
date                DATE NOT NULL
prev_close          DOUBLE
close               DOUBLE
limit_up            DOUBLE
limit_down          DOUBLE
limit_pct           DOUBLE
is_limit_up         BOOLEAN
is_limit_down       BOOLEAN
source              VARCHAR NOT NULL
```

### `factor_values`（未纳入本库）

逻辑主键：`code + date + factor_name + factor_version`

```sql
code              VARCHAR NOT NULL
date              DATE NOT NULL
factor_name       VARCHAR NOT NULL
factor_version    VARCHAR NOT NULL
raw_value         DOUBLE
clean_value       DOUBLE
industry_neutral  BOOLEAN NOT NULL
universe_name     VARCHAR
created_at        TIMESTAMP NOT NULL
```

因子采用长表保存，避免每新增一个因子就改变表结构；因子定义、参数和输入数据版本
必须由 `factor_version` 唯一确定。本仓库目前不生产因子表，由策略侧自行计算。

## 物理层与视图类型

策略应通过 `catalog/quant.duckdb` 读统一类型，而不是直接依赖单票 Parquet 推断结果。
已知物理层与逻辑层差异由 catalog 视图消化，例如：

- 公司行为每10股字段、日线 `volume` 等在部分文件中为整型，视图侧按契约转为 `DOUBLE`。
- BaoStock 指标驼峰字段在视图中重命名，0/1 状态转为 `BOOLEAN`。
- 申万映射的 `security_code` 在视图中统一为 `code`。
- `available_date` 保存在 `financial_availability`，不回写财务文件本体。
- 行业/指数有效区间由年度快照派生，带 `snapshot_derived` 质量标记。

## 数据连接规则

- 日线、每日指标、每日股本：按 `code + date` 精确连接。
- 财务数据：按 `available_date <= date` 取当时最新报告，禁止直接按报告期生效。
- 行业和指数成分：按 `valid_from <= date AND (valid_to IS NULL OR date < valid_to)`。
- 公司行为：收益复权按 `ex_date`，信息事件研究按最早有效公告日。
- 当前行情和申万成分快照：只能用于抓取时点，不能回填历史。

## 首次全量构建（已完成；仅重建时参考）

全量构建已跑通。若在空目录重做，按阶段执行（默认跳过已有文件；勿对全市场随意 `--force`）：

```bash
# 股票池
python3 scripts/build_dataset.py universe
python3 scripts/build_dataset.py listing-dates

# 少量股票验证
python3 scripts/build_dataset.py bars --codes 000001 600519 688017
python3 scripts/build_dataset.py financials --codes 000001 600519
python3 scripts/build_dataset.py announcements --codes 000001 600519
python3 scripts/build_dataset.py indicators --codes 000001 600519

# 全量任务
python3 scripts/build_dataset.py bars
python3 scripts/build_dataset.py snapshot
python3 scripts/build_dataset.py financials
python3 scripts/build_dataset.py announcements
python3 scripts/build_dataset.py corporate-actions
python3 scripts/build_dataset.py equity-structure
python3 scripts/build_dataset.py shares-daily
python3 scripts/build_dataset.py limit-prices
python3 scripts/build_dataset.py indicators
python3 scripts/build_dataset.py bj-indicators
python3 scripts/build_dataset.py bj-valuation
python3 scripts/build_dataset.py reference
python3 scripts/build_dataset.py industry-indices
python3 scripts/build_dataset.py sw-membership

# 修复与时点整合
python3 scripts/build_dataset.py repair-qfq
python3 scripts/build_dataset.py repair-financials
python3 scripts/build_dataset.py announcements-q1
python3 scripts/build_dataset.py financial-availability
python3 scripts/build_dataset.py reference-intervals

# 研究层与质量
python3 scripts/build_dataset.py warehouse
python3 scripts/build_dataset.py catalog
python3 scripts/build_dataset.py validate
python3 scripts/build_dataset.py manifest
```

不建议直接运行 `all`。日常维护请用文首的 `update-daily`。

## 查看数据

```bash
# 数据集整体进度
python3 scripts/view_data.py

# 展示每类数据的样例
python3 scripts/view_data.py --samples

# 查看某只股票的日线、每日指标、财务、公告和最新估值
python3 scripts/view_data.py --code 600519 --rows 20

# 搜索股票
python3 scripts/view_data.py --search 茅台

# 直接查看文件
python3 scripts/view_data.py \
  --file parquet/daily_indicators/600519.parquet \
  --rows 20

python3 scripts/view_data.py \
  --file parquet/reference/industry_membership.parquet \
  --rows 20
```

## 回测注意

1. 腾讯PE、PB和市值是抓取时点快照，不能回填到历史日期。
2. 财务因子必须按首次可用公告日期生效，避免未来数据。
3. 前复权序列会因未来公司行为变化，原始行情应永久保留。
4. 行业和指数成分必须使用历史快照，不能用当前成分回填历史。
5. 回测还需实现手续费、印花税、滑点、T+1、涨跌停和停牌限制。
6. 免费公开源无法完全复原历史简称、行业和退市证券，缺口应在质量报告中披露。
