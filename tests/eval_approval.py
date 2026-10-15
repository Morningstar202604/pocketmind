#!/usr/bin/env python3
"""审批中心单测：生命周期 + 持久化（可直接运行，风格与其余套件一致）。

为什么改成脚本而不是 pytest：本仓库的回归套件全部是「直接 python 运行」的脚本，
CI 也只执行脚本；此前本文件用 pytest + pytest-asyncio 写，而 CI 既未安装 pytest
也没有 pytest 步骤——等于从不运行（且其持久化断言还停在旧文件格式上，即便跑也必红）。
现在改为仓库统一风格，并纳入 CI 门禁。

覆盖：
  超时默认拒绝 / allow_once 不落白名单 / allow_always 落本会话白名单 /
  forget 清会话 / 未知 gate 返回 False / 白名单写盘 + 重启恢复（新格式）/
  跨会话查询 / 旧格式（会话级）文件兼容载入

更细的三级审批语义（deny_always / allow_global / 黑名单跨会话短路）见 smoke/p4_agent_depth_test.py。
"""
from __future__ import annotations

import asyncio
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agentd.approval import ApprovalCenter  # noqa: E402

passed, failed = [], []


def check(name: str, cond: bool, detail: str = ""):
    (passed if cond else failed).append(name)
    print(("  ✅ " if cond else "  ❌ ") + name + (f" — {detail}" if detail and not cond else ""))


async def _ask_and_decide(ac: ApprovalCenter, sid: str, tid: str, tool: str, decision: str) -> str:
    """发起一次 ask，随后用 decide 唤醒，返回 ask 的最终结论。"""
    task = asyncio.create_task(ac.ask(sid, tid, tool))
    await asyncio.sleep(0.05)
    ac.decide(sid, tid, decision)
    return await asyncio.wait_for(task, 2)


async def main() -> None:
    print("== 审批中心单测 ==")

    # 1) 超时默认拒绝（安全优先）
    ac = ApprovalCenter(timeout_cb=lambda: 0.01)
    res = await ac.ask("s1", "tc1", "write_file")
    check("超时 → timeout（默认拒绝）", res == "timeout", f"got {res}")

    # 2) allow_once：本次放行，不写白名单
    ac = ApprovalCenter(timeout_cb=lambda: 120)
    res = await _ask_and_decide(ac, "s1", "tc1", "write_file", "allow_once")
    check("allow_once → allow", res == "allow", f"got {res}")
    check("allow_once 不落白名单", ac.is_auto_allowed("s1", "write_file") is False)
    res2 = await _ask_and_decide(ac, "s1", "tc2", "write_file", "deny")
    check("同会话同名工具仍会再次询问", res2 == "deny", f"got {res2}")

    # 3) allow_always：写本会话白名单，第二次直接放行（不再等待）
    ac = ApprovalCenter(timeout_cb=lambda: 120)
    res = await _ask_and_decide(ac, "s1", "tc1", "write_file", "allow_always")
    check("allow_always → allow", res == "allow", f"got {res}")
    check("已写入本会话白名单", ac.is_auto_allowed("s1", "write_file") is True)
    res = await asyncio.wait_for(ac.ask("s1", "tc2", "write_file"), timeout=1)
    check("白名单命中直接放行（不建等待门）", res == "allow", f"got {res}")
    check("白名单不外溢到其它会话", ac.is_auto_allowed("s2", "write_file") is False)

    # 4) decide 未知 gate → False
    check("未知 gate 返回 False", ac.decide("no_such", "no_such", "allow_once") is False)

    # 5) forget：清该会话白名单
    ac.forget("s1")
    check("forget 清空该会话白名单", ac.is_auto_allowed("s1", "write_file") is False)

    # 6) 持久化：写盘为新格式（sessions/global/deny）并在新实例中恢复
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
        f = Path(td) / "approval_always.json"
        ac1 = ApprovalCenter(timeout_cb=lambda: 120, persist_path=f)
        await _ask_and_decide(ac1, "s1", "tc1", "write_file", "allow_always")
        data = json.loads(f.read_text(encoding="utf-8"))
        check(
            "落盘为新格式（sessions 包裹）",
            isinstance(data.get("sessions"), dict) and "write_file" in data["sessions"].get("s1", []),
            f"data={data}",
        )
        ac2 = ApprovalCenter(timeout_cb=lambda: 120, persist_path=f)
        check("重启后白名单恢复", ac2.is_auto_allowed("s1", "write_file") is True)

        # 7) 旧格式（顶层 {sid: [tool]}）兼容载入
        legacy = Path(td) / "legacy.json"
        legacy.write_text(json.dumps({"s-old": ["run_shell"]}), encoding="utf-8")
        ac3 = ApprovalCenter(timeout_cb=lambda: 120, persist_path=legacy)
        check("旧格式文件兼容载入", ac3.is_auto_allowed("s-old", "run_shell") is True)

    # 8) 跨会话查询（前端判断默认勾选用）
    ac = ApprovalCenter(timeout_cb=lambda: 120)
    await _ask_and_decide(ac, "sx", "t1", "run_shell", "allow_always")
    check("is_always_allowed_in_any_session 命中", ac.is_always_allowed_in_any_session("run_shell") is True)
    check("未允许的工具返回 False", ac.is_always_allowed_in_any_session("write_file") is False)

    print(f"\n结果: {len(passed)} 通过 / {len(failed)} 失败")
    if failed:
        print("失败项：" + "；".join(failed))
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    asyncio.run(main())
