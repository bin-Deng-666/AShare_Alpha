# AShare Alpha

A 股数据拉取、审计、通用回测与本地交易计划操作台。公开仓库只包含框架代码和一个 **单标的买入并持续持有** 的 demo。行情文件、模型、缓存、运行结果和其他研究策略保留本地。

## 快速开始

支持 macOS/Linux、Python 3.10–3.13。发布分支使用干净历史，克隆时选择该分支：

```bash
git clone --single-branch --branch main https://github.com/bin-Deng-666/AShare_Alpha.git
cd AShare_Alpha
uv sync --frozen --extra dev
uv run --frozen --extra dev ashare-alpha demo --output /tmp/ashare-demo
uv run --frozen --extra dev ashare-alpha check --quiet
```

离线演示用代码生成合成行情：只买入 `000001`，随后一直持有，输出账户曲线、成交记录和可重放输入。它不下载真实数据，也不包含择时、止损、选股因子或模型训练。

## 拉取真实数据

数据代码保留获取、规范化、更新恢复、公司行为和质量检查能力，真实数据在本地生成：

```bash
uv run --frozen --extra dev ashare-alpha data inspect
uv run --frozen --extra dev ashare-alpha data plan
uv run --frozen --extra dev ashare-alpha data update
uv run --frozen --extra dev ashare-alpha data accept
```

`ASHARE_WORKSPACE` 可把行情、审计、模型账户和日志放到独立目录：

```bash
export ASHARE_WORKSPACE="$HOME/ashare-workspace"
uv run --frozen --extra dev ashare-alpha web
```

操作台位于 `http://127.0.0.1:8787`：更新至最新已收盘交易日，再查看 demo 的下一交易日请求及当前模型持仓。确认买入后，后续正常计划显示持续持有和无卖出。

## 单标的 demo

```bash
uv run --frozen --extra dev ashare-alpha strategy backtest demo \
  --start 2024-01-02 --end 2024-01-31 --save-inputs --output /tmp/hold-run
uv run --frozen --extra dev ashare-alpha plan demo --panel /tmp/hold-run/inputs
```

默认标的 `000001` 仅为接口示例。更换标的可传 `--params` JSON，如 `{"code":"600000"}`；每次运行仍只有一个标的。策略文件见 [demo](strategy/demo/README.md)。成交、手续费、T+1、公司行为和账户均由公共引擎处理，模型账户需要与实盘独立核对。

## 结构与安装

| 位置 | 职责 |
|---|---|
| `data/ashare_data/` | 数据拉取、派生、审计与查询 |
| `backtest/` | 通用时钟、撮合、账户与结果 |
| `strategy/demo/` | 唯一公开策略：单标的买入并持有 |
| `strategy/research/` | 通用输入、结果和身份工具 |
| `ashare_alpha/` | 命令入口、工作区、策略注册与原子计划发布 |
| `webui/` | 本地 API 与操作台 |

依赖在 `pyproject.toml` 定义并由 `uv.lock` 固定。只安装应用可用 `pip install -e '.[data,web]'`；开发组 `dev` 包含测试、数据和 WebUI 依赖。MootDX 的旧依赖仅在独立 legacy 数据工具环境安装。已有 `python -m data/backtest/strategy/cli.run/webui.server` 入口保持可调用。

数据、模型、其他研究策略及工作区产物由 [.gitignore](.gitignore) 排除。测试数据使用合成 fixture，目录允许规则只发布 demo 及通用工具。公开安装包不包含本机行情和研究策略。

## 文档

- [数据使用](data/README.md)、[字段与可得日契约](data/docs/DATA_CONTRACTS.md)
- [回测接口](backtest/README.md)、[回测架构](backtest/ARCHITECTURE.md)
- [策略示例](strategy/demo/README.md)、[WebUI 使用](webui/docs/README.md)
- [贡献与验证](CONTRIBUTING.md)、[变更记录](CHANGELOG.md)、[安全报告](SECURITY.md)

代码使用 [MIT License](LICENSE)。数据遵循供应商条款，当前修订快照不声明为完整历史 PIT 数据。支持日频多头现金模型，成交与结算假设见回测文档。
