# strategy/

策略实现目录。每个子目录是一个策略。

## 现有策略

| 目录 | 注册名 | 说明 |
|------|--------|------|
| [`demo/`](demo/) | `crowding_price_volume` | 完整实验策略（enrichment / 多 overlay） |
| [`regime_sparse/`](regime_sparse/) | 待注册 `regime_sparse` | demo 瘦身骨干 + T+1（冻结 v0.1） |

设计新策略前先读 [`LESSONS.md`](LESSONS.md)。

## 约定目录

```text
strategy/
├── README.md
├── LESSONS.md
└── <name>/
    ├── docs/           # STRATEGY / CHANGELOG / specs
    ├── versions/       # 冻结回测快照
    ├── experiments/    # 消融
    ├── cache/          # 信号面板
    ├── reports/        # 当前回测
    └── …               # 代码自由组织（建议 config/features/engine/cli/lib）
```

目录名宜等于注册名（`regime_sparse` 已对齐；`demo` 为例外）。不要用 demo 式 `archive/` + `releases/` 双轨，除非确有历史考古需要。

### docs /

版本记在 `CHANGELOG.md`；设计写在 `STRATEGY.md` 与 `specs/`。

### versions /

```text
versions/summary.csv
versions/<version>/   META.json · PERFORMANCE.md · trades.csv · ledger_daily.csv
```

活版本 params 在代码默认值；面板在 `cache/`。

### experiments /

同一面板只改一个变量；结论写回 `docs/CHANGELOG.md`。

## 回测

通用工具在仓库根 [`backtest/`](../backtest/)：

```python
from backtest.metrics import compute_metrics
from backtest.costs import trade_fee, apply_slippage
```

报告写入 `<name>/reports/`：`PERFORMANCE.md` · `trades.csv` · `ledger_daily.csv`（字段约定见 `backtest` / 各策略 README）。

```bash
cd strategy/<name>
python3 cli/run_backtest.py    # 或策略自定义入口
```

## 新建策略

1. 建 `strategy/<name>/` 与 `docs/` · `versions/` · `experiments/`
2. 实现 params / 面板 / 引擎 / CLI；`sys.path` 加入仓库根以用 `backtest/`
3. `utils/adapters/<name>.py` → `registry`
4. 在 [`LESSONS.md`](LESSONS.md) 追加复盘

## 日荐

`python3 run.py <strategy>` → `output/<strategy>/<exec_date>/`
