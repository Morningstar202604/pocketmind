"""定时/条件触发：APScheduler(AsyncIOScheduler) + 官方 SQLAlchemyJobStore 持久化。

设计要点（P2 重构）：
1. 持久化改由 APScheduler 官方 SQLAlchemyJobStore 承担：job 的 trigger、func 引用、
   args/kwargs 全部 pickle 进 SQLite（apscheduler_jobs 表），进程重启后自动恢复调度，
   不再维护自写的 jobs 表。
2. 回调必须是「模块级、可导入」的函数（SQLAlchemyJobStore 会按引用 pickle/unpickle），
   因此定义顶层 ``_fire_job``；它只接收 job_id（位置参数）+ 字符串 kwargs，绝不绑定
   持有 store/runs 的闭包。真正执行 agent 的逻辑放在 main.py 的顶层函数
   ``run_agent_for_job``，触发时再延迟导入，避免循环依赖。
3. 业务字段（message/session_id/condition/name/trigger_type/expr/created_at）全部塞进
   job.kwargs（纯字符串），不新增扩展表，不改动 SQLAlchemyJobStore 的表结构。
4. 「停用」用 APScheduler 的 pause_job/resume_job 表达：停用即 next_run_time=None，
   持久化后重启仍保持停用状态。
"""
from __future__ import annotations

import time
import uuid
from collections.abc import Awaitable, Callable
from pathlib import Path

from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.date import DateTrigger
from apscheduler.triggers.interval import IntervalTrigger

# trigger_type 合法值
TRIGGER_TYPES = ("cron", "interval", "date")

# 条件操作符：支持 battery < 阈值（如 "battery < 20"）
CONDITION_OPS = ("<", ">", "<=", ">=", "==")


def parse_condition(cond: str) -> tuple[str, str, float] | None:
    """解析条件字符串 → (metric, op, value)；无法解析返回 None。"""
    if not cond or not isinstance(cond, str):
        return None
    s = cond.strip().lower()
    for op in CONDITION_OPS:
        if op in s:
            left, right = s.split(op, 1)
            metric = left.strip()
            try:
                value = float(right.strip())
            except ValueError:
                return None
            if metric not in ("battery",):
                return None
            return metric, op, value
    return None


async def _check_condition(cond: tuple[str, str, float]) -> bool:
    """条件求值：读手机电量与阈值比较。读不到电池时视为满足（避免任务静默丢失）。"""
    metric, op, value = cond
    try:
        from .tools.phone import get_battery  # 延迟导入避免循环

        info = await get_battery()
        pct = float(info.get("percentage", 0))
    except Exception:  # noqa: BLE001
        return True
    return {
        "<": pct < value,
        ">": pct > value,
        "<=": pct <= value,
        ">=": pct >= value,
        "==": abs(pct - value) < 0.5,
    }.get(op, True)


async def _fire_job(
    job_id: str,
    *,
    message: str = "",
    session_id: str = "",
    condition: str = "",
    name: str = "",
    **_: object,
) -> None:
    """APScheduler 触发入口（模块级、可被 pickle 的回调）。

    APScheduler 调用形式为 ``_fire_job(*args, **kwargs)``：args=[job_id]，其余业务字段
    从 kwargs 解包。真正执行 agent 的是 main.py 顶层函数 ``run_agent_for_job``，此处
    延迟导入以解开 main ↔ scheduler 的循环依赖。
    """
    from .notify import notify

    # 条件触发：不满足则跳过本轮（仍发一条通知留痕）
    parsed = parse_condition(condition)
    if parsed:
        ok = await _check_condition(parsed)
        if not ok:
            notify("口袋 Agent · 条件未满足", f"{name}：{condition}，本次跳过", persistent=False)
            return

    notify("口袋 Agent · 定时任务", f"正在执行：{name}", persistent=False)
    try:
        from .main import run_agent_for_job  # 延迟导入：避免与 main.py 循环依赖

        await run_agent_for_job(session_id, message)
    except Exception:  # noqa: BLE001 —— 定时任务异常不能影响调度器
        notify("口袋 Agent · 定时任务出错", name, persistent=False)


