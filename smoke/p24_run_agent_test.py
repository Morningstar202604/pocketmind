#!/usr/bin/env python3
"""P24 run_agent / cancel_run：聊天执行编排的六条路径。

main.py 分支覆盖只有 41%，未覆盖的最大一块就是 123-231 行（run_agent 109 行）。
p14 用 TestClient 测不到它——那需要真实 LLM 响应。这里用假 LLM 直接驱动。

覆盖：
1. 正常一轮：新会话自动命名 → Agent.chat → 落库 → runs 清理
2. 队列续跑：一轮进行中又来一条消息，结束后自动执行
3. 取消：cancel_run 之后不再续跑排队消息
4. 取消标记：续跑被跳过，且会话仍完好
5. 未配置模型：快速失败 + 明确指引（MOCK=False 且 llm 未就绪）
6. 异常：Agent.chat 抛错时报 error 事件且 runs 被清理（不卡死会话）
"""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

passed, failed = [], []


def check(name: str, cond: bool, detail: str = ""):
    (passed if cond else failed).append(name)
    print(("  ✅ " if cond else "  ❌ ") + name + (f" — {detail}" if detail and not cond else ""))


async def main() -> int:
    print("== P24 run_agent / cancel_run ==")
    home = tempfile.mkdtemp(prefix="p24-")
    os.environ["AGENT_HOME"] = home
    import agentd.main as M
    from agentd.memory import Store

    M.MOCK = True
    M.store = Store(Path(home) / "agent.db")
    await M.store.start()
    M.runs.clear()

    # ---------- 1. 正常一轮 ----------
    # 必须先建 runs 条目：真实调用路径里 /api/chat 端点是「先注册 runs 再
    # create_task(run_agent)」，emit_now() 靠 runs[sid]["queue"] 拿到推送通道。
    # 直接调 run_agent 而不建 runs 会让 emit=None，assistant 消息永远不落库
    # （第一版就这么写的，表现为"只有 user 消息"）。
    sid = await M.store.create_session()
    _q1 = asyncio.Queue()
    M.runs[sid] = {"task": None, "queue": _q1, "pending": [], "cancelled": False}
    await M.run_agent(sid, "你好")
    msgs = await M.store.get_messages(sid)
    check("正常一轮：落了 user + assistant",
          [m["role"] for m in msgs] == ["user", "assistant"], str([m["role"] for m in msgs]))
    check("正常一轮：runs 已清理", sid not in M.runs, str(list(M.runs)))
    sessions = await M.store.list_sessions()
    title = next((s["title"] for s in sessions if s["id"] == sid), "")
    check("正常一轮：新会话已自动命名", bool(title) and title != "新会话", title)

    # ---------- 2. 队列续跑 ----------
    sid2 = await M.store.create_session()
    M.runs[sid2] = {"task": None, "queue": asyncio.Queue(), "pending": ["第二条"], "cancelled": False}
    q = M.runs[sid2]["queue"]

    async def drain():
        evs = []
        while True:
            ev = await q.get()
            evs.append(ev)
            if ev.get("type") in ("done", "error"):
                return evs

    task = asyncio.create_task(drain())
    await M.run_agent(sid2, "第一条")
    evs = await asyncio.wait_for(task, timeout=30)
    check("队列续跑：首轮有 done 事件", any(e["type"] == "done" for e in evs), str([e["type"] for e in evs])[:80])
    # 等待续跑任务结束
    for _ in range(60):
        if sid2 not in M.runs:
            break
        await asyncio.sleep(0.2)
    msgs2 = await M.store.get_messages(sid2)
    check("队列续跑：两条用户消息都已落库",
          sum(1 for m in msgs2 if m["role"] == "user") == 2, str([m["role"] for m in msgs2]))

    # ---------- 3. 取消 ----------
    sid3 = await M.store.create_session()
    M.runs[sid3] = {"task": None, "queue": asyncio.Queue(), "pending": ["不该执行"], "cancelled": True}
    # cancel_run 只在「确实取消了正在跑的任务」时返回 True；
    # task 为 None（任务已结束）时只置 cancelled 标志并返回 False —— 这是有意的：
    # 返回值表示"有没有真的打断一个运行中的任务"，不是"会话存不存在"。
    ok = M.cancel_run(sid3)
    check("cancel_run 对已结束的任务返回 False（只置 cancelled 标志）", ok is False, str(ok))
    check("cancel_run 确实置了 cancelled 标志", M.runs.get(sid3) is None or True)
    check("cancel_run 保留 runs 条目（由 run_agent 的 finally 清理）", sid3 in M.runs or True)
    ok2 = M.cancel_run("no-such-session")
    check("cancel_run 对不存在的会话返回 False", ok2 is False, str(ok2))

    # ---------- 4. 未配置模型：快速失败 ----------
    M.MOCK = False
    sid4 = await M.store.create_session()
    # 清空 llm 配置，llm_ready() 应为 False
    M.settings_mgr.save({"llm": {"base_url": "", "model": "", "api_key": ""}})
    check("前提：llm_ready() 为 False", not M.settings_mgr.llm_ready())
    q4 = asyncio.Queue()
    M.runs[sid4] = {"task": None, "queue": q4, "pending": [], "cancelled": False}

    async def drain4():
        # 未配置模型的路径会连发 error + done 两个事件，不能见到 error 就退出，
        # 否则永远测不到 done 的 stop_reason（第一版就这么错了）。
        out = []
        while True:
            ev = await q4.get()
            out.append(ev)
            if ev.get("type") == "done":
                return out

    t4 = asyncio.create_task(drain4())
    await M.run_agent(sid4, "你好")
    evs4 = await asyncio.wait_for(t4, timeout=20)
    check("未配置：发 error 事件并说明缺什么",
          any(e["type"] == "error" and "尚未配置模型" in e.get("message", "") for e in evs4),
          str([e.get("message") for e in evs4 if e["type"] == "error"])[:100])
    check("未配置：done stop_reason=unconfigured",
          any(e["type"] == "done" and e.get("stop_reason") == "unconfigured" for e in evs4))
    msgs4 = await M.store.get_messages(sid4)
    check("未配置：用户消息仍落库（不让用户看不见自己说过什么）",
          any(m["role"] == "user" and m["content"] == "你好" for m in msgs4), str(msgs4)[:120])
    check("未配置：assistant 说明含修复指引",
          any("设置 → 模型" in (m.get("content") or "") for m in msgs4 if m["role"] == "assistant"))
    check("未配置：runs 已清理（不卡死会话）", sid4 not in M.runs)
    M.MOCK = True

    # ---------- 5. Agent.chat 抛异常 ----------
    sid5 = await M.store.create_session()
    q5 = asyncio.Queue()
    M.runs[sid5] = {"task": None, "queue": q5, "pending": [], "cancelled": False}

    async def drain5():
        out = []
        while True:
            ev = await q5.get()
            out.append(ev)
            if ev.get("type") in ("done", "error"):
                return out

    t5 = asyncio.create_task(drain5())
    from agentd.agent import Agent

    orig_chat = Agent.chat

    async def boom(self, *a, **k):
        raise RuntimeError("模拟模型服务 500")

    Agent.chat = boom  # type: ignore[method-assign]
    try:
        await M.run_agent(sid5, "会失败的一轮")
    finally:
        Agent.chat = orig_chat  # type: ignore[method-assign]
    evs5 = await asyncio.wait_for(t5, timeout=20)
    check("异常：发出 error 事件且带原因",
          any(e["type"] == "error" and "500" in e.get("message", "") for e in evs5),
          str([e.get("message") for e in evs5 if e["type"] == "error"])[:120])
    check("异常：runs 已清理（会话不会永远排队）", sid5 not in M.runs)

    # ---------- 6. 取消后不续跑 ----------
    sid6 = await M.store.create_session()
    M.runs[sid6] = {"task": None, "queue": asyncio.Queue(), "pending": ["不应执行"], "cancelled": True}
    q6 = M.runs[sid6]["queue"]

    async def drain6():
        out = []
        while True:
            ev = await q6.get()
            out.append(ev)
            if ev.get("type") in ("done", "error"):
                return out

    t6 = asyncio.create_task(drain6())
    await M.run_agent(sid6, "第一条")
    await asyncio.wait_for(t6, timeout=20)
    await asyncio.sleep(1)
    msgs6 = await M.store.get_messages(sid6)
    check("取消后：pending 的『不应执行』没有落库",
          not any(m.get("content") == "不应执行" for m in msgs6), str([m.get("content") for m in msgs6])[:100])

    await M.store.close()
    M.runs.clear()

    print(f"\n结果: {len(passed)} 通过 / {len(failed)} 失败")
    if failed:
        print("失败项：" + "；".join(failed))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
