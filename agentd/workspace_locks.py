"""工作区文件锁：防止人、Agent、Git 合并同时覆盖同一个文件。

这不是跨进程文件锁，而是 agentd 单进程内的协作锁。PocketMind 的 Web UI、
Agent 工具和 PTY 都跑在同一个 Python 进程里，用同一把 ``asyncio.Lock`` 足够
把「读-改-写」串起来；跨进程场景仍由 mtime 冲突检测兜底。

规则：
- 以 resolve 后的绝对路径为 key，符号链接不会绕开锁；
- 默认等待 8 秒，超时返回 ``WorkspaceLockBusy``，由上层转成 409；
- 锁只保护写动作，读和预览不阻塞；
- 锁表按路径长期保留（单用户手机场景路径数量有限，换来的实现更简单）。
"""
from __future__ import annotations

import asyncio
import time
from contextlib import asynccontextmanager
from pathlib import Path


class WorkspaceLockBusy(RuntimeError):
    def __init__(self, path: str, owner: str = ""):
        self.path = path
        self.owner = owner
        super().__init__(f"文件正在被 {owner or '另一个操作'} 修改：{path}")


class _Entry:
    __slots__ = ("lock", "owner", "since")

    def __init__(self) -> None:
        self.lock = asyncio.Lock()
        self.owner = ""
        self.since = 0.0


class WorkspaceLockRegistry:
    def __init__(self) -> None:
        self._locks: dict[str, _Entry] = {}

    @staticmethod
    def key(path: str | Path) -> str:
        try:
            return str(Path(path).expanduser().resolve())
        except OSError:
            return str(Path(path).expanduser())

    def _entry(self, path: str | Path) -> tuple[str, _Entry]:
        key = self.key(path)
        entry = self._locks.get(key)
        if entry is None:
            entry = _Entry()
            self._locks[key] = entry
        return key, entry

    @asynccontextmanager
    async def hold(self, path: str | Path, owner: str = "human", wait: float = 8.0):
        """持有路径锁；等待超时抛 WorkspaceLockBusy。"""
        key, entry = self._entry(path)
        try:
            await asyncio.wait_for(entry.lock.acquire(), timeout=max(0.0, wait))
        except TimeoutError:
            raise WorkspaceLockBusy(key, entry.owner) from None
        entry.owner = owner
        entry.since = time.time()
        try:
            yield
        finally:
            entry.owner = ""
            entry.since = 0.0
            entry.lock.release()

    def snapshot(self) -> list[dict]:
        return [
            {"path": key, "owner": e.owner, "since": e.since}
            for key, e in self._locks.items()
            if e.lock.locked()
        ]


_registry: WorkspaceLockRegistry | None = None


def get_locks() -> WorkspaceLockRegistry:
    global _registry
    if _registry is None:
        _registry = WorkspaceLockRegistry()
    return _registry


def hold(path: str | Path, owner: str = "human", wait: float = 8.0):
    return get_locks().hold(path, owner=owner, wait=wait)