class SchedulerService:
    """AsyncIOScheduler + SQLAlchemyJobStore 的薄封装。

    对外保持 create/update/delete/list/get/delete_by_session/start/shutdown 接口不变，
    main.py 路由层无需感知底层从「自管 sqlite」换成了「官方 JobStore」。
    """

    def __init__(self, db_path: str | Path, notify_cb: Callable[[str, str], None] | None = None):
        # notify_cb 仅为兼容旧构造签名保留（当前 _fire_job 直接调 notify）；
        # 传入时也无害，这里暂存以便将来扩展。
        self._notify_cb = notify_cb or (lambda t, c: None)
        # 官方 SQLAlchemyJobStore：url 形式内部自建同步 engine，表 apscheduler_jobs 自动创建
        self._jobstore = SQLAlchemyJobStore(url=f"sqlite:///{db_path}")
        self._sched = AsyncIOScheduler(jobstores={"default": self._jobstore})

    # ---------- trigger 构造 ----------
    @staticmethod
    def _make_trigger(job: dict):
        tt = job["trigger_type"]
        if tt == "cron":
            return CronTrigger.from_crontab(job["expr"])
        if tt == "interval":
            return IntervalTrigger(seconds=max(int(float(job["expr"])), 5))
        # date：一次性，格式 "YYYY-MM-DD HH:MM"
        return DateTrigger(run_date=job["expr"])

    # ---------- job ↔ dict 转换 ----------
    @staticmethod
    def _job_to_dict(job) -> dict:
        """把 APScheduler Job 对象转成对外 dict（字段与旧自管 JobStore 对齐）。"""
        kw = dict(job.kwargs or {})
        nrt = job.next_run_time
        return {
            "id": job.id,
            "name": kw.get("name", job.id),
            "trigger_type": kw.get("trigger_type", ""),
            "expr": kw.get("expr", ""),
            "message": kw.get("message", ""),
            "session_id": kw.get("session_id", ""),
            "condition": kw.get("condition", ""),
            # 停用任务被 pause 后 next_run_time 为 None
            "enabled": nrt is not None,
            "created_at": kw.get("created_at", 0),
            "next_run": nrt.isoformat() if nrt else None,
        }

    # ---------- 公共 CRUD（接口与旧实现一致） ----------
    def start(self) -> None:
        """启动调度器：SQLAlchemyJobStore 自动加载已持久化的 job，未停用者恢复触发。"""
        if not self._sched.running:
            self._sched.start()

    def get(self, jid: str) -> dict | None:
        job = self._sched.get_job(jid)
        return self._job_to_dict(job) if job else None

    def list(self) -> list[dict]:
        return [self._job_to_dict(j) for j in self._sched.get_jobs()]

    def create(self, job: dict) -> dict:
        jid = job.get("id") or uuid.uuid4().hex[:12]
        # 业务字段全部进 kwargs（纯字符串，可 pickle）；args 只放 job_id
        kwargs = {
            "message": job["message"],
            "session_id": job.get("session_id", ""),
            "condition": job.get("condition", ""),
            "name": job["name"],
            "trigger_type": job["trigger_type"],
            "expr": job["expr"],
            "created_at": job.get("created_at", time.time()),
        }
        self._sched.add_job(
            _fire_job,
            trigger=self._make_trigger(job),
            id=jid,
            args=[jid],
            kwargs=kwargs,
            replace_existing=True,
            misfire_grace_time=300,
        )
        # 默认停用：立即 pause（next_run_time=None，持久化后重启仍停用）
        if not job.get("enabled", True):
            self._sched.pause_job(jid)
        return self.get(jid)

    def update(self, jid: str, patch: dict) -> dict | None:
        if self._sched.get_job(jid) is None:
            return None
        if "enabled" in patch:
            if patch["enabled"]:
                self._sched.resume_job(jid)
            else:
                self._sched.pause_job(jid)
        return self.get(jid)

    def delete(self, jid: str) -> bool:
        try:
            self._sched.remove_job(jid)
            return True
        except Exception:  # noqa: BLE001
            return False

    def delete_by_session(self, session_id: str) -> int:
        """删除某会话下全部任务（会话删除时级联清理）。"""
        removed = 0
        for job in self._sched.get_jobs():
            if (job.kwargs or {}).get("session_id") == session_id:
                try:
                    self._sched.remove_job(job.id)
                    removed += 1
                except Exception:  # noqa: BLE001
                    pass
        return removed

    def shutdown(self) -> None:
        try:
            if self._sched.running:
                self._sched.shutdown(wait=False)
        except Exception:  # noqa: BLE001
            pass
        try:
            self._jobstore.engine.dispose()
        except Exception:  # noqa: BLE001
            pass


# 便于类型标注：run_agent_for_job 的签名约定（main.py 提供实现）
RunAgentForJob = Callable[[str, str], Awaitable[None]]
