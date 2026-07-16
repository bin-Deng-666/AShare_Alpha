# CHANGELOG

## v0.1 — 骨干冻结（2026-07-16）

冻结至 [`../versions/v0.1/`](../versions/v0.1/)：

- 引擎：open_A + 主出场 + Risk-On + SOTA + crowd-traj + T+1
- 面板：四层 base，无 B/C / leader / enrichment 链
- params：`AlphaCore` + `Overlays` + `Execution`
- 回测：总收益 +141.5%，年化 14.5%，Sharpe 1.05，最大回撤 -14.9%，买入 210

中间重构（曾记为 0.1.1 瘦身）与冻结前指标 bit-exact，并入本版。

## v0 — 设计立项（2026-07-16）

- 新建 `regime_sparse`；目录对齐 `strategy/README` 约定
