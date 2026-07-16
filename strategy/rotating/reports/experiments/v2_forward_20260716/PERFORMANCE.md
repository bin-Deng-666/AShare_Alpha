# rotating performance

区间：2026-05-25 至 2026-07-16；初始资金：1,000,000 元。

| 场景 | 年化收益 | Sharpe | 最大回撤 | 年化换手 |
|---|---:|---:|---:|---:|
| factor_historical | 20.50% | 1.16 | -6.70% | 13.37 |
| elastic_historical | 0.57% | 0.03 | -9.20% | 26.37 |
| gbrt_historical | 8.01% | 0.54 | -6.04% | 6.10 |
| ensemble_historical | 16.67% | 1.01 | -6.77% | 11.76 |
| factor_flat_20bp_roundtrip | 20.64% | 1.16 | -6.69% | 13.38 |
| elastic_flat_20bp_roundtrip | 0.96% | 0.05 | -9.16% | 26.45 |
| gbrt_flat_20bp_roundtrip | 7.77% | 0.52 | -6.04% | 6.10 |
| ensemble_flat_20bp_roundtrip | 8.62% | 0.54 | -7.27% | 12.10 |
| gbrt_flat_40bp_roundtrip | 7.12% | 0.48 | -6.06% | 6.10 |
| gbrt_flat_80bp_roundtrip | 5.83% | 0.39 | -6.09% | 6.11 |

## Rank IC

| 分数 | 日数 | 平均 Rank IC | 年化 ICIR | HAC(4) t | 正 IC 日占比 |
|---|---:|---:|---:|---:|---:|
| factor_score | 32 | 0.0885 | 7.67 | 1.45 | 68.8% |
| elastic_score | 32 | 0.0897 | 7.41 | 1.41 | 68.8% |
| gbrt_score | 32 | 0.0952 | 7.37 | 1.38 | 65.6% |
| ensemble_score | 32 | 0.0911 | 7.42 | 1.40 | 68.8% |

## 模型训练标签 Rank IC

| 分数 | 日数 | 平均 Rank IC | 年化 ICIR | HAC(4) t | 正 IC 日占比 |
|---|---:|---:|---:|---:|---:|
| factor_score | 32 | 0.0885 | 7.67 | 1.45 | 68.8% |
| elastic_score | 32 | 0.0897 | 7.41 | 1.41 | 68.8% |
| gbrt_score | 32 | 0.0952 | 7.37 | 1.38 | 65.6% |
| ensemble_score | 32 | 0.0911 | 7.42 | 1.40 | 68.8% |

ICIR 基于重叠的五日标签，仅作模型比较；显著性判断需使用重叠收益稳健误差。

结果仅用于研究，不构成收益承诺；正式结论必须同时检查数据覆盖、未成交积压、容量和未参与调参的封存区间。
