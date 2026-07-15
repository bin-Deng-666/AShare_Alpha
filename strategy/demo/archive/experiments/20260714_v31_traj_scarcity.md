# 2026-07-14 · v3.1 拥挤轨迹 + 稀缺溢价

## 动机

v3.0 Risk-On 已把「有信号日」仓位拉到 ~77%，但大涨月捕获仍 ~28%——主因是 **open_A 信号干旱**（假牛满仓、真牛空仓）。

## 机制

1. **拥挤轨迹（Uncrowded Momentum）**  
   Risk-On 日允许 `mid` 拥挤且 5 日拥挤分上升、score≤0.65 的 open_A（仍禁 high/extreme）。
2. **稀缺溢价**  
   日开仓候选数处近 60 日低分位 → 单票 ×1.35（仅 Risk-On）；高分位 → ×0.85。
3. 赢家凸性 / 过松轨迹：消融未采用。

## 消融（相对 v3.0 +166.9% / 1.15 / -18.0%）

| 变体 | 总收益 | Sharpe | 2020 | 结论 |
|------|--------|--------|------|------|
| traj | +195.9% | 1.29 | +27.8% | 主贡献 |
| scarce alone | +159.6% | 1.14 | — | 单独不够 |
| **traj+scarce** | **+196.7%** | **1.32** | **+32.0%** | **默认** |
| +winner | +177.3% | 1.23 | — | 伤，关 |

CSV：`experiments/ablations/ablation_v31.csv`。

## 默认

- `enable_crowd_traj=True`
- `enable_scarcity_premium=True`
- 面板仍 `signals_*_v28.parquet`
