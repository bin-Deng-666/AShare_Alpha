# rotating performance

区间：2021-01-01 至 2023-12-31；初始资金：1,000,000 元。

| 场景 | 年化收益 | Sharpe | 最大回撤 | 年化换手 |
|---|---:|---:|---:|---:|
| factor_historical | -8.13% | -0.68 | -29.28% | 6.54 |
| elastic_historical | -6.39% | -0.45 | -32.58% | 11.58 |
| gbrt_historical | -4.54% | -0.29 | -27.78% | 9.67 |
| ensemble_historical | -8.38% | -0.65 | -32.65% | 7.50 |
| factor_flat_20bp_roundtrip | -8.91% | -0.74 | -31.31% | 6.52 |
| elastic_flat_20bp_roundtrip | -5.89% | -0.40 | -32.88% | 11.82 |
| gbrt_flat_20bp_roundtrip | -2.97% | -0.19 | -26.71% | 10.29 |
| ensemble_flat_20bp_roundtrip | -8.23% | -0.64 | -32.48% | 7.58 |
| gbrt_flat_40bp_roundtrip | -4.06% | -0.25 | -27.78% | 10.47 |
| gbrt_flat_80bp_roundtrip | -6.29% | -0.38 | -29.98% | 10.87 |

## Rank IC

| 分数 | 日数 | 平均 Rank IC | 年化 ICIR | 正 IC 日占比 |
|---|---:|---:|---:|---:|
| factor_score | 721 | 0.0712 | 14.04 | 80.4% |
| elastic_score | 721 | 0.0812 | 14.59 | 82.5% |
| gbrt_score | 721 | 0.0855 | 15.57 | 83.8% |
| ensemble_score | 721 | 0.0779 | 14.67 | 82.2% |

ICIR 基于重叠的五日标签，仅作模型比较；显著性判断需使用重叠收益稳健误差。

结果仅用于研究，不构成收益承诺；正式结论必须同时检查数据覆盖、未成交积压、容量和未参与调参的封存区间。
