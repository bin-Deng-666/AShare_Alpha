# rotating performance

区间：2021-01-01 至 2023-12-31；初始资金：1,000,000 元。

| 场景 | 年化收益 | Sharpe | 最大回撤 | 年化换手 |
|---|---:|---:|---:|---:|
| factor_historical | -8.13% | -0.68 | -29.28% | 6.54 |
| elastic_historical | -1.00% | -0.07 | -25.05% | 11.56 |
| gbrt_historical | -2.83% | -0.18 | -26.43% | 10.14 |
| ensemble_historical | -5.74% | -0.47 | -26.10% | 7.12 |
| factor_flat_20bp_roundtrip | -8.91% | -0.74 | -31.31% | 6.52 |
| elastic_flat_20bp_roundtrip | -0.66% | -0.05 | -25.15% | 11.73 |
| gbrt_flat_20bp_roundtrip | -1.76% | -0.11 | -26.65% | 10.24 |
| ensemble_flat_20bp_roundtrip | -5.52% | -0.44 | -25.80% | 7.19 |
| gbrt_flat_40bp_roundtrip | -2.82% | -0.17 | -27.33% | 10.44 |
| gbrt_flat_80bp_roundtrip | -5.00% | -0.29 | -29.36% | 10.84 |

## Rank IC

| 分数 | 日数 | 平均 Rank IC | 年化 ICIR | 正 IC 日占比 |
|---|---:|---:|---:|---:|
| factor_score | 721 | 0.0712 | 14.04 | 80.4% |
| elastic_score | 721 | 0.0833 | 14.79 | 83.2% |
| gbrt_score | 721 | 0.0874 | 15.35 | 84.3% |
| ensemble_score | 721 | 0.0788 | 14.67 | 82.2% |

## 模型训练标签 Rank IC

| 分数 | 日数 | 平均 Rank IC | 年化 ICIR | 正 IC 日占比 |
|---|---:|---:|---:|---:|
| factor_score | 721 | 0.0712 | 14.04 | 80.4% |
| elastic_score | 721 | 0.0833 | 14.79 | 83.2% |
| gbrt_score | 721 | 0.0874 | 15.35 | 84.3% |
| ensemble_score | 721 | 0.0788 | 14.67 | 82.2% |

ICIR 基于重叠的五日标签，仅作模型比较；显著性判断需使用重叠收益稳健误差。

结果仅用于研究，不构成收益承诺；正式结论必须同时检查数据覆盖、未成交积压、容量和未参与调参的封存区间。
