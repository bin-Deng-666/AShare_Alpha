# crowding_price_volume

A 股日频策略：**双层量能 × 价量 × 拥挤 × 相对估值**，基准沪深300，本金账本回测。

**唯一活版本：v1.0**（策略参数 + 费用/滑点 + 富化信号面板同一版本号）。

> 历史面板流水线脚本名里的 `v26/v27/v28` 只是重建**步骤**，不是第二套策略版本。

## 文件树

```text
crowding_price_volume/
├── README.md
├── config/           # 参数分层（Core / Overlays / Dormant / Execution）+ 活版本 v1.0
├── features/         # 信号面板
├── backtest/         # portfolio + costs / entry / overlay / research_sizing
├── cli/              # 日荐、回测、面板、发版
├── lib/
├── docs/             # STRATEGY + specs
├── cache/            # signals_*_v1.0.parquet（当前默认）
├── reports/          # 当前 PERFORMANCE（与 v1.0 同步）
├── releases/         # 活版本序列（v1.0 …）
└── archive/          # 旧版 flat15bp、实验笔记、legacy 预设
```

## 阅读顺序

1. [`docs/STRATEGY.md`](docs/STRATEGY.md)
2. [`docs/specs/`](docs/specs/)
3. [`releases/`](releases/) — **版本与效果**
4. [`reports/PERFORMANCE.md`](reports/PERFORMANCE.md) — 当前报告

## 表现

| 文件 | 用途 |
|------|------|
| [`reports/PERFORMANCE.md`](reports/PERFORMANCE.md) | 当前默认（v1.0） |
| [`releases/v1.0/`](releases/v1.0/) | 冻结发布包 |
| [`archive/`](archive/) | 历史 flat15bp 版本与实验 |

## 命令

```bash
cd strategies/crowding_price_volume

# 日荐 / 回测默认都读 v1.0 面板
python3 cli/run_daily.py
python3 cli/run_backtest.py --start 2020-01-01

# 盘后重建 v1.0 富化面板（内部仍走 sota→…→最终写出 v1.0）
python3 cli/rebuild_panel_cache.py
python3 cli/archive_versions.py --version v1.0 --sync-latest
```

仓库根日荐：`python3 run.py crowding_price_volume`（同样默认 v1.0 面板）。

数据入口：`a_share_quant_data/catalog/quant.duckdb`。
