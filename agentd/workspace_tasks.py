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
import shutil
import time
import uuid

from .paths import home_dir
from .tools.shell import MAX_OUTPUT, _kill_group, _rlimit_preexec

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
        """边跑边把 stdout/stderr 写进记录：任务面板 1.5s 轮询即可看到增量。

        这里不复用 run_shell，因为 run_shell 只在结束时返回；但仍保留同样的
        独立进程组、rlimit 和超时整组回收，避免 npm run dev 留下孤儿进程。
        """
        shell = shutil.which("bash") or "/bin/sh"
        spawn: dict = {
            "stdout": asyncio.subprocess.PIPE,
            "stderr": asyncio.subprocess.PIPE,
            "start_new_session": True,
        }
        if os.name == "posix":
            spawn["preexec_fn"] = _rlimit_preexec
        proc = await asyncio.create_subprocess_exec(shell, "-c", full_command, **spawn)
        assert proc.stdout is not None and proc.stderr is not None
        timed_out = False

        async def pump(stream, key: str) -> None:
            while True:
                chunk = await stream.read(1024)
                if not chunk:
                    return
                text = chunk.decode("utf-8", errors="replace")
                current = rec.get(key, "")
                room = MAX_OUTPUT - len(current)
                if room > 0:
                    rec[key] = current + text[:room]
                if len(current) + len(text) > MAX_OUTPUT:
                    rec["truncated"] = True
                rec["updated_at"] = _now()

        pumps = [
            asyncio.create_task(pump(proc.stdout, "stdout")),
            asyncio.create_task(pump(proc.stderr, "stderr")),
        ]
        try:
            await asyncio.wait_for(proc.wait(), timeout=int(rec["timeout"]))
        except TimeoutError:
            timed_out = True
            _kill_group(proc)
            await proc.wait()
        except asyncio.CancelledError:
            rec.update({"status": "cancelled", "error": "任务已取消", "ended_at": _now()})
            _kill_group(proc)
            await proc.wait()
            for p in pumps:
                p.cancel()
            rec["_task"] = None
            rec["log_path"] = _persist(rec)
            raise
        finally:
            for p in pumps:
                if not p.done():
                    p.cancel()
            await asyncio.gather(*pumps, return_exceptions=True)

        cancelled = rec.get("status") == "cancelling"
        if cancelled:
            rec["status"] = "cancelled"
            rec["error"] = "任务已取消"
        elif timed_out:
            rec["status"] = "failed"
            rec["error"] = f"任务超时（{rec['timeout']}s）"
        elif proc.returncode == 0:
            rec["status"] = "done"
        else:
            rec["status"] = "failed"
            tail = rec.get("stderr", "")[:200] or rec.get("stdout", "")[:120]
            rec["error"] = f"命令以非零状态退出（exit {proc.returncode}）：{tail}"
        rec.update({
            "ended_at": _now(),
            "exit_code": proc.returncode,
            "timed_out": bool(timed_out),
        })
        rec["_task"] = None
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
