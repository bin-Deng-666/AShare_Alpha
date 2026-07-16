# rotating performance

区间：2021-01-01 至 2023-12-31；初始资金：1,000,000 元。

| 场景 | 年化收益 | Sharpe | 最大回撤 | 年化换手 |
|---|---:|---:|---:|---:|
| factor_historical | -8.13% | -0.68 | -29.28% | 6.54 |
| elastic_historical | -5.59% | -0.40 | -31.07% | 12.16 |
| gbrt_historical | -1.94% | -0.12 | -25.04% | 12.11 |
| ensemble_historical | -7.19% | -0.60 | -29.16% | 7.18 |
| factor_flat_20bp_roundtrip | -8.91% | -0.74 | -31.31% | 6.52 |
| elastic_flat_20bp_roundtrip | -3.61% | -0.26 | -29.19% | 12.62 |
| gbrt_flat_20bp_roundtrip | -2.27% | -0.14 | -25.69% | 12.51 |
| ensemble_flat_20bp_roundtrip | -7.44% | -0.60 | -29.42% | 7.30 |
| gbrt_flat_40bp_roundtrip | -3.57% | -0.21 | -26.72% | 12.80 |
| gbrt_flat_80bp_roundtrip | -6.29% | -0.36 | -30.31% | 13.42 |

## Rank IC

| 分数 | 日数 | 平均 Rank IC | 年化 ICIR | 正 IC 日占比 |
|---|---:|---:|---:|---:|
| factor_score | 721 | 0.0712 | 14.04 | 80.4% |
| elastic_score | 721 | 0.0826 | 14.90 | 83.5% |
| gbrt_score | 721 | 0.0868 | 15.54 | 85.3% |
| ensemble_score | 721 | 0.0782 | 14.69 | 82.0% |

## 模型训练标签 Rank IC

| 分数 | 日数 | 平均 Rank IC | 年化 ICIR | 正 IC 日占比 |
|---|---:|---:|---:|---:|
| factor_score | 721 | 0.0622 | 12.43 | 78.4% |
| elastic_score | 721 | 0.0733 | 13.51 | 80.6% |
| gbrt_score | 721 | 0.0775 | 14.15 | 81.8% |
| ensemble_score | 721 | 0.0685 | 13.08 | 80.6% |

ICIR 基于重叠的五日标签，仅作模型比较；显著性判断需使用重叠收益稳健误差。

结果仅用于研究，不构成收益承诺；正式结论必须同时检查数据覆盖、未成交积压、容量和未参与调参的封存区间。
