# cache/

信号面板 Parquet（gitignore，体积大）。

**对外只有一个版本文件：`signals_2020-01-01_v1.0.parquet`**（与策略 v1.0 同号）。

| 文件 | 用途 |
|------|------|
| `signals_2020-01-01_latest.parquet` | 基础面板（`features/build_panel` 产出，中间输入） |
| `signals_2020-01-01_v1.0.parquet` | **当前默认**（富化完成态：SOTA→涨停/行业→黑窗/指数→北向两融） |

`cli/build_sota_cache.py`、`build_v26/v27/v28_cache.py` 是重建流水线的**内部步骤名**（历史遗留），最终产物一律写成 `*_v1.0.parquet`。中间 `sota/v26/v27`（及旧名 `v28`）重建后可删。

```bash
# 从基础面板重建当前 v1.0
python3 cli/rebuild_panel_cache.py
```

资金流数据需先：`python3 ../../a_share_quant_data/scripts/ingest_flow_data.py --catalog`
