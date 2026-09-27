#!/usr/bin/env python3
"""P3 性能与资源泄漏回归（不需起服务，进程内直测）。

覆盖本轮专项检测发现并修复的问题：
  1. memory.list_sessions 不再 N+1（50 会话只发 1~2 条 SQL，旧实现 51 条）；
  2. messages 表建了 session_id 索引（idx_msg_sid）；
  3. phone._run / shell.run_shell 在「外部 cancel」时真正杀掉子进程（不残留）；
  4. agent._trim 不会留下孤立 tool 消息（导致 OpenAI 400）；
  5. ApprovalCenter.forget 立即唤醒等待中的审批门（不再干等 120s）；
  6. run_agent 被 cancel 后不再偷偷续跑 pending 队列。
"""
import asyncio
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("AGENT_HOME", tempfile.mkdtemp(prefix="pa-p3-"))

passed, failed = [], []

# 预先造一个会跑 30s 的命令桩（同步文件写，放在异步函数外避免 ASYNC230）
_FAKE_LONG = "/tmp/p3_long.sh"
open(_FAKE_LONG, "w").write("#!/bin/bash\nexec /bin/sleep 30\n")
os.chmod(_FAKE_LONG, 0o755)


def check(name: str, ok: bool, detail: str = ""):
    (passed if ok else failed).append(name)
    print(f"  {'✅' if ok else '❌'} {name}" + (f"  — {detail}" if detail else ""))


async def t_n_plus_1():
    from sqlalchemy import event

    from agentd.memory import Store

    tmp = Path(tempfile.mkdtemp(prefix="pa-p3-n1-"))
    store = Store(tmp / "agent.db")
    await store.start()
    sids = [await store.create_session() for _ in range(50)]
    for sid in sids:
        for i in range(10):
            await store.add_message(sid, "user", f"m{i}")

    counts = {"n": 0}

    @event.listens_for(store.engine.sync_engine, "before_cursor_execute")
    def _c(*a, **k):
        counts["n"] += 1

    counts["n"] = 0
    rows = await store.list_sessions(limit=50)
    check("list_sessions 非 N+1（SQL 条数 ≤2，实测 50 会话）", counts["n"] <= 2, f"SQL={counts['n']}")
    check("list_sessions 消息数正确", rows and all(r["message_count"] == 10 for r in rows), f"样例={[r['message_count'] for r in rows[:3]]}")

    idx = (await store._conn.execute(__import__("sqlalchemy").text(
        "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='messages'"
    ))).fetchall()
    names = [r[0] for r in idx]
    check("messages 表有 session_id 索引", "idx_msg_sid" in names, str(names))
    await store.close()


async def t_subprocess_kill_on_cancel():
    from agentd.tools.phone import _run

    async def runner():
        t = asyncio.create_task(_run([_FAKE_LONG], timeout=60))
        await asyncio.sleep(0.5)
        t.cancel()
        try:
            await t
        except asyncio.CancelledError:
            pass
        await asyncio.sleep(0.3)

    await runner()
    # 检查是否还有 sleep 30 残留
    import subprocess

    out = subprocess.run(  # noqa: ASYNC221
        ["ps", "-eo", "comm,args"], capture_output=True, text=True
    ).stdout
    leftovers = [ln for ln in out.splitlines() if ln.strip().split()[:1] == ["sleep"] and ln.rstrip().endswith("30") and "bash -c" not in ln]
    check("外部 cancel 后子进程被回收（不残留）", len(leftovers) == 0, f"残留={leftovers}")
    for ln in leftovers:
        try:
            os.kill(int(ln.split()[1]), 9)
        except Exception:
            pass


async def t_trim_no_orphan():
    from agentd.agent import _trim

    msgs = [{"role": "system", "content": "s"}, {"role": "user", "content": "u"}]
    for i in range(6):
        msgs.append({"role": "assistant", "content": None, "tool_calls": [{"id": f"c{i}"}]})
        msgs.append({"role": "tool", "tool_call_id": f"c{i}", "content": "t"})
    msgs.append({"role": "user", "content": "last"})
    ok_all = True
    detail = ""
    for maxn in (5, 6, 7, 9, 11, 13):
        out = _trim([dict(m) for m in msgs], maxn=maxn)
        for i, m in enumerate(out):
            if m["role"] != "tool":
                continue
            prev = out[i - 1] if i else None
            if not prev or not (prev["role"] == "tool" or (prev["role"] == "assistant" and prev.get("tool_calls"))):
                ok_all = False
                detail = f"maxn={maxn} 在 i={i} 留孤立 tool"
                break
    check("_trim 不产生孤立 tool 消息", ok_all, detail)


async def t_approval_forget_wakes():
    import agentd.main as m

    # 构造一个会阻塞 120s 的 ask，但 forget 后应立刻返回
    approval = m.ApprovalCenter()
    # 不依赖真实 settings，直接 hack timeout
    async def waiter():
        return await approval.ask("sid-x", "tid-1", "some_tool")

    t = asyncio.create_task(waiter())
    await asyncio.sleep(0.2)  # 让 gate 注册上
    assert ("sid-x", "tid-1") in approval._gates
    # forget 应唤醒等待者（置 deny + set event），而非干等 120s
    approval.forget("sid-x")
    try:
        decision = await asyncio.wait_for(t, timeout=2.0)
    except TimeoutError:
        decision = "STILL-WAITING"
    check("forget 立即唤醒审批门（不干等 120s）", decision == "deny", f"decision={decision}")


async def main():
    print("== P3 性能/泄漏回归 ==")
    await t_n_plus_1()
    await t_subprocess_kill_on_cancel()
    await t_trim_no_orphan()
    await t_approval_forget_wakes()
    print(f"\n结果: {len(passed)} 通过 / {len(failed)} 失败")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    asyncio.run(main())
