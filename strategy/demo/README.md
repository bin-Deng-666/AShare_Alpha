# crowding_price_volume

A 股日频策略：双层量能 × 价量 × 拥挤 × 相对估值，基准沪深300，本金账本回测。

唯一活版本：v1.0（策略参数 + 费用/滑点 + 富化信号面板同一版本号）。

> 历史面板流水线脚本名里的 `v26/v27/v28` 只是重建步骤，不是第二套策略版本。

## 文件树

```text
demo/
├── README.md
├── config/           # 参数分层（Core / Overlays / Dormant / Execution）+ 活版本 v1.0
├── features/         # 信号面板（build_panel + sota_factors）
├── engine/           # 策略级回测引擎（portfolio / entry / walkforward）
├── cli/              # 日荐 / 回测 / 面板重建 / 发版
├── lib/              # DB 连接 / 路径 / 市场数据加载
├── docs/             # STRATEGY + specs
├── cache/            # signals_*_v1.0.parquet（当前默认）
├── reports/          # PERFORMANCE.md + trades.csv + ledger_daily.csv
├── releases/         # 活版本序列（v1.0 …）
└── archive/          # 旧版 flat15bp、实验笔记、消融脚本
```

## 阅读顺序

1. [`docs/STRATEGY.md`](docs/STRATEGY.md) — 策略设计全貌
2. [`docs/specs/`](docs/specs/) — 决策矩阵、指标体系、费用口径
3. [`releases/`](releases/) — 版本与效果
4. [`reports/PERFORMANCE.md`](reports/PERFORMANCE.md) — 当前回测报告

## 回测指引

### 前置条件

- `data/catalog/quant.duckdb` 存在（由 `daily_update.py` 生成）
- `cache/signals_2020-01-01_v1.0.parquet` 存在（由 `rebuild_panel_cache.py` 生成）
- 回测从仓库根运行，确保 `backtest/` 和 `strategy/demo/` 都在 sys.path 上

### 运行回测

```bash
# 从仓库根运行
cd strategy/demo
python3 cli/run_backtest.py
# 默认：2020-01-01 起，100 万本金，v1.0 面板，真实费用 + 滑点
```

输出文件（写入 `reports/`）：

| 文件 | 内容 |
|------|------|
| `PERFORMANCE.md` | 完整回测报告（收益、风险、相对、交易、分年、消融复盘） |
| `trades.csv` | 交易明细（代码、方向、价格、份额、费用、已实现盈亏、持仓天数、出场原因） |
| `ledger_daily.csv` | 日度账本（NAV、现金、持仓数、仓位暴露、risk_on/false_bull 状态） |

### 重新构建信号面板

修改 `features/build_panel.py` 或任一 enrichment 脚本后，需要重建面板再回测：

```bash
cd strategy/demo
python3 cli/rebuild_panel_cache.py
# 内部链路：build_panel → sota → v26 → v27 → v28 → 写出 signals_*_v1.0.parquet
```

也可以从仓库根一键完成（数据更新 + 面板重建）：

```bash
python3 daily_update.py --force-panel
```

### 消融实验

验证某个参数或 overlay 的增量贡献时，使用同一面板只改一个变量：

```bash
cd strategy/demo

# 示例：关闭 Risk-On 暴露加成
python3 -c "
import sys; sys.path.insert(0, '.'); sys.path.insert(0, '../..')
import dataclasses
from config.params import DEFAULT_PARAMS
from engine.portfolio import run_portfolio
from features.build_panel import load_signals
from lib.market_data import load_bars, load_hs300
from backtest.metrics import compute_metrics
from pathlib import Path
import pandas as pd

sig = load_signals(Path('cache/signals_2020-01-01_v1.0.parquet'))
bars = load_bars('2020-01-01')
hs = load_hs300('2020-01-01')
names = sig.drop_duplicates('code').set_index('code')['name'].fillna('').astype(str).to_dict()

p = dataclasses.replace(DEFAULT_PARAMS, enable_risk_on=False)
r = run_portfolio(sig, bars, names=names, params=p, market=hs)
m = compute_metrics(r['daily'], r['trades'], hs, 1_000_000)
print(f'ret={m[\"returns\"][\"total_return\"]:.2%}  sharpe={m[\"risk\"][\"sharpe\"]:.2f}  dd={m[\"risk\"][\"max_drawdown\"]:.2%}')
"
```

历史消融脚本在 `archive/experiments/ablations/`，每个版本一个文件，记录了从 v2.0 到 v3.3 的完整迭代过程。

### Walk-Forward 验证

```bash
cd strategy/demo
python3 engine/walkforward.py
# 滚动 OOS 年度评估
```

### 命令速查

| 命令 | 用途 |
|------|------|
| `python3 cli/run_backtest.py` | 完整回测（v1.0 面板 + 真实费用） |
| `python3 cli/run_backtest.py --start 2022-01-01` | 指定起始日 |
| `python3 cli/rebuild_panel_cache.py` | 重建 v1.0 信号面板 |
| `python3 cli/run_daily.py` | 当日日荐（下一交易日开盘建议） |
| `python3 cli/archive_versions.py --version v1.0 --sync-latest` | 同步缓存到 releases/ |
| `python3 engine/walkforward.py` | 滚动 OOS 验证 |

仓库根日荐：`python3 run.py crowding_price_volume`

## 表现速览

v1.0（2020-01-02 → 2026-07-14，100 万本金，真实费用 + 滑点）：

| 指标 | 数值 |
|------|------|
| 总收益 | +143.3% |
| 年化 | 14.6% |
| Sharpe | 1.12 |
| 最大回撤 | -17.7% |
| Beta | 0.18 |
| 日均仓位 | 32.5% |
| 买入笔数 | 213 |

完整报告见 [`reports/PERFORMANCE.md`](reports/PERFORMANCE.md)，版本演进复盘见 [`archive/versions/README.md`](archive/versions/README.md)。

数据入口：`data/catalog/quant.duckdb`。
