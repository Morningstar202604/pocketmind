"""审批中心：管理所有待审批工具调用的生命周期。

每个待审批工具调用持有一个 asyncio.Event；超时默认拒绝（安全优先）。
支持「始终允许」白名单（按会话 + 工具名粒度）。

持久化说明
--------
「始终允许」白名单持久化到 ``~/.agent/approval_always.json``。
（与 Store 解耦：审批在 Store 之前就需要，且启动时不一定立即建库）
重启后恢复，避免用户反复确认同一工具。
"""
from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from pathlib import Path


# PersistentStore：简单的 JSON 原子写 + 崩溃安全。
def _atomic_json_write(path: Path, data: dict) -> None:
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def _load_json_safe(path: Path) -> dict:
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass
    return {}


class ApprovalCenter:
    """每个待审批工具调用一个 asyncio.Event；超时默认拒绝（安全优先）。"""

    def __init__(self, timeout_cb: Callable[[], int] | None = None, persist_path: Path | None = None):
        self._gates: dict[tuple[str, str], dict] = {}
        self._always: dict[str, set] = {}
        self._timeout_cb = timeout_cb or (lambda: 120)
        self._persist_path = persist_path
        # 加载持久化的白名单
        if persist_path:
            try:
                loaded = _load_json_safe(persist_path)
                self._always = {k: set(v) for k, v in loaded.items()}
            except Exception:
                pass

    def _persist(self) -> None:
        """白名单变更后写盘（原子替换）。"""
        if not self._persist_path:
            return
        try:
            _atomic_json_write(
                self._persist_path,
                {k: sorted(v) for k, v in self._always.items() if v},
            )
        except Exception:
            pass  # 写盘失败不影响运行时；下次重启会读旧版本

    async def ask(self, session_id: str, tool_call_id: str, tool_name: str) -> str:
        if tool_name in self._always.setdefault(session_id, set()):
            return "allow"
        timeout = self._timeout_cb()
        gate = {"event": asyncio.Event(), "decision": None}
        self._gates[(session_id, tool_call_id)] = gate
        try:
            await asyncio.wait_for(gate["event"].wait(), timeout=timeout)
        except TimeoutError:
            return "timeout"  # 超时默认拒绝（安全优先）
        finally:
            self._gates.pop((session_id, tool_call_id), None)
        d = gate["decision"]
        if d == "allow_always":
            self._always.setdefault(session_id, set()).add(tool_name)
            self._persist()
            return "allow"
        if d == "allow_once":
            return "allow"
        return "deny"

    def is_auto_allowed(self, session_id: str, tool_name: str) -> bool:
        """该工具是否已被用户「始终允许」。"""
        return tool_name in self._always.get(session_id, set())

    def is_always_allowed_in_any_session(self, tool_name: str) -> bool:
        """该工具是否在任何会话中被『始终允许』（前端用：判断默认勾选）。"""
        return any(tool_name in v for v in self._always.values())

    def decide(self, session_id: str, tool_call_id: str, decision: str) -> bool:
        gate = self._gates.get((session_id, tool_call_id))
        if not gate:
            return False
        gate["decision"] = decision
        gate["event"].set()
        return True

    def forget(self, session_id: str) -> None:
        """会话删除时清理：未决审批 + 「始终允许」记忆。"""
        for k, gate in self._gates.items():
            if k[0] == session_id and not gate["event"].is_set():
                gate["decision"] = "deny"
                gate["event"].set()
        self._gates = {k: v for k, v in self._gates.items() if k[0] != session_id}
        if session_id in self._always:
            del self._always[session_id]
            self._persist()
