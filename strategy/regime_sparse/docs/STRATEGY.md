# 策略总述：regime_sparse

| 项 | 内容 |
|----|------|
| 策略 ID | `regime_sparse` |
| 目录 | `strategy/regime_sparse/`（= 注册名） |
| 频率 | 日频；信号日终 → **T+1 开盘** |
| 市场 | A 股可交易宇宙 |
| 活版本 | **v0.1**（已冻结 [`versions/v0.1/`](../versions/v0.1/)） |
| 上游 | `crowding_price_volume`（`strategy/demo`）+ [`LESSONS.md`](../../LESSONS.md) |

---

## 1. 定位

**双层量能择时壳** + **稀疏事件账本**。

从 demo 带走的是组织方式与交易纪律，不是绑死 `dry_up→thrust` 的配方名。买点配方在 v0.x 暂借以便跑通，标为可替换。

不赌：「业绩好看 → 产业一定涨」。

---

## 2. 相对 demo 的边界

**继承：** 四层分工、双层量能、稀疏账本、出场优先、Risk-On、消融纪律、真实成本、主回测自 2020。

**暂借（可换）：** `open_A` = dry_up→thrust；拥挤 / 估值分桶定义。

**不继承：** 假牛、MFE、B/C、dormant 开关堆、sota→v26→v28 enrichment 链、Vol Targeting 等连续暴露手法；目录不用 `archive/`+`releases/` 双轨。

---

## 3. Alpha 假设

1. 大盘×行业量能决定进攻窗口。  
2. 个股时机（v0：价量）决定何时动手。  
3. 拥挤 / 破位决定该不该收手。  
4. 相对估值做买侧门禁。  
5. 组合：稀疏持仓 + 现金 + 出场；Risk-On 只放大已有信号上的暴露。

---

## 4. 四层与闭环

| 层 | 职责 | v0.1 |
|----|------|--------|
| Regime | 能否新开、仓位乘数 | 已实现 |
| Timing | 何时买/持/减 | 价量形态（可换） |
| Risk | 收手 / 减仓 | 拥挤 + 形态（可换） |
| Quality | 买侧门禁与排序 | 相对估值（可换） |

买点默认仅 **open_A**。出场：stop / soft_stop / take_profit / trail / stall_take / failure / max_hold。Risk-On：好日子放大暴露，不放宽 funnel。

流水线：宇宙 → Regime → 个股特征 → `action` → 组合（行业分散、可选 Risk-On）→ T+1 清单。防御日允许零新开。

---

## 5. 交易假设

| 项 | 约定 |
|----|------|
| 基准 | 沪深300 |
| 成交 | T+1 开盘；先卖后买；涨停不买、跌停不卖 |
| 成本 | 佣金 + 印花税 + 过户 + 分档开盘滑点（`backtest/costs`） |
| 持仓 | 信号驱动；允许高现金 |

细节见 [`specs/execution.md`](specs/execution.md)。

---

## 6. 验证

主回测 / 调参：2020-01 起。Walk-forward / 压力年只读不调参。优化看近，压力看远。

---

## 7. 不做

裸绝对估值扫股；业绩→产业链主逻辑；本阶段深度学习选股；未消融转正 overlay；与 demo 共享可变代码。

---

## 8. 代码布局

```text
config/     AlphaCore / Overlays / Execution
features/   build_panel（四层，无 B/C）· sota_factors
engine/     portfolio（T+1）
cli/        run_backtest · rebuild_panel_cache
lib/        catalog / bars / panel path
```

尚未：`utils/adapters/regime_sparse.py`、日荐 `run.py regime_sparse`。
