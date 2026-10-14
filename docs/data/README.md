# A 股研究数据

独立维护 A 股行情、财务、公告和公司行为，支持不定时补数、历史修订及本地研究查询。数据管线不包含选股、因子、组合或回测逻辑。

## 安装与运行

需要 Python 3.10+。在仓库根目录执行：

```bash
python -m pip install -e '.[data]'
python -m ashare_alpha.data --help
```

支持 macOS/Linux 的 Python 3.10–3.13。数据源依赖由根 `pyproject.toml` 的 `data` 组声明；也可运行 `uv sync --frozen --extra data`。`ASHARE_WORKSPACE` 选择数据与状态目录，默认配置来自安装包。

## 补数工作流

先查看计划，补数完成后检查质量，再开始研究：

```bash
python -m ashare_alpha.data inspect
python -m ashare_alpha.data status
python -m ashare_alpha.data plan --target 2026-10-10
python -m ashare_alpha.data update --target 2026-10-10 --workers 4 --parallel-tasks 4
python -m ashare_alpha.data validate
```

省略 `--target` 时使用配置截止日期或系统当天。更新会检查交易日历、源刷新周期、逐任务实现和成功记录；再次执行同一命令会复用已成功且未变化的工作。请求间隔、重试次数和并发设置不使已验证的数据事实失效。

分红、股本按公告与生效日期检测变化，只刷新变化证券的完整历史；默认重叠 7 天，每 30 天核对全市场历史。停更期间的公告从上次成功检查日续接，成功记录按批次交给财务，财务无需等全部公告结束。请求期数随实际缺口扩大。

需要重新检查历史数据时：

```bash
python -m ashare_alpha.data update --refresh-from 2024-01-01 --target 2026-10-10
```

这会回刷适用数据源并合并修订，保留区间外已有记录。它不是恢复历史数据版本：供应商返回的最新修订值与当时首次披露值可能不同。

完整联网验收使用：

```bash
python -m ashare_alpha.data accept --target 2026-10-10 --workers 4 --parallel-tasks 4
```

验收运行核心全市场更新，失败时续传重试一次；成功后重复执行，验证任务全部跳过，最后检查实际数据和刷新 manifest。所有条件满足才记录 `pass`。这项操作会抓取并发布真实数据，首次建立成功记录及补齐长时间缺口可能耗时较长。周末/节假日行情截止日由交易日历确定；公告等披露仍按请求截止日期检查。

局部维护和可选数据：

```bash
python -m ashare_alpha.data update --tasks prices indicators --codes 000001 --refresh-from 2024-01-01
python -m ashare_alpha.data update --tasks financials
python -m ashare_alpha.data update --tasks block_trades disclosure_trades holder_changes
python -m ashare_alpha.data update --tasks connect_margin cash_yield
python -m ashare_alpha.data catalog
```

`--tasks` 自动包含依赖。`--codes` 可以显式指定退市证券；子集执行的成功记录与全市场记录隔离。新增可选数据后，`catalog` 只从本地文件注册查询视图，不发起抓取。

初始化、外部文件导入及修复使用显式维护命令：

```bash
python -m ashare_alpha.data build --help
python -m ashare_alpha.data build bars --codes 000001
python -m ashare_alpha.data build import-simtradedata-indicators --source-root /path/to/release
```

`--force` 强制执行任务，适合排障；有明确的历史修订范围时优先用 `--refresh-from`。首次建立完整历史及成功记录通常比后续补数慢。

## 查询

```python
import duckdb

with duckdb.connect('workspace/data/catalog/quant.duckdb', read_only=True) as con:
    data = con.execute('''
        SELECT code, date, close, turnover_pct, pe_ttm
        FROM stock_daily
        WHERE date >= ?
        ORDER BY date, code
        LIMIT 100
    ''', ['2024-01-01']).fetchdf()
```

`stock_daily` 使用前复权价格，`stock_daily_raw` 使用未复权价格。财务、行业和公司行为字段必须结合可得日及质量标记使用，完整约定见 [数据契约](DATA_CONTRACTS.md)。

## 项目结构

