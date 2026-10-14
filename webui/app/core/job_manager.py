"""One strategy-scoped workflow: update data, verify it, then generate the next-session plan."""

from __future__ import annotations
import asyncio
import os
import re
import signal
import sys
from datetime import datetime, date, timedelta
from webui.app.core.config_loader import get_strategy_paths
from webui.app.core.schema import StageConfig, StrategyConfig
from webui.app.core.sessions import market_clock, closed_session
from webui.app.core import checkpoint as cp

PYTHON = sys.executable
ENV = {**os.environ, "PYTHONUNBUFFERED": "1"}
for key in ("PYTHONHOME", "PYTHONPATH"):
    ENV.pop(key, None)


class JobManager:
    def __init__(self):
        self.current_job = None
        self.process = None
        self.output_lines = []
        self._lock = asyncio.Lock()
        self._task = None
        self._steps_status = {}
        self._stage_configs = {}
        self._t_full_before = None
        self._target_date = None
        self._config = None
        self._substage = ""
        self._substep_detail = ""
        self._internal_step = self._internal_total = 0
        self._outer_step = self._outer_total = 0

    @property
    def is_busy(self):
        return self.current_job is not None and self.current_job["status"] == "running"

    async def start_update(
        self, cfg: StrategyConfig, resume=False, *, generate_plan=False
    ):
        return await self._start(
            cfg, "prepare" if generate_plan else "update", resume=resume
        )

    async def start_daily(self, cfg):
        return await self._start(cfg, "daily")

    async def _start(self, cfg, kind, *, resume=False):
        async with self._lock:
            if self.is_busy:
                return {
                    "error": "已有任务在运行中",
                    "job_id": self.current_job["job_id"],
                }
            clock = market_clock()
            self._target_date = clock["cutoff"]
            if kind == "daily":
                try:
                    self._signal_date(cfg)
                except ValueError as exc:
                    return {"error": str(exc)}
            # Verification also runs when reusing audited data; no cached check status.
            stages = (
                list(cfg.all_stages)
                if kind in ("update", "prepare")
                else list(cfg.post_update_checks)
            )
            if kind in ("daily", "prepare"):
                stages.append(
                    StageConfig(
                        "daily_plan",
                        "生成下一交易日计划",
                        "根据所选策略和模型持仓计算买卖",
                        [],
                        cfg.daily_plan_cwd,
                        cfg.daily_plan_timeout,
                    )
                )
            job_id = f"{kind}_{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}"
            self.current_job = {
                "job_id": job_id,
                "job_type": kind,
                "strategy_id": cfg.strategy_id,
                "status": "running",
                "stage": "准备任务",
                "target_date": self._target_date,
                "start_time": datetime.now().isoformat(timespec="seconds"),
                "log": [],
            }
            self.output_lines = []
            self._stage_configs = {stage.id: stage for stage in stages}
            self._steps_status = {stage.id: "pending" for stage in stages}
            self._reset_progress()
            from webui.app.core.t_full import _t_full

            self._t_full_before = _t_full(cfg)
            if resume and kind in ("update", "prepare"):
                previous = cp.read_checkpoint(cfg.strategy_id)
                if previous:
                    self.output_lines.append("重新核对数据 receipts 并运行策略检查。")
            self._config = cfg
            cp.persist_job(cfg.strategy_id, self.current_job)
            self._task = asyncio.create_task(self._run_steps(job_id, kind, cfg, stages))
            return {
                "job_id": job_id,
                "strategy_id": cfg.strategy_id,
                "target_date": self._target_date,
            }

    def _signal_date(self, cfg):
        if not cfg.daily_asof_argument:
            return None
        paths = get_strategy_paths(cfg)
        session = closed_session(paths["calendar"], cutoff=self._target_date)
        if not session["session"]:
            raise ValueError("交易日历尚未覆盖最新已收盘日期，请先更新数据。")
        from webui.app.core.t_full import _t_full

        observed = _t_full(cfg)
        if observed is None or observed < session["session"]:
            raise ValueError("行情或审计尚未覆盖最新已收盘交易日，请先更新数据。")
        return session["session"]

    def _resolve_tokens(self, tokens, cfg):
        paths = get_strategy_paths(cfg)
        target = self._target_date or market_clock()["cutoff"]
        values = {
            "calendar_through": str(date.fromisoformat(target) + timedelta(days=35)),
            "python": PYTHON,
            "repo_root": str(paths["repo"]),
            "strat_root": str(paths["strat_root"]),
            "panel_path": str(paths["panel"]),
            "strategy_id": cfg.strategy_id,
            "target_date": target,
        }
        resolved = []
        for token in tokens:
            for name, value in values.items():
                token = token.replace("{" + name + "}", value)
            if token.endswith(".py") and not token.startswith(PYTHON):
                resolved.append(PYTHON)
            resolved.append(token)
        return resolved

    def _resolve_command(self, stage, cfg):
        return self._resolve_tokens(stage.command, cfg)

    def _resolve_daily_command(self, cfg):
        return self._resolve_tokens(cfg.daily_plan_command, cfg)

    def _resolve_cwd(self, stage, cfg):
        return get_strategy_paths(cfg)["repo"] / stage.cwd

    def _reset_progress(self):
        self._substage = self._substep_detail = ""
        self._internal_step = self._internal_total = self._outer_step = (
            self._outer_total
        ) = 0

    def _checkpoint(self, cfg):
        if self.current_job["job_type"] in ("update", "prepare"):
            cp.write_checkpoint(
                cfg.strategy_id,
                self.current_job["job_id"],
                self._steps_status,
                self._t_full_before,
                target_date=self._target_date,
            )

    async def _stop_process(self):
        if self.process is not None and self.process.returncode is None:
            try:
                os.killpg(self.process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            try:
                await asyncio.wait_for(self.process.wait(), timeout=5)
            except asyncio.TimeoutError:
                try:
                    os.killpg(self.process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                await self.process.wait()

    async def _run_steps(self, job_id, kind, cfg, stages):
        try:
            for index, stage in enumerate(stages, 1):
                if self.current_job["status"] != "running":
                    return
                if self._steps_status[stage.id] == "completed":
                    continue
                self.current_job["stage"] = stage.label
                self._reset_progress()
                if stage.id == "daily_plan":
                    signal_date = self._signal_date(cfg)
                    command = self._resolve_daily_command(cfg)
                    if signal_date:
                        command += [cfg.daily_asof_argument, signal_date]
                else:
                    command = self._resolve_command(stage, cfg)
                self.output_lines.extend(
                    [
                        f"[阶段 {index}/{len(stages)}] {stage.label}",
                        f"命令: {' '.join(command)}",
                    ]
                )
                spawn = asyncio.create_task(
                    asyncio.create_subprocess_exec(
                        *command,
                        stdout=asyncio.subprocess.PIPE,
                        stderr=asyncio.subprocess.STDOUT,
                        cwd=str(self._resolve_cwd(stage, cfg)),
                        env=ENV,
                        start_new_session=True,
                    )
                )
                try:
                    self.process = await asyncio.shield(spawn)
                except asyncio.CancelledError:
                    self.process = await spawn
                    raise
                deadline = asyncio.get_running_loop().time() + stage.timeout
                while True:
                    remaining = deadline - asyncio.get_running_loop().time()
                    if remaining <= 0:
                        raise asyncio.TimeoutError()
                    line = await asyncio.wait_for(
                        self.process.stdout.readline(), timeout=remaining
                    )
                    if not line:
                        break
                    text = line.decode("utf-8", errors="replace").rstrip()
                    if text:
                        self.output_lines.append(text)
                        self.current_job["log"] = self.output_lines[-200:]
                        self._parse_substage(text)
                rc = await asyncio.wait_for(
                    self.process.wait(),
                    timeout=max(0.01, deadline - asyncio.get_running_loop().time()),
                )
                if self.current_job["status"] != "running":
                    return
                if rc:
                    raise RuntimeError(f"{stage.label}失败，退出码 {rc}")
                self._steps_status[stage.id] = "completed"
                self.output_lines.append(f"完成: {stage.label}")
                self._checkpoint(cfg)
            if self.current_job["status"] != "running":
                return
            self.current_job.update(
                status="done",
                stage="下一交易日计划已生成" if kind == "prepare" else "完成",
                end_time=datetime.now().isoformat(timespec="seconds"),
            )
            if kind in ("update", "prepare"):
                cp.clear_checkpoint(cfg.strategy_id)
        except asyncio.CancelledError:
            self.current_job.update(
                status="interrupted",
                error="服务退出，任务已中断，可重新更新以恢复。",
                end_time=datetime.now().isoformat(timespec="seconds"),
            )
            self.output_lines.append(self.current_job["error"])
            await self._stop_process()
            self._checkpoint(cfg)
            raise
        except Exception as exc:
            await self._stop_process()
            if self.current_job["status"] == "cancelled":
                return
            self.current_job.update(
                status="failed",
                error=str(exc) or "任务超时",
                end_time=datetime.now().isoformat(timespec="seconds"),
            )
            self.output_lines.append("失败: " + self.current_job["error"])
            if "stage" in locals():
                self._steps_status[stage.id] = "failed"
            self._checkpoint(cfg)
        finally:
            self.current_job["log"] = self.output_lines[-200:]
            cp.persist_job(cfg.strategy_id, self.current_job)
            self.process = None

    async def cancel(self):
        if not self.is_busy:
            return False
        self.current_job.update(
            status="cancelled", end_time=datetime.now().isoformat(timespec="seconds")
        )
        self.output_lines.append("任务已取消，原计划保留。")
        await self._stop_process()
        from webui.app.core.config_loader import get_strategy

        self._checkpoint(get_strategy(self.current_job["strategy_id"]))
        return True

    async def shutdown(self):
        """Stop the managed process group before the application event loop closes."""
        if self._task is not None and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        else:
            await self._stop_process()
        if self.is_busy:
            self.current_job.update(
                status="interrupted",
                end_time=datetime.now().isoformat(timespec="seconds"),
            )
            self._checkpoint(self._config)
            cp.persist_job(self._config.strategy_id, self.current_job)

    _RE_SUBSTEP = re.compile(r"^\[(\d+)/(\d+)\]\s*(.+)")
    _RE_INTERNAL = re.compile(r"(?:Step|feature step)\s+(\d+)/(\d+)\s*:?\s*(.*)", re.I)
    _RE_CODE_PROGRESS = re.compile(r"progress:\s*(\d+)/(\d+)")

    def _parse_substage(self, text):
        match = self._RE_SUBSTEP.match(text)
        if match:
            self._substage = match.group(3)
            self._outer_step = int(match.group(1))
            self._outer_total = int(match.group(2))
            return
        match = self._RE_INTERNAL.search(text) or self._RE_CODE_PROGRESS.search(text)
        if match:
            self._internal_step = int(match.group(1))
            self._internal_total = int(match.group(2))
            self._substep_detail = f"{self._internal_step}/{self._internal_total}"

    def get_state(self):
        if self.current_job is None:
            return {"status": "idle"}
        return {
            **{key: value for key, value in self.current_job.items() if key != "log"},
            "log_tail": self.output_lines[-80:],
            "total_steps": len(self._stage_configs),
            "completed_steps": sum(
                status == "completed" for status in self._steps_status.values()
            ),
            "stage_ids": list(self._stage_configs),
            "steps_status": dict(self._steps_status),
            "substage": self._substage,
            "substep_detail": self._substep_detail,
            "internal_step": self._internal_step,
            "internal_total": self._internal_total,
            "outer_step": self._outer_step,
            "outer_total": self._outer_total,
        }


jm = JobManager()
