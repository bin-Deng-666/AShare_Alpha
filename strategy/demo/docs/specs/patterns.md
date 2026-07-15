# 规格：个股价量形态

只使用少量可量化形态；**成交额 `amount` 为主**，成交量为辅。单独形态不得下单，必须与双层量能、拥挤、估值联合（见 [`decision_matrix.md`](decision_matrix.md)）。

---

## 1. 输入

- 前复权 OHLC、`amount`（`daily_bars` qfq）
- 可选：换手 `turnover_pct`（`daily_indicators`）

---

## 2. 预计算特征（示意）

| 特征 | 定义 |
|------|------|
| `amt_pct_N` | 当日 amount 近 N 日分位 |
| `amt_ma_ratio` | 短均额 / 长均额 |
| `close_vs_ma` | close / MA_m - 1 |
| `dist_high_N` | close / 近 N 日最高 - 1 |
| `upper_shadow_ratio` | (high - max(open,close)) / (high-low+ε) |
| `ret_k` | 近 k 日收益 |

具体窗口在实现参数表固定；本文件约束形态语义，不锁死每个 magic number。

---

## 3. 形态枚举

| ID | 语义 | 判定要点（示意） | 主要用途 |
|----|------|------------------|----------|
| `thrust_up` | 价强且额能同步 | 价格强势/创新高区，amount 分位或短均抬升 | 买点 B；持仓确认；买点 A 的第二步 |
| `stall_high` | 高位滞涨 | 接近近期高点，涨幅收敛，额能不创新高或走弱 | 减仓；买点 C 的龙头条件 |
| `climax_volume` | 高潮放量 | 额能极端放大，涨幅收敛或长上影 | 减仓/不追；买点 C 龙头条件 |
| `break_down_vol` | 放量破位 | 跌破关键均线/支撑且额能放大 | 回避/减仓 |
| `dry_up_base` | 缩量企稳 | 回调中额能显著萎缩，价格收稳 | 买点 A 的第一步 |
| `rotation_wake` | 补涨启动 | 相对龙头落后后，价格转强且额能从低位抬升 | 买点 C 候选 |

互斥与优先级（同日多标签时）：

1. `break_down_vol` > `climax_volume` > `stall_high`  
2. `thrust_up` 与 `dry_up_base` 一般不同日；若冲突以更严格定义为准并打日志  
3. `rotation_wake` 可与弱 `thrust_up` 并存，实现时输出主标签 + 辅标签  

---

## 4. 硬约束

- 禁止增加第 7 个形态，除非先在 `archive/experiments/` 立项并完成对照检验。
- 禁止使用无法复现的「目测 K 线口诀」。
- 涨跌停日：形态可算，但可交易性由组合层拦截。

---

## 5. 输出字段

每日每票：

- `pattern_primary`, `pattern_secondary`（可空）
- 支撑特征原值
- `pattern_version`（与本规格版本一致）

---

## 6. 验收

- 单元测试：人造 OHLC/amount 序列能稳定打出预期标签  
- 事件研究：`stall_high`/`climax_volume` 后前瞻收益应弱于 `thrust_up`（全样本或分 regime）
