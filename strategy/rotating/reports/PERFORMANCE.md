# rotating performance

区间：2021-01-01 至 2026-05-22；初始资金：1,000,000 元。

| 场景 | 年化收益 | Sharpe | 最大回撤 | 年化换手 |
|---|---:|---:|---:|---:|
| factor_historical | -0.34% | -0.03 | -32.09% | 5.56 |
| elastic_historical | 0.93% | 0.06 | -32.58% | 9.89 |
| gbrt_historical | 2.41% | 0.16 | -27.78% | 5.77 |
| ensemble_historical | -0.59% | -0.04 | -34.56% | 5.95 |
| factor_flat_20bp_roundtrip | -1.25% | -0.10 | -35.25% | 5.73 |
| elastic_flat_20bp_roundtrip | 1.22% | 0.08 | -32.88% | 10.38 |
| gbrt_flat_20bp_roundtrip | 3.15% | 0.21 | -26.71% | 6.09 |
| ensemble_flat_20bp_roundtrip | -0.37% | -0.03 | -34.47% | 6.04 |
| gbrt_flat_40bp_roundtrip | 2.58% | 0.17 | -27.78% | 6.24 |
| gbrt_flat_80bp_roundtrip | 1.39% | 0.09 | -29.98% | 6.55 |

## Rank IC

| 分数 | 日数 | 平均 Rank IC | 年化 ICIR | 正 IC 日占比 |
|---|---:|---:|---:|---:|
| factor_score | 1296 | 0.0751 | 13.10 | 80.2% |
| elastic_score | 1296 | 0.0835 | 13.35 | 81.8% |
| gbrt_score | 1296 | 0.0881 | 14.02 | 82.8% |
| ensemble_score | 1296 | 0.0817 | 13.50 | 81.4% |

ICIR 基于重叠的五日标签，仅作模型比较；显著性判断需使用重叠收益稳健误差。

结果仅用于研究，不构成收益承诺；正式结论必须同时检查数据覆盖、未成交积压、容量和未参与调参的封存区间。
