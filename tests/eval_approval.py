"""审批中心单测：生命周期 + 持久化。"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from agentd.approval import ApprovalCenter


@pytest.mark.asyncio
async def test_ask_returns_timeout_on_deny():
    """ask 超时 → 返回 timeout（安全优先：默认拒绝）"""
    ac = ApprovalCenter(timeout_cb=lambda: 0.01)
    result = await ac.ask("s1", "tc1", "write_file")
    assert result == "timeout"


@pytest.mark.asyncio
async def test_allow_once():
    """decide(allow_once) → ask 返回 allow（不写 _always）"""
    ac = ApprovalCenter(timeout_cb=lambda: 120)
    asked = asyncio.Event()
    captured: dict[str, str] = {}

    async def _run() -> None:
        captured["r"] = await ac.ask("s1", "tc1", "write_file")
        asked.set()

    task = asyncio.create_task(_run())
    await asyncio.sleep(0.05)  # 等待 ask 阻塞在 gate
    ok = ac.decide("s1", "tc1", "allow_once")
    assert ok is True
    await asyncio.wait_for(asked.wait(), 2)
    assert captured["r"] == "allow"
    await task
    # allow_once 不应写入 always，第二个 tool_call_id 仍需询问
    assert ac.is_auto_allowed("s1", "write_file") is False


@pytest.mark.asyncio
async def test_allow_always_persists_across_calls():
    """allow_always 后同会话后续 ask 直接放行"""
    ac = ApprovalCenter(timeout_cb=lambda: 1)  # 短超时，避免阻塞

    async def _first() -> str:
        return await ac.ask("s1", "tc1", "write_file")

    task = asyncio.create_task(_first())
    await asyncio.sleep(0.05)
    ac.decide("s1", "tc1", "allow_always")
    result = await asyncio.wait_for(task, 2)
    assert result == "allow"
    # 已写入 always，第二次 ask 直接 allow
    r2 = await ac.ask("s1", "tc2", "write_file")
    assert r2 == "allow"


@pytest.mark.asyncio
async def test_forget_clears_session():
    """forget 后 always 记忆清空"""
    ac = ApprovalCenter(timeout_cb=lambda: 120)
    ac._always["s1"] = {"write_file"}
    assert ac.is_auto_allowed("s1", "write_file") is True
    ac.forget("s1")
    assert ac.is_auto_allowed("s1", "write_file") is False


@pytest.mark.asyncio
async def test_decide_unknown_gate_returns_false():
    """不存在的 gate → False"""
    ac = ApprovalCenter(timeout_cb=lambda: 120)
    assert ac.decide("no_such", "no_such", "allow_once") is False


def test_persist_to_disk(tmp_path: Path) -> None:
    """allow_always 写盘 + 重启恢复。"""
    p = tmp_path / "approval_always.json"

    async def _allow_and_close(center: ApprovalCenter) -> None:
        async def _do() -> str:
            return await center.ask("s1", "tc1", "write_file")

        task = asyncio.create_task(_do())
        await asyncio.sleep(0.05)
        center.decide("s1", "tc1", "allow_always")
        await asyncio.wait_for(task, 2)

    # 第一实例：允许并写盘
    ac1 = ApprovalCenter(timeout_cb=lambda: 120, persist_path=p)
    asyncio.run(_allow_and_close(ac1))

    # 验证文件存在且 JSON 格式正确
    assert p.exists()
    data = json.loads(p.read_text(encoding="utf-8"))
    assert "s1" in data
    assert "write_file" in data["s1"]

    # 新实例：从磁盘恢复
    ac2 = ApprovalCenter(timeout_cb=lambda: 120, persist_path=p)
    assert ac2.is_auto_allowed("s1", "write_file") is True


def test_is_always_allowed_in_any_session() -> None:
    """跨会话查询某个工具是否被 always 允许。"""
    ac = ApprovalCenter(timeout_cb=lambda: 120)
    ac._always["s1"] = {"run_shell"}
    ac._always["s2"] = set()
    assert ac.is_always_allowed_in_any_session("run_shell") is True
    assert ac.is_always_allowed_in_any_session("write_file") is False
