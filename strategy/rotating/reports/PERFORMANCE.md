# rotating performance

区间：2021-01-01 至 2026-05-22；初始资金：1,000,000 元。

| 场景 | 年化收益 | Sharpe | 最大回撤 | 年化换手 |
|---|---:|---:|---:|---:|
| factor_historical | -0.34% | -0.03 | -32.09% | 5.56 |
| elastic_historical | 5.47% | 0.36 | -25.05% | 10.91 |
| gbrt_historical | 2.54% | 0.18 | -26.43% | 5.83 |
| ensemble_historical | 0.90% | 0.07 | -27.68% | 5.41 |
| factor_flat_20bp_roundtrip | -1.25% | -0.10 | -35.25% | 5.73 |
| elastic_flat_20bp_roundtrip | 6.05% | 0.38 | -25.15% | 11.20 |
| gbrt_flat_20bp_roundtrip | 3.78% | 0.26 | -26.65% | 5.80 |
| ensemble_flat_20bp_roundtrip | 0.98% | 0.08 | -27.42% | 5.53 |
| gbrt_flat_40bp_roundtrip | 3.25% | 0.22 | -27.33% | 5.94 |
| gbrt_flat_80bp_roundtrip | 2.16% | 0.14 | -29.36% | 6.23 |

## Rank IC

| 分数 | 日数 | 平均 Rank IC | 年化 ICIR | 正 IC 日占比 |
|---|---:|---:|---:|---:|
| factor_score | 1296 | 0.0751 | 13.10 | 80.2% |
| elastic_score | 1296 | 0.0852 | 13.52 | 82.0% |
| gbrt_score | 1296 | 0.0895 | 13.91 | 82.8% |
| ensemble_score | 1296 | 0.0824 | 13.52 | 81.2% |

## 模型训练标签 Rank IC

| 分数 | 日数 | 平均 Rank IC | 年化 ICIR | 正 IC 日占比 |
|---|---:|---:|---:|---:|
| factor_score | 1296 | 0.0751 | 13.10 | 80.2% |
| elastic_score | 1296 | 0.0852 | 13.52 | 82.0% |
| gbrt_score | 1296 | 0.0895 | 13.91 | 82.8% |
| ensemble_score | 1296 | 0.0824 | 13.52 | 81.2% |

ICIR 基于重叠的五日标签，仅作模型比较；显著性判断需使用重叠收益稳健误差。

结果仅用于研究，不构成收益承诺；正式结论必须同时检查数据覆盖、未成交积压、容量和未参与调参的封存区间。
