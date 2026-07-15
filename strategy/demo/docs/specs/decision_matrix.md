# 规格：联合决策矩阵

将双层量能、价量、拥挤、估值合成为动作枚举。实现：`features/build_panel.py`（信号面板）+ `backtest/portfolio.py`（成交）。

**实现开关（v1.0）：** 面板可产出 `open_A/B/C`；组合与日荐默认仅消费 `open_A`（`enable_open_b=False`、`enable_open_c=False`，`allowed_attack_modes=("full_attack",)`）。下列 B/C 条件为规格；打开对应 flag 后再生效。

---

## 1. 动作枚举

| 动作 | 含义 |
|------|------|
| `open_A` | 买点 A：回撤后再启动 |
| `open_B` | 买点 B：量能扩张期强势确认 |
| `open_C` | 买点 C：补涨切换（卫星） |
| `hold` | 继续持有 |
| `reduce` | 减仓 |
| `avoid` | 不持有 / 清仓倾向 |
| `flat` | 无信号（不主动开仓） |

同日优先级（高到低）：`avoid` > `reduce` > `open_*` > `hold` > `flat`。  
`open_A/B/C` 冲突时：主仓优先 A/B（分高者），C 仅能进卫星桶。

---

## 2. 总开关：双层量能

先取 [`volume_regime.md`](volume_regime.md) 的 `attack_mode`：

| attack_mode | 允许的新开 | 仓位 |
|-------------|------------|------|
| `full_attack` | A、B、C | `position_mult` 高 |
| `structure_attack` | B、C，高质量 A | 中 |
| `quality_only` | 仅 A（条件更严） | 低 |
| `no_new_long` | 无 | 存量按持/减管理 |
| `defend` | 无 | 倾向 `reduce`/`avoid` |

---

## 3. 新开多条件

以下均需：可交易宇宙通过 + 估值门禁允许该买点 + `attack_mode` 允许。

### `open_A`

```text
attack_mode ∈ {full_attack, structure_attack, quality_only}
AND 近期出现 dry_up_base（窗口内）
AND 当日或确认日 thrust_up
AND crowding_bucket ∈ {low, mid}   # 自 high 回落更佳，实现可加「拥挤下行」标志
AND valuation_bucket ∈ {cheap, neutral}
```

### `open_B`

```text
attack_mode ∈ {full_attack, structure_attack}
AND 行业 IndVol 偏 expand（structure 时尤其需要）
AND pattern_primary = thrust_up
AND crowding_bucket ∈ {low, mid}   # 禁止 extreme；默认禁止 high
AND valuation_bucket ∈ {cheap, neutral}
```

### `open_C`（卫星）

```text
attack_mode ∈ {full_attack, structure_attack}
AND 同行业龙头（拥挤高或涨幅领跑）存在 stall_high 或 climax_volume
AND 本票 rotation_wake（可辅 thrust_up）
AND crowding_bucket ∈ {low, mid}
AND valuation_bucket ∈ {cheap, neutral}  # 对 expensive 更严，默认禁
AND 卫星仓位未超上限
```

---

## 4. 持仓 `hold`

```text
已持有
AND pattern 非 break_down_vol
AND 非（crowding high/extreme AND pattern ∈ {stall_high, climax_volume}）
AND attack_mode ≠ defend（defend 时改为评估 reduce）
AND thrust_up 仍成立 或 短窗内未转恶化
```

细则实现时可把「未转恶化」写成明确状态机。

---

## 5. `reduce` / `avoid`

### `avoid`（强）

```text
break_down_vol
OR (crowding_bucket = extreme AND pattern ∈ {stall_high, climax_volume, break_down_vol})
OR valuation_bucket = extreme_expensive AND crowding_bucket ∈ {high, extreme} AND 价量恶化
OR attack_mode = defend AND 个股价量恶化
```

### `reduce`（中）

```text
crowding_bucket ∈ {high, extreme}
AND pattern ∈ {stall_high, climax_volume}
AND 未达 avoid
加码：valuation expensive → 更大减仓比例
加码：行业 IndVol 从 expand 转为 shrink → 同板块降权
```

---

## 6. 解释字段（强制）

每条信号输出：

- `action`, `attack_mode`, `position_mult`
- `pattern_primary`, `crowding_bucket`, `valuation_bucket`
- `mkt_regime`, `ind_regime`
- `reason_codes[]`（如 `A_REBASE`, `B_THRUST`, `C_ROTATION`, `REDUCE_CROWDED_STALL`）

用于归因与实验对照，禁止「黑箱只出权重」。

---

## 7. 验收清单

- [ ] `defend` / `no_new_long` 日无新开  
- [ ] `open_B` 永不在 `crowding=extreme` 触发  
- [ ] 科技股 `expensive` 判定不依赖跨行业绝对 PE  
- [ ] 每笔模拟成交可追溯 reason_codes  
- [ ] 有买有卖：样本期内 `open_*` 与 `reduce/avoid` 均有足够触发次数
