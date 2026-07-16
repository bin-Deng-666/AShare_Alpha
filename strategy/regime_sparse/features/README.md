# features/

| 文件 | 说明 |
|------|------|
| `build_panel.py` | DuckDB 四层面板 → parquet（`open_A` only） |
| `sota_factors.py` | 引擎内 turn_cv 排序加权 |

```bash
python3 cli/rebuild_panel_cache.py
```
