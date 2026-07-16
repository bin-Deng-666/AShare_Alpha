# cli/

| 命令 | 用途 |
|------|------|
| `python3 cli/rebuild_panel_cache.py` | 重建 `cache/signals_*_v0.1.parquet` |
| `python3 cli/run_backtest.py` | 全样本回测 → `reports/` |
| `python3 cli/run_backtest.py --start 2024-01-01` | 指定起点 |
| `python3 cli/run_backtest.py --rebuild` | 边建面板边回测 |

在 `strategy/regime_sparse/` 下运行。
