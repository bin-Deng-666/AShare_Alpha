# 实验：20260713_v22_smart_hold_cooldown

| 项 | 内容 |
|----|------|
| 实验 ID | `20260713_v22_smart_hold_cooldown` |
| 对照 | v2.1 |
| 目标 | 降 max_hold 磨盘、提胜率；避免重复踩坑 |

## 试过但未默认采纳

| 模块 | 结果 |
|------|------|
| progress_exit（到期前进度不足就砍） | 过严，收益明显下滑 |
| ATR / structure 硬止损 | 样本内伤害 expectancy |
| pyramid 加仓 | 与上述叠加后变差 |
| failure_hold_ok_streak=3/5 | 大量误杀 |

## 采纳为 v2.2 默认

1. **`enable_smart_max_hold`**：到期时小盈/持有健康 → 半减；否则全平（替代一刀切 max_hold）
2. **`enable_stop_cooldown`**：止损/失效后同票冷却约 20 日

代码保留 ATR / progress / structure / pyramid，默认关。

## 消融摘要（同面板）

| 变体 | 总收益 | Sharpe | 胜率 |
|------|--------|--------|------|
| v2.1 复现 | ~+69% | ~0.66 | ~54% |
| cooldown only | ~+76% | ~0.72 | ~54% |
| **smartMH + cooldown（采纳）** | **~+69%** | **~0.66** | **~68%** |

（最终以 `archive_versions` 冻结数字为准。）