| 路径 | 职责 |
|---|---|
| `src/data/` | 数据源、派生计算、交易规则、查询和任务编排 |
| `src/data/config/dataset.json` | 时间范围、请求预算、重试和核对周期 |
| `workspace/data/parquet/` | 标准化事实及通用派生资产 |
| `workspace/data/catalog/quant.duckdb` | 当前查询视图 |
| `workspace/data/state/` | 更新进度、输入指纹、失败原因及运行耗时 |
| `tests/data/` | 更新和发布行为测试 |
| `scripts/benchmarks/` | 隔离性能测量；部分命令会发起真实请求 |
| `workspace/data/manifest.json` | 数据库存摘要 |
| `workspace/data/quality_report.json` | 最近完整质量检查结果 |

实时日志输出到终端；批次结果写入 `state/runs/`，完整联网验收结果写入 `state/acceptance.json`。同一时刻只允许一个更新批次持有发布锁，批次内部按依赖并行执行。`--parallel-tasks` 控制同时运行的任务数，`--workers` 控制每个任务内部逐证券的并发数，默认均为 4；`--parallel-tasks 1` 可串行排障。这两个数字不是总体连接数，源任务还受各自限流和资源组约束，增加进程数不保证同比提速。质量检查也使用写锁，避免检查过程中数据被改动。

更新期间可在另一个终端执行 `python -m ashare_alpha.data status`，查看同时运行的任务。WebUI 或其他程序可以调用 `python -m ashare_alpha.data status --json`，或导入 `ashare_alpha.data.pipeline.status.read_status()`，读取 `latest_run.current_tasks`、目标日期、逐任务成功检查日期和质量报告是否仍适用于最新批次。进程中断留下的运行记录会显示为 `interrupted`；`checked_through` 不代表每个证券都有该日成交记录。

增量更新复用未变化产物和已经完成的源抓取。日度指标会同步保存 `preclose`，历史参考价缓存与新增指标覆盖完整时，公司行为校验不再重复回源；初次补齐历史缓存仍需时间。事件状态中的 `work` 可检查本次实际处理了多少证券、哪些参考价走本地或源查询。市场源记录中的 `last_work` 包含模式、窗口、变化证券及实际 HTTP 请求计数。

配置中 `event_overlap_days=7`、`event_full_refresh_days=30` 控制事件核对；`source_batch_size=50` 控制公告成功记录的交接批次。来源最小请求间隔当前为东财/CNInfo/Sina 各 0.25 秒；每个来源任务保留统一预算和失败退避。财务预算在交接批次间持续共享，不会随着批次数增多放大请求速率。

## 质量与验证

```bash
python -m pytest tests/data -q
python -m scripts.benchmarks.watermarks --limit 6000
python -m scripts.benchmarks.incremental_plan
python -m scripts.benchmarks.event_validation --limit 300
python -m scripts.benchmarks.parallel_tasks
python -m scripts.benchmarks.market_incremental
python -m scripts.benchmarks.financial_handoff
```

数据质量报告和运行结果分别保存，`python -m ashare_alpha.data status` 可以检查报告是否适用于最近一次运行。2026-10-10 截止批次已完成更新与复跑，行情实际到 2026-10-09：5,840 个证券、raw/qfq 各 14,364,445 行，涨跌停与每日股本覆盖全部 raw 日期。

质量状态仍为 `review`：001872、001914、302132 在 2012-09-10 的三行指标，经回查确认源端没有返回。对应估值和 ST/交易状态保持空值，缺口保存在 `state/indicator_source_gaps.json`，不能将它们改称完整观测。

股本按源生效日向后对齐，首次观测之前及源未提供的字段保留 NULL；总市值不使用流通市值替代。未知 ST、缺参考价、未支持事件和 OHLC 越界会保留涨跌停质量原因；不可用边界的触板标记为 NULL。回测接入应按这些条件决定可用范围，完整计数见验证依据。

- [架构](ARCHITECTURE.md)：模块边界、依赖和更新协议。
- [数据契约](DATA_CONTRACTS.md)：视图、字段、单位和时点限制。
- [验证依据](VALIDATION.md)：测试覆盖、真实文件测量及未验证范围。
