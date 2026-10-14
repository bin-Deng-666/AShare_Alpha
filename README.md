# AShare Alpha

A 股数据拉取与审计、日频回测和本地交易计划操作台。公开版本提供通用框架，以及一个**单标的买入并持有**示例。

## 快速开始

支持 macOS / Linux、Python 3.10–3.13。

```bash
git clone --single-branch --branch main https://github.com/bin-Deng-666/AShare_Alpha.git
cd AShare_Alpha
uv sync --frozen --extra dev
uv run ashare-alpha demo --output /tmp/ashare-demo
uv run ashare-alpha check --quiet
```

离线 demo 使用合成行情，买入 `000001` 后持续持有，输出账户曲线、成交记录和可重放输入，无需先下载行情。

## 项目结构

```text
AShare_Alpha/
├── src/                 # 框架源码，安装后导入为 ashare_alpha
│   ├── data/            # 数据获取、派生、审计与查询
│   ├── backtest/        # 撮合、账户、权益处理与绩效
│   ├── cli/             # 命令行入口
│   ├── web/             # 本地 API、页面与策略展示适配
│   └── research/        # 通用输入身份和结果发布工具
├── strategy/            # 具体策略；公开版本只包含 demo
├── tests/               # Python 回归与 browser/ 浏览器测试
├── docs/                # 使用说明、数据契约和架构决策
├── scripts/             # 质量检查、benchmarks/、mac/、legacy/
└── workspace/           # 本地生成：data/、artifacts/、jobs/、cache/
```

`src/` 通过打包配置映射为 Python 包 `ashare_alpha`，文件树无需再套同名目录。依赖统一由 `pyproject.toml` 与 `uv.lock` 管理；按需安装可用 `pip install -e '.[data,web]'`。

`workspace/` 和本地研究目录由 Git 忽略，安装包只收录明确声明的公共模块和 demo。目录变化与旧入口的替代方式见[迁移说明](docs/adr/003-source-layout.md)。

## 数据与操作台

```bash
uv run ashare-alpha data inspect
uv run ashare-alpha data plan
uv run ashare-alpha data update
uv run ashare-alpha data accept
uv run ashare-alpha web
```

浏览器打开 `http://127.0.0.1:8787`：选择策略，更新到最新已收盘交易日，查看下一交易日计划与对应的模型持仓。生成失败时保留上一份完整计划。

默认数据写入 `workspace/data/`。用环境变量切换工作区：

```bash
export ASHARE_WORKSPACE="$HOME/ashare-workspace"
uv run ashare-alpha web
```

策略输入与结果位于工作区的 `artifacts/strategies/`，任务状态位于 `jobs/`。macOS 双击入口在 `scripts/mac/`。

## 单标的策略示例

```bash
uv run ashare-alpha strategy backtest demo \
  --start 2024-01-02 --end 2024-01-31 --save-inputs --output /tmp/hold-run
uv run ashare-alpha plan demo --panel /tmp/hold-run/inputs
```

默认代码 `000001` 仅为示例，可用 `--params` JSON 更换。每次账户只买入一个标的；没有选股、择时或模型训练，详见 [demo](strategy/demo/README.md)。计划默认保存 `result.json` 与 `report.md`，需要交易和持仓 CSV 时加 `--csv`。

## 文档与开发

- [数据使用](docs/data/README.md)、[字段与可得日契约](docs/data/DATA_CONTRACTS.md)
- [回测接口](docs/backtest/README.md)、[账户与撮合架构](docs/backtest/ARCHITECTURE.md)
- [策略目录](strategy/README.md)、[操作台](docs/web.md)
- [贡献与验证](.github/CONTRIBUTING.md)、[变更记录](docs/CHANGELOG.md)、[安全报告](.github/SECURITY.md)

MootDX 的旧依赖单独保存在 `scripts/legacy/requirements-tdx.txt`，仅供独立旧版数据工具环境使用。

代码采用 [MIT License](LICENSE)。数据遵循供应商条款；当前修订快照不等于完整历史 PIT 数据。模型账户与实际交易账户应独立核对，执行与结算假设见回测文档。
