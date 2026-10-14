# ADR 003：源码、策略与工作区分离

状态：采纳。替代 ADR 001 中关于顶层领域包和源码目录内运行资产的布局决定；继续沿用其公开安装入口、单 demo 发布和工作区隔离原则。

## 决策

仓库顶层只承担明确职责：`src/` 是框架源码，`strategy/` 是具体策略，`tests/` 是框架测试，`docs/` 是文档，`scripts/` 是开发与维护工具，`workspace/` 是本地运行资产。

`pyproject.toml` 用 `package-dir = {ashare_alpha = "src", strategy = "strategy"}` 显式映射 Python 包。物理目录为 `src/backtest/`，导入名为 `ashare_alpha.backtest`；这不是传统 `src/ashare_alpha/` 布局。明确列出公共子包可防止私有策略被自动扫描进发行包；新增公共子包须同步更新列表。

具体策略仍位于 `strategy/demo/` 等目录。通用内容身份、运行配置和发布工具由 `src/research/` 提供。框架测试统一位于 `tests/`，浏览器测试与 Node 依赖放在 `tests/browser/`，私有策略可保留自己的测试目录。

## 入口迁移

| 旧入口 / 导入 | 当前替代 |
|---|---|
| `python -m data` | `ashare-alpha data` / `python -m ashare_alpha.data` |
| `python -m strategy` | `ashare-alpha strategy` |
| `python -m backtest` | `python -m ashare_alpha.backtest` |
| `python -m cli.run` | `ashare-alpha plan` |
| `python -m webui.server` | `ashare-alpha web` |
| `from backtest import …` | `from ashare_alpha.backtest import …` |
| `data.ashare_data.*` | `ashare_alpha.data.*` |
| `strategy.research.*` | `ashare_alpha.research.*` |
| `webui.app.*` | `ashare_alpha.web.*` |

`ashare-alpha`、`ashare-data`、`ashare-plan`、`ashare-web` 命令名称保持稳定。旧顶层模块不保留空壳转发包；本地旧研究的代码及输入归档保留，但旧模块路径不是当前支持的运行入口。运行前执行 `uv sync --frozen --extra dev`，不依赖手工 `sys.path` 注入。

## 工作区迁移

源码检出默认工作区改为仓库 `workspace/`；`ASHARE_WORKSPACE` 仍表示所选工作区根目录；已安装应用的默认根目录仍为 XDG 用户数据目录。

- 行情、DuckDB 视图、审计和更新状态：`<workspace>/data/`。
- 策略输入、结果和计划：`<workspace>/artifacts/strategies/<id>/`。
- 后台任务日志与 checkpoint：`<workspace>/jobs/`。
- 服务 PID 与锁：工作区根目录。

迁移工具 `scripts/migrate_workspace.py` 默认只显示计划，`--apply` 才搬移旧目录；目标已存在或任务正在写入时拒绝覆盖。移动数据后运行 `ashare-alpha data catalog`，重新生成指向新 Parquet 目录的查询视图。实际行情、审计和成功 receipts 不修改为新内容；代码身份变化可以触发下一次更新重新验证。

快照中记录的历史路径是来源说明，不应批量改写。已保存的输入可独立重放；代码模块身份变更导致新 run ID 是预期行为，不覆盖旧结果或重新命名已观察的封存区间。

## 文档与依赖

根目录保留 README、AGENTS、LICENSE、pyproject 和 lock。贡献与安全流程位于 `.github/`，CHANGELOG、使用说明和 ADR 位于 `docs/`。移除重复 requirements 安装入口，正常安装使用 extras；MootDX 独立依赖保留在 `scripts/legacy/requirements-tdx.txt`。

## 验证

迁移必须通过 Python 回归、浏览器回归、公开源码和 wheel 范围检查，并在仓库外安装 wheel 后验证 CLI、demo 和静态资源。数据目录迁移还需验证 catalog 的视图均指向当前工作区。所有私有研究和运行资产仍在 Git 忽略规则及显式打包白名单之外。
