# ADR 001：公开安装入口与兼容领域包

状态：布局部分已由 [ADR 003](003-source-layout.md) 替代；以下保留原始决策背景。

数据、回测、研究及命令入口已有消费者和可重放快照，移动所有代码会同时改变导入身份和研究代码身份。本次使用一个 Python 发行包 `ashare-alpha`，新增 `ashare_alpha` 公开应用入口、工作区配置、策略能力与计划提交契约，现有 `data`、`backtest`、`strategy`、`cli` 和 `webui` 保留为明确打包的领域包。

WebUI 完整使用 `webui.app` 导入；不使用 `sys.path` 注入。wheel 只包含框架、数据拉取代码、唯一 demo 与所需资源，其他研究策略、归档、测试与运行数据不进入应用安装包。现有 `python -m data/backtest/strategy/cli.run/webui.server` 保持可调用，新用户使用 `ashare-alpha` 及独立 console scripts。

工作区由 `ASHARE_WORKSPACE` 选择。源码检出默认使用当前仓库布局，安装包默认使用 XDG 用户数据目录；source config/SQL/静态文件从包资源读取，运行文件写入工作区。选择工作区不需要迁移现有市场数据。

未来调整领域包命名需要明确兼容版本和迁移工具，不能通过批量改目录丢失历史输入与运行身份。
