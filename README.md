# AShare_Alpha

A 股日频量化研究框架：数据仓库 → 信号面板 → 策略日荐。

## 目录结构

```text
AShare_Alpha/
├── run.py                  # 策略日荐入口（信号日 → 下一交易日开盘）
├── daily_update.py         # 盘后更新编排：数据层 + 资金流 + 面板重建
├── data/                   # A 股研究数据库
│   ├── scripts/            #   build_dataset / update / research_layer / flow
│   ├── config/             #   抓取范围与限流配置
│   ├── parquet/            #   原始层（行情 / 财务 / 公告 / 股本…）
│   ├── warehouse/          #   派生层（stock_daily / ASOF join）
│   └── catalog/            #   DuckDB 研究层（quant.duckdb）
├── backtest/               # 通用回测工具（metrics / costs / slippage）
├── strategy/               # 策略实现（见 strategy/README.md）
├── utils/                  # 日荐注册 / adapter / 仓位 / 落盘
└── output/                 # 日荐报告（按执行日）
```

## 快速开始

```bash
# 1. 盘后更新（数据层 + 资金流 + 信号面板，一站做完）
python3 daily_update.py
python3 daily_update.py --skip-slow       # 仅行情链路，跳过财务/公告
python3 daily_update.py --data-only       # 只更新库，不重建面板
python3 daily_update.py --workers 8       # 8 线程并行拉取

# 2. 策略日荐（daily_update 跑完后即可）
python3 run.py <strategy>
# → output/<strategy>/<exec_date>/report.md

# 3. 回测验证（在策略目录下）
cd strategy/<name> && python3 run_backtest.py
# → reports/PERFORMANCE.md + trades.csv + ledger_daily.csv
```

## 数据层

| 组件 | 说明 |
|------|------|
| `data/scripts/build_dataset.py` | 全量构建 / CLI 入口（`update-daily` 子命令） |
| `data/scripts/update.py` | 盘后增量管线（行情 / 财务 / 公告，支持多线程并行） |
| `data/scripts/research_layer.py` | 财务可得日 / warehouse / DuckDB catalog |
| `data/scripts/ingest_flow_data.py` | 北向资金 + 两融余额摄取 |

数据源：腾讯财经 / mootdx / akshare / BaoStock / 新浪 / 巨潮，三级回退保证可用性。
覆盖全 A 股（含退市）14 个数据层，DuckDB ASOF join 对齐事件滞后字段。

细节见 [`data/README.md`](data/README.md)。

## 回测工具层

`backtest/` 是策略无关的通用工具，任何策略都可以直接导入：

| 模块 | 用途 | 依赖 |
|------|------|------|
| `backtest.metrics` | Sharpe / Sortino / Calmar / Alpha / Beta / 胜率 / 盈利因子 / 分年统计 | 仅 numpy / pandas |
| `backtest.costs` | A 股佣金 / 印花税 / 过户费 / 分档滑点 / 手数取整 | 鸭子类型 params，不绑定任何策略 |

策略自己的回测引擎放在 `strategy/<name>/engine/`，通过 `from backtest.metrics import compute_metrics` 和 `from backtest.costs import trade_fee` 调用通用层。

细节见 [`backtest/README.md`](backtest/README.md)。

## 策略

策略实现规范见 [`strategy/README.md`](strategy/README.md)，策略复盘与经验沉淀见 [`strategy/LESSONS.md`](strategy/LESSONS.md)。

当前有一个实验策略 `crowding_price_volume`（双层量能 × 价量 × 拥挤 × 相对估值），注册名通过 `utils/adapters/` 自动注册。
