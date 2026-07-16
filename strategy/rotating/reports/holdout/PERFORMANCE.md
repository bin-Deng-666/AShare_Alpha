# rotating performance

区间：2024-01-01 至 2026-05-22；初始资金：1,000,000 元。

| 场景 | 年化收益 | Sharpe | 最大回撤 | 年化换手 |
|---|---:|---:|---:|---:|
| factor_historical | 7.32% | 0.52 | -16.96% | 5.01 |
| elastic_historical | 9.93% | 0.55 | -16.24% | 8.67 |
| gbrt_historical | 12.08% | 0.82 | -10.51% | 1.42 |
| ensemble_historical | 4.13% | 0.33 | -14.48% | 3.75 |
| factor_flat_20bp_roundtrip | 8.03% | 0.57 | -17.00% | 5.19 |
| elastic_flat_20bp_roundtrip | 13.46% | 0.73 | -15.22% | 8.85 |
| gbrt_flat_20bp_roundtrip | 12.44% | 0.85 | -10.53% | 1.41 |
| ensemble_flat_20bp_roundtrip | 3.38% | 0.26 | -15.14% | 3.79 |
| gbrt_flat_40bp_roundtrip | 12.29% | 0.83 | -10.59% | 1.41 |
| gbrt_flat_80bp_roundtrip | 11.99% | 0.81 | -10.71% | 1.42 |

## Rank IC

| 分数 | 日数 | 平均 Rank IC | 年化 ICIR | 正 IC 日占比 |
|---|---:|---:|---:|---:|
| factor_score | 569 | 0.0793 | 12.26 | 79.8% |
| elastic_score | 569 | 0.0859 | 12.20 | 80.7% |
| gbrt_score | 569 | 0.0905 | 12.66 | 81.4% |
| ensemble_score | 569 | 0.0858 | 12.48 | 80.1% |

ICIR 基于重叠的五日标签，仅作模型比较；显著性判断需使用重叠收益稳健误差。

结果仅用于研究，不构成收益承诺；正式结论必须同时检查数据覆盖、未成交积压、容量和未参与调参的封存区间。
