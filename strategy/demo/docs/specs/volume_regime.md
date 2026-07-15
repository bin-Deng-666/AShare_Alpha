# 规格：双层量能 Regime（大盘 × 行业）

量能不是只看「整个市场」，而是：

- **大盘量能 `MktVol`**：总风险预算与能否进攻  
- **行业量能 `IndVol`**：资金是否进入该股所属板块  

个股价量见 [`patterns.md`](patterns.md)，不在本层合并成单一分数。

---

## 1. 输入

| 对象 | 建议口径 | 数据来源（规划） |
|------|----------|------------------|
| 大盘成交额 | 全 A 或沪深日成交额合计 | 日线 `amount` 加总 / 指数侧 |
| 行业成交额 | 申万一级（或实现选定的行业分类）成分股 `amount` 合计 | `industry_membership` + `daily_bars` |
| 相对吸筹 | `ind_amount / mkt_amount` | 上两者 |

股票映射到行业：当日有效的行业成员关系（区间表 `valid_from` / `valid_to`）。

---

## 2. 特征（日频）

对大盘与每个行业分别计算（符号：`x_amount`）：

| 特征 | 定义（示意） | 说明 |
|------|--------------|------|
| `amount_pct_N` | 今日额在近 N 日中的分位 | 默认 N=60 或 120，实现时固定 |
| `amount_ma_ratio` | `MA_short / MA_long` | 如 5/20 |
| `amount_chg_k` | 近 k 日额相对再前 k 日变化 | 扩张/萎缩速度 |
| `ind_vs_mkt_pct` | 仅行业：`ind/mkt` 的历史分位 | 相对大盘吸筹 |

参数表实现时放 `features/params`；变更走实验日志。

---

## 3. 分档枚举

### 3.1 大盘 `MktVolRegime`

| 值 | 示意条件（待校准） |
|----|-------------------|
| `expand` | 分位高且短均在长均之上，或连续放量抬升 |
| `neutral` | 中等分位，趋势不明 |
| `shrink` | 分位低或连续萎缩 |
| `climax_mkt` | 分位极端高（情绪高潮） |

### 3.2 行业 `IndVolRegime`

| 值 | 示意条件（待校准） |
|----|-------------------|
| `expand` | 行业额分位高，或 `ind_vs_mkt` 分位抬升 |
| `neutral` | 中性 |
| `shrink` | 行业额萎缩或相对大盘持续走弱 |
| `climax_ind` | 行业额能极端高潮 |

---

## 4. 组合矩阵 → 行为

行 = 大盘，列 = 行业。输出：`attack_mode` + `position_mult`（相对基准仓位乘数）。

| 大盘 \ 行业 | expand | neutral | shrink | climax_ind |
|-------------|--------|---------|--------|------------|
| **expand** | 主攻；买点 A/B 开；mult 高 | 可买；略降仓、价量更严 | 慎买；仓位严 | 主攻但禁追高潮买；提高减仓敏感 |
| **neutral** | 结构攻；偏 B/C；mult 中 | 仅高质量买点 A；mult 低 | 基本不新开 | 结构攻但防板块高潮 |
| **shrink** | 默认不新开（极弱反弹可观测） | 不新开 | **防御**：只减不理买 | 不新开 |
| **climax_mkt** | 禁追买；拥挤标的优先评估减仓 | 减仓权重↑ | 减仓/回避 | 强防御 |

`attack_mode` 建议枚举：

- `full_attack` / `structure_attack` / `quality_only` / `no_new_long` / `defend`

细则与买点的拼接见 [`decision_matrix.md`](decision_matrix.md)。

---

## 5. 优先级

1. 先算双层量能 → 得到 `attack_mode`、`position_mult`  
2. 再算个股形态 / 拥挤 / 估值  
3. 即便个股信号强，若 `attack_mode = no_new_long | defend`，**禁止新开多**（存量按减仓规则管理）

---

## 6. 输出字段（信号可解释）

每日每行业（及大盘一行）：

- `mkt_regime`, `ind_regime`, `attack_mode`, `position_mult`
- 关键特征原值（便于归因与监控漂移）

---

## 7. 验收

- 任意交易日 T，行业成员与额能只用 T 及以前数据  
- 对照回测：仅大盘 vs 双层量能，结构行情期应能拉开差异  
- `shrink×shrink` 日新开多次数应为 0
