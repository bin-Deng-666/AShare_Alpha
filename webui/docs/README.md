# 下一交易日计划操作台

主流程是 **选择策略 → 更新至最新已收盘交易日 → 自动生成下一交易日买卖计划**。

## 启动

从仓库根安装：

```bash
uv sync --frozen --extra data --extra web
uv run --frozen --extra data --extra web ashare-alpha web
```

也可用 `pip install -e '.[data,web]'`。打开 `http://127.0.0.1:8787`；`--port` 可选择其他本地端口。macOS 双击根目录启动脚本使用 `.venv` 或显式 `ASHARE_PYTHON`。`ASHARE_WORKSPACE` 选择行情和运行状态所在工作区。

## 使用

1. 选择已接入策略。当前唯一公开示例为 `demo`，fixture 与归档不进入生产列表。
2. 点击 **更新数据并生成计划**。更新日历安排、核心行情与审计，再验证已安装 demo 契约，最后生成并提交完整计划。
3. 查看买入含费预算、卖出请求股数、信号日→执行日，以及同一份计划的模型持仓。

行情已更新并核验时，可使用“用已更新数据重新生成”。覆盖不足时按钮禁用，服务端拒绝请求。无买入/无卖出可以是有效结果。历史视图中的订单和持仓来自同一份已保存报告。

## 日期与质量

北京时间 15:00 为日线边界；收盘前只使用上一已收盘日，周末和节假日按真实交易日历处理。单独 calendar 任务查询未来 35 个自然日范围内已公布安排，用于确认执行日；行情和财务仍保持本次已收盘截止日期。日历未确认时执行日为 null。

任务开始固定本次截止日，更新完成后再确认信号日期。旧 checkpoint 不代替新日期检查。未覆盖或结构性错误阻止新计划，确认源缺口维持 review，并在计划保留提示。

## 发布与任务

计划写入 `plans/.revisions/<date>/<identity>/`，报告、JSON、订单和持仓完整后，原子提交 `plans/index.json`。最新、历史和模型持仓均由同一索引选择；未提交版本不可见。旧日期目录及旧 `plan.json` 兼容读取。

任务同一时间只运行一个。网页取消为 cancelled，服务退出为 interrupted；停止整组子进程并保存 checkpoint。重启后的旧 running 记录显示中断，可以重新执行更新以利用数据层 receipts 恢复。损坏 checkpoint 会显示异常，不冒充已完成。

`demo` 买入指定单一标的后持续持有，不提供择时、止损、换仓或模型训练。真实行情、其他研究策略及运行结果保留本地，公开页面和安装包仅提供这一示例。

模型账户不等于实盘账户。买入请求在下一开盘确定股数，未成交卖单仍保留真实模型持仓。

接口由 FastAPI `/docs` 提供可检查的 OpenAPI 描述。POST 请求严格验证字段和布尔类型，错误请求返回 422，未知策略返回 404，忙碌返回 409。

## API 与产物

| 操作 | 路径 |
|---|---|
| 策略与展示配置 | GET `/api/strategies`、`/api/strategies/{id}/config` |
| 更新并生成 | POST `/api/strategies/{id}/prepare` |
| 用已审计数据生成 | POST `/api/strategies/{id}/daily` |
| 数据状态 | GET `/api/strategies/{id}/status` |
| 最新/历史计划 | GET `/api/strategies/{id}/daily/latest`、`/history`、`/daily/{date}` |
| 模型持仓 | GET `/api/strategies/{id}/positions` |
| 任务与取消 | GET `/api/job/current`、POST `/api/job/cancel` |

`prepare` 的可选请求字段为 `resume: bool`。数据与研究入口保持独立：任务依次调用公开数据命令、已安装策略检查及计划生成，不自行实现选股、成交或账户算法。

工作区中的 `strategy/artifacts/<id>/datasets/` 为固定输入，`runs/` 为标准回测结果，`plans/` 为已提交计划；`webui/jobs/` 保存任务日志和原子 checkpoint。

## 验证

统一入口 `ashare-alpha check` 包含 API、北京时间、日历覆盖、流程顺序、覆盖不足、并发、取消与服务退出测试。跨层故障测试验证 CSV 写入和索引提交中断时旧计划、订单及持仓仍一致。浏览器回归覆盖策略切换、一键操作、历史视图与移动宽度。

开发、安装和浏览器命令见 [贡献说明](../../CONTRIBUTING.md)。真实联网更新与离线测试分开执行；已存旧计划不会因测试或任务失败变成当前建议。
