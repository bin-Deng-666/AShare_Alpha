# releases/

活版本序列（A 股实测执行环境）。历史 flat15bp 版本在 [`../archive/versions/`](../archive/versions/)。

| 版本 | 一句话 | 总收益 | 年化 | Sharpe | 最大回撤 | 报告 |
|------|--------|--------|------|--------|----------|------|
| **v1.0** | v3.3 alpha + 拆分费用 + 开盘分档滑点 | **217.60%** | **19.40%** | **1.42** | **-17.73%** | [`v1.0/`](v1.0/) |

当前默认 = **v1.0**（`config/params.py` / `reports/PERFORMANCE.*` / `cache/signals_*_v1.0.parquet`）。

发版：`python3 cli/archive_versions.py --version v1.0 --sync-latest`
