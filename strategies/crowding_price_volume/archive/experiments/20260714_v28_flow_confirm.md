# 2026-07-14 · v2.8 北向/两融市场流确认

## 动机

catalog 此前无北向/两融。按「软确认、不当主开仓」原则接入市场级资金流，优化 v2.7。

## 数据

| 表 | 源 | 说明 |
|----|----|------|
| `northbound_market_daily` | EM `RPT_MUTUAL_DEAL_HISTORY` 005/001/003 | 北向净买（亿元）；**2024-08-19 后 NET 断供为 NaN** |
| `margin_market_daily` | akshare `stock_margin_sse` | 上交所融资余额等，覆盖至样本末 |

ingest：`data/scripts/ingest_flow_data.py --catalog`  
面板：`cli/build_v28_cache.py` → `cache/signals_*_v28.parquet`

特征：`nb_weak` / `nb_strong`（z60 或 5 日净买阈值）、`mg_stress`（融资余额 5 日增速 z）。

## 消融（相对 v2.7 +109.7% / 0.93 / -19.7%）

| variant | 总收益 | Sharpe | MaxDD | 结论 |
|---------|--------|--------|-------|------|
| nb_soft | +99.9% | 0.89 | -19.2% | **伤收益，关** |
| nb_boost×1.10 | +114.1% | 0.94 | -18.6% | 有用 |
| mg_mild (×0.95+禁延) | +112.8% | 0.96 | -19.8% | 有用 |
| **nb×1.15 + mg_mild** | **+123.9%** | **1.00** | **-18.0%** | **默认** |
| nb_soft+* | 更差 | — | — | 勿叠 soft |

CSV：`experiments/ablations/ablation_v28.csv`、`ablation_v28b.csv`。

## 默认（v2.8）

- `enable_nb_flow_boost=True`，`nb_strong_size_mult=1.15`
- `enable_margin_stress=True`，`mg_stress_size_mult=0.95`，禁 extend，**不当 soft_stop stress**
- `enable_nb_flow_soft=False`

## 已知缺口

- 北向净买 2024-08 后 NaN → 该段 `nb_strong` 恒 False（中性）；两融仍生效
- 个股两融未 ingest（open_A 宇宙 ~2500 票，成本高）；后续可按持仓增量拉取
