# Contributing

欢迎通过 GitHub issue 描述问题、复现条件和预期行为。实现变更请提交到独立分支，PR 描述应说明用户行为变化、验证结果和兼容性。

## 开发环境

支持 macOS/Linux，Python 3.10–3.13。从仓库根执行：

```bash
uv sync --frozen --extra dev
uv run --frozen --extra dev ashare-alpha check --quiet
uv run --frozen --extra dev pytest -q
uv run --frozen --extra dev ruff check .
uv run --frozen --extra dev ruff format --check .
python scripts/check_architecture.py
python scripts/check_docs.py
```

`dev` 包含维护中的测试与数据/WebUI 依赖。公开策略只有 `strategy/demo`，其他本地研究由 Git 忽略规则排除。`uv.lock` 固定本次可复现环境，依赖更新通过单独 PR 提交 lock 变化及 CI 结果。

## 边界

- data 提供市场事实和审计，不导入策略、CLI 或 WebUI。
- backtest 处理时钟、订单、账户和绩效，不导入具体策略。
- 公开示例在 `ashare_alpha/strategies.py` 声明身份和能力；本地研究不会自动发布，WebUI 配置只描述展示与编排。
- 运行产物通过 `ASHARE_WORKSPACE` 隔离；日志、行情、模型和当前质量报告不进入 Git。
- 订单不等于成交；未确认交易日、缺失披露、源 review 和模型账户边界必须保留。

## 验证

故障修复需要能复现问题的测试，尤其是取消/退出、原子发布、未成交账户状态和数据可得时刻。普通测试使用合成数据或受控源响应，不访问生产全市场数据。联网供应商验收单独记录目标日期、源版本、质量与失败情况。

前端测试：

```bash
npm ci --prefix webui
npm exec --prefix webui -- playwright install chromium
npm test --prefix webui
```

CI 覆盖 macOS/Linux 的 Python 环境、安装后的 wheel、离线示例和浏览器交互。PR 应保持这些检查通过。为数据金融计算模块修改格式或组织时，同时评估其代码身份与缓存失效范围。

## 发布

更新版本、CHANGELOG 和公开格式的兼容说明，先构建 wheel/sdist，再从仓库外安装并验证资源和离线命令。GitHub 分支推送不等于 PyPI 发布；本项目当前不自动上传 PyPI。
