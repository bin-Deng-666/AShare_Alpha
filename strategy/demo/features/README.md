# features/

特征与信号面板由 `build_panel.py` 一次性在 DuckDB 中计算并落盘到 `../cache/signals_*.parquet`。

```bash
cd strategies/crowding_price_volume
python3 -c "from features.build_panel import build_signal_panel; build_signal_panel('2020-01-01')"
```

数据源：`a_share_quant_data/catalog/quant.duckdb`  
行业：CSRC 一级区间（PIT）。估值科技组用 PS/PB 行业分位。
