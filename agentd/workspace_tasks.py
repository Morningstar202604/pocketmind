"""工作区后台任务：把长命令从「HTTP 请求一直挂着」改成可查询、可取消的任务。

边界：
- 只管理工作区里由人显式启动的命令；Agent 的 run_shell 仍走 Agent 自己的调用链；
- 进程由 `run_shell` 启动，取消时它会回收整个进程组，避免 `npm run dev` 留孤儿；
- 任务元数据落 `$AGENT_HOME/workspace_tasks/*.json`，只保留最近 100 条；
- 输出在任务结束后一次性读取（最多 30KB）。真正的流式日志留给下一阶段。
"""
from __future__ import annotations

import asyncio
import json
import os
import shlex
import time
import uuid

from .paths import home_dir
from .tools.shell import run_shell

_MAX_TASKS = 100
_MAX_SERIALIZED = 50


def _now() -> float:
    return time.time()


def _public(rec: dict) -> dict:
    return {k: v for k, v in rec.items() if not k.startswith("_")}


def _persist(rec: dict) -> str | None:
    try:
        root = home_dir() / "workspace_tasks"
        root.mkdir(parents=True, exist_ok=True)
        path = root / f"{rec['id']}.json"
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(_public(rec), ensure_ascii=False, indent=2), encoding="utf-8")
        os.chmod(tmp, 0o600)
        tmp.replace(path)
        return str(path)
    except OSError:
        return None


class WorkspaceTaskManager:
    def __init__(self) -> None:
        self._tasks: dict[str, dict] = {}

    def _trim(self) -> None:
        if len(self._tasks) <= _MAX_TASKS:
            return
        finished = [r for r in self._tasks.values() if r.get("status") not in {"running", "cancelling"}]
        for rec in sorted(finished, key=lambda x: float(x.get("created_at") or 0))[: len(self._tasks) - _MAX_TASKS]:
            self._tasks.pop(str(rec.get("id")), None)

    async def start(self, *, command: str, cwd: str, timeout_s: int) -> dict:
        tid = uuid.uuid4().hex[:12]
        rec: dict = {
            "id": tid,
            "command": command,
            "cwd": cwd,
            "timeout": timeout_s,
            "status": "running",
            "created_at": _now(),
            "started_at": _now(),
            "ended_at": None,
            "exit_code": None,
            "stdout": "",
            "stderr": "",
            "error": "",
            "timed_out": False,
            "truncated": False,
        }
        self._tasks[tid] = rec
        rec["_task"] = asyncio.create_task(self._run(rec, f"cd {shlex.quote(cwd)} && {command}"))
        self._trim()
        return _public(rec)

    async def _run(self, rec: dict, full_command: str) -> None:
        try:
            result = await run_shell(full_command, int(rec["timeout"]))
            if rec.get("status") == "cancelling":
                rec["status"] = "cancelled"
            elif result.get("error") or result.get("timed_out"):
                rec["status"] = "failed"
            elif result.get("exit_code") in (0, None):
                rec["status"] = "done"
            else:
                rec["status"] = "failed"
            rec.update({
                "ended_at": _now(),
                "exit_code": result.get("exit_code"),
                "stdout": result.get("stdout", ""),
                "stderr": result.get("stderr", ""),
                "error": result.get("error", ""),
                "timed_out": bool(result.get("timed_out")),
                "truncated": bool(result.get("truncated")),
            })
        except asyncio.CancelledError:
            rec.update({
                "status": "cancelled",
                "ended_at": _now(),
                "error": "任务已取消",
                "_task": None,
            })
            rec["log_path"] = _persist(rec)
            raise
        finally:
            rec["_task"] = None
            if rec.get("status") != "cancelled":
                rec["ended_at"] = rec.get("ended_at") or _now()
            rec["log_path"] = _persist(rec)

    def list(self, limit: int = 20) -> list[dict]:
        items = sorted(
            (_public(r) for r in self._tasks.values()),
            key=lambda x: float(x.get("created_at") or 0),
            reverse=True,
        )
        return items[: max(1, min(int(limit or 20), _MAX_SERIALIZED))]

    def get(self, task_id: str) -> dict | None:
        rec = self._tasks.get(task_id)
        return _public(rec) if rec else None

    def cancel(self, task_id: str) -> dict:
        rec = self._tasks.get(task_id)
        if not rec:
            return {"error": "任务不存在"}
        task = rec.get("_task")
        if rec.get("status") not in {"running", "cancelling"} or not isinstance(task, asyncio.Task):
            return {"error": f"任务已是 {rec.get('status')} 状态，不能取消"}
        rec["status"] = "cancelling"
        task.cancel()
        return {"ok": True, "id": task_id, "status": "cancelling"}

    async def shutdown(self) -> None:
        for rec in self._tasks.values():
            task = rec.get("_task")
            if isinstance(task, asyncio.Task) and not task.done():
                task.cancel()
        await asyncio.sleep(0)


_manager: WorkspaceTaskManager | None = None


def get_manager() -> WorkspaceTaskManager:
    global _manager
    if _manager is None:
        _manager = WorkspaceTaskManager()
    return _manager
