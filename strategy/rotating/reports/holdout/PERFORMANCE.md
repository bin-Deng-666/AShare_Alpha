# rotating performance

区间：2024-01-01 至 2026-05-22；初始资金：1,000,000 元。

| 场景 | 年化收益 | Sharpe | 最大回撤 | 年化换手 |
|---|---:|---:|---:|---:|
| factor_historical | 7.32% | 0.52 | -16.96% | 5.01 |
| elastic_historical | 7.45% | 0.41 | -18.12% | 11.23 |
| gbrt_historical | 10.84% | 0.78 | -11.40% | 1.41 |
| ensemble_historical | 4.66% | 0.34 | -16.42% | 4.28 |
| factor_flat_20bp_roundtrip | 8.03% | 0.57 | -17.00% | 5.19 |
| elastic_flat_20bp_roundtrip | 7.75% | 0.42 | -18.14% | 11.37 |
| gbrt_flat_20bp_roundtrip | 10.53% | 0.78 | -10.87% | 1.38 |
| ensemble_flat_20bp_roundtrip | 6.47% | 0.47 | -16.46% | 4.34 |
| gbrt_flat_40bp_roundtrip | 10.38% | 0.77 | -10.93% | 1.38 |
| gbrt_flat_80bp_roundtrip | 10.08% | 0.74 | -11.03% | 1.39 |

## Rank IC

| 分数 | 日数 | 平均 Rank IC | 年化 ICIR | 正 IC 日占比 |
|---|---:|---:|---:|---:|
| factor_score | 569 | 0.0793 | 12.26 | 79.8% |
| elastic_score | 569 | 0.0871 | 12.33 | 80.3% |
| gbrt_score | 569 | 0.0912 | 12.57 | 80.7% |
| ensemble_score | 569 | 0.0863 | 12.50 | 79.6% |

## 模型训练标签 Rank IC

| 分数 | 日数 | 平均 Rank IC | 年化 ICIR | 正 IC 日占比 |
|---|---:|---:|---:|---:|
| factor_score | 569 | 0.0793 | 12.26 | 79.8% |
| elastic_score | 569 | 0.0871 | 12.33 | 80.3% |
| gbrt_score | 569 | 0.0912 | 12.57 | 80.7% |
| ensemble_score | 569 | 0.0863 | 12.50 | 79.6% |

ICIR 基于重叠的五日标签，仅作模型比较；显著性判断需使用重叠收益稳健误差。

结果仅用于研究，不构成收益承诺；正式结论必须同时检查数据覆盖、未成交积压、容量和未参与调参的封存区间。
