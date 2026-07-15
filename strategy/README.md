# strategy/

策略实现目录。每个子目录是一个策略。

## 约定目录

```text
strategy/
├── README.md          # 本文件
├── LESSONS.md         # 策略复盘与经验沉淀（设计新策略前先读）
└── <name>/            # 每个策略一个目录
    ├── docs/          # 版本变更记录
    ├── versions/      # 每个版本的冻结报告
    ├── experiments/   # 消融实验脚本与结果
    ├── *.py           # 策略代码（自由组织）
    ├── cache/         # [自动] 信号面板 parquet
    └── reports/       # [自动] 当前回测报告
```

只约定三个目录，代码怎么组织是策略作者的自由。

### docs/

记录每个版本改了什么。一个 `CHANGELOG.md` 逐版本列出改动内容、消融结论、是否转正。策略设计文档（STRATEGY.md、因子定义等）也放这里。

### versions/

保存每个版本的冻结报告——即该版本回测结果最好的一次快照。按版本号建子目录：

```text
versions/
├── summary.csv        # 汇总表：版本 / 收益 / Sharpe / 回撤 / 买入数 / 成本口径
└── <version>/         # 冻结快照
    ├── META.json      #   版本元信息（params diff、面板路径、创建时间）
    ├── PERFORMANCE.md
    ├── trades.csv
    └── ledger_daily.csv
```

只有一个活版本，其 params 写在策略代码的默认参数中，面板缓存在 `cache/`。

### experiments/

每个版本的设计探索：消融实验脚本（`ablation_<version>.py`）和结果（`ablation_<version>.csv`）。验证某个改动是否有效，用同一面板只改一个变量。实验结论写回 `docs/CHANGELOG.md`。

## 回测工具

通用工具在仓库根 `backtest/`，策略无关：

```python
from backtest.metrics import compute_metrics        # Sharpe / Alpha / 胜率 / 分年
from backtest.costs import trade_fee, apply_slippage  # A 股费用 / 滑点 / 手数
```

详细 API 见 [`../backtest/README.md`](../backtest/README.md)。

## 回测报告

回测输出写入 `<name>/reports/`，三个文件。

### PERFORMANCE.md

人读报告，开头注明基准、回测区间、本金、成交假设，随后分以下段落：

**1. 资金与盈亏**

| 指标 | 说明 |
|------|------|
| 初始本金 | 回测起始资金 |
| 期末权益 | 最后交易日总权益 |
| 期末现金 / 期末市值 | 现金 + 持仓市值拆分 |
| 总盈亏 | 期末权益 - 初始本金 |
| 总收益率 | 总盈亏 / 初始本金 |
| 已实现盈亏 | 卖出累计实现损益 |
| 未实现盈亏（残差） | 总盈亏 - 已实现盈亏 |
| 总费用 | 全部交易费用合计 |

**2. 收益 vs 基准**

| 指标 | 策略列 + 基准列 |
|------|------|
| 累计收益 | 区间总收益 |
| 年化收益 | 几何年化 |
| 年化波动 | 日收益 std × sqrt(242) |
| Sharpe | 年化收益 / 年化波动 |
| Sortino | 年化收益 / 下行波动 |
| Calmar | 年化收益 / 最大回撤 |
| 最大回撤 | 峰值到谷值 |
| 最长回撤持续（交易日） | 回水所需天数 |

**3. 相对基准**

| 指标 | 说明 |
|------|------|
| 净值差 | 策略 NAV - 基准 NAV |
| 超额年化 | 超额净值年化 |
| 跟踪误差 TE | 超额收益 std × sqrt(242) |
| 信息比率 IR | 超额年化 / TE |
| Beta | 回归斜率 |
| Alpha 年化 | 回归截距 × 242 |
| 相关 | 相关系数 |
| 日超额胜率 | 超额 > 0 的日占比 |
| 超额最大回撤 | 超额净值回撤 |

**4. 交易与持仓**

| 指标 | 说明 |
|------|------|
| 买入 / 卖出笔数 | 交易次数 |
| 胜率 | 盈利卖出占比 |
| 平均盈利 / 平均亏损 | 单笔均值 |
| 盈亏比 | 平均盈利 / |平均亏损| |
| 盈利因子 | 总盈利 / |总亏损| |
| 平均 / 中位持仓天数 | 持仓时长 |
| 年化换手 | 双边成交额 / 均权益 / 年数 |
| 日均 / 最大持股数 | 持仓数量 |
| 日均现金占比 | 现金 / 权益 |
| 日均仓位暴露 | 1 - 现金占比 |

**5. 分年**

逐年列出策略收益、基准收益、超额收益。

**6. 过程文件**

指引 `ledger_daily.csv` / `trades.csv` / `PERFORMANCE.json` 的用途。

**7. 说明**

成交假设明细：T+1 开盘、100 股整数手；费用为佣金 + 过户费 + 卖出印花税；开盘分档滑点（按市值/成交额分大/中/小三档）。

### trades.csv

每笔交易一行：

| 字段 | 含义 |
|------|------|
| `date` / `code` / `name` | 交易日期 / 股票代码 / 名称 |
| `side` | BUY / SELL |
| `shares` / `price` / `amount` | 成交股数 / 价格 / 金额 |
| `fee` | 该笔费用（佣金 + 过户费 + 印花税） |
| `realized_pnl` | 已实现盈亏（仅卖出行） |
| `hold_days` | 持仓天数（仅卖出行） |
| `reason` | 出场原因（take_profit / trail_stop / mfe_exit / stop_loss / max_hold / ...） |

### ledger_daily.csv

每日一行：

| 字段 | 含义 |
|------|------|
| `date` | 交易日 |
| `equity` / `nav` | 权益（元）/ 净值（归一化） |
| `cash` / `market_value` | 现金 / 持仓市值 |
| `daily_ret` / `daily_pnl` | 日收益率 / 日盈亏（元） |
| `n_positions` | 当日持仓数 |
| `cash_ratio` | 现金占比 |
| 策略 overlay 列 | 策略特有的状态标记（如 risk_on / false_bull） |

## 回测流程

```bash
cd strategy/<name>

# 运行回测
python3 run_backtest.py
# → reports/PERFORMANCE.md + trades.csv + ledger_daily.csv

# 消融实验
python3 experiments/ablation_<version>.py
# → experiments/ablation_<version>.csv

# 冻结版本快照
python3 archive_versions.py --version <version>
# → versions/<version>/ 目录生成
```

## 新建策略

1. 在 `strategy/` 下新建目录 `strategy/<name>/`
2. 创建 `docs/`、`versions/`、`experiments/` 三个目录
3. 写策略代码（params、信号面板、回测引擎、CLI 入口），自由组织
4. 代码中通过 `from backtest.metrics import compute_metrics` 和 `from backtest.costs import trade_fee` 调用仓库根通用工具，需将仓库根加入 sys.path：`sys.path.insert(0, str(Path(__file__).resolve().parents[2]))`
5. 在 `utils/adapters/` 注册策略 adapter
6. 在 [`LESSONS.md`](LESSONS.md) 追加复盘

## 日荐与报告

- 运行：`python3 run.py <strategy>`（仓库根）
- 日荐报告：`output/<strategy>/<exec_date>/`
