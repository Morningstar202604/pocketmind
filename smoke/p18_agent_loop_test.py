#!/usr/bin/env python3
"""P18 Agent 主循环：_loop 的每个分支都要有行为断言。

为什么要有这一层：`Agent._loop` 是全项目最热的代码（每次对话都跑），
圈复杂度 53、未覆盖 184 行。在把它拆小之前，必须先把**当前行为**钉死，
否则重构无法证明"只是搬家、没改行为"。

这里用假 LLM（不走网络）驱动 _loop，覆盖：
1. 纯文本回复 → 落库 assistant + done(end_turn)
2. Plan 模式 → 落库计划 + done(plan)，且**不执行**任何工具
3. 工具调用 + 批准 → tool_start / tool_update(completed) / 结果回填
4. 工具调用 + 拒绝 → status=denied，且**不执行**工具
5. 工具调用 + 审批超时 → status=denied，文案区分超时
6. 达到 max_iters 仍未收敛 → emit error 终止
7. 工具执行抛异常 → status=failed，不炸循环
8. thinking 事件转发
"""
from __future__ import annotations

import asyncio
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

passed, failed = [], []


def check(name: str, cond: bool, detail: str = ""):
    (passed if cond else failed).append(name)
    print(("  ✅ " if cond else "  ❌ ") + name + (f" — {detail}" if detail and not cond else ""))


class FakeLLM:
    """按剧本逐轮返回：每轮是一组 (content, reasoning, tool_calls)。"""

    def __init__(self, rounds: list[list[tuple]]):
        self.rounds = rounds
        self.i = 0
        self.seen_messages: list[list[dict]] = []

    async def chat(self, messages, tools):
        self.seen_messages.append([dict(m) for m in messages])
        if self.i >= len(self.rounds):
            # 剧本用完 → 收敛为纯文本，模拟模型看到工具结果后收尾。
            # （之前复用最后一轮会让"工具报错后应继续"的用例无限调工具）
            batch = [("（剧本结束）", None, None)]
        else:
            batch = self.rounds[self.i]
        self.i += 1
        for content, reasoning, tcs in batch:
            yield SimpleNamespace(
                choices=[SimpleNamespace(
                    delta=SimpleNamespace(content=content, reasoning_content=reasoning, tool_calls=tcs)
                )]
            )


def tc_delta(index: int, call_id: str, name: str, args: str):
    return SimpleNamespace(
        index=index, id=call_id,
        function=SimpleNamespace(name=name, arguments=args),
    )


async def build_agent(rounds, agent_mode="act"):
    from agentd.agent import Agent
    from agentd.config import Settings
    from agentd.memory import Store

    td = Path(tempfile.mkdtemp(prefix="p18-"))
    store = Store(td / "agent.db")
    await store.start()
    # Settings 的入参是**配置文件路径**，不是目录（第一版传目录导致 PermissionError）
    s = Settings(td / "config.json")
    s.save({"agent_mode": agent_mode, "permission_mode": "approve"})
    a = Agent(store, s, mock=True)
    # 必须是**同一个** FakeLLM 实例：如果写成 `lambda: FakeLLM(rounds)`，
    # 每次 _stream 都会新建一个，round 计数永远停在 0，剧本永远不会推进
    # （第一版就这么错了，表现为"工具报错后无限循环"）。
    fake = FakeLLM(rounds)
    a._llm = lambda: fake
    return a, store, td


async def main() -> int:
    print("== P18 Agent 主循环 ==")
    from agentd.agent import _trim  # noqa: F401  (确认可导入)

    # ---------- 1. 纯文本 ----------
    a, store, _ = await build_agent([[("你好呀", None, None)]])
    sid = await store.create_session()
    events: list[dict] = []

    async def emit(ev):
        events.append(ev)

    tools = []
    await a._loop(sid, [{"role": "user", "content": "hi"}], tools, emit, None, "auto", 5, 0, {})
    msgs = await store.get_messages(sid)
    check("纯文本：落库一条 assistant", len(msgs) == 1 and msgs[0]["role"] == "assistant", str(msgs)[:80])
    check("纯文本：内容正确", msgs[0]["content"] == "你好呀", msgs[0]["content"])
    check("纯文本：emit text 事件", any(e["type"] == "text" for e in events))
    check("纯文本：emit done end_turn",
          any(e["type"] == "done" and e.get("stop_reason") == "end_turn" for e in events), str(events[-2:]))

    # ---------- 2. thinking 转发 ----------
    a2, store2, _ = await build_agent([[("答", "我在想…", None)]])
    sid2 = await store2.create_session()
    ev2: list[dict] = []
    await a2._loop(sid2, [{"role": "user", "content": "q"}], [], lambda e: _append(ev2, e), None, "auto", 3, 0, {})
    check("thinking 事件被转发", any(e["type"] == "thinking" and "我在想" in e.get("text", "") for e in ev2), str(ev2)[:120])

    # ---------- 3. Plan 模式不执行工具 ----------
    a3, store3, _ = await build_agent(
        [[(None, "想想", [tc_delta(0, "c1", "run_shell", '{"command":"echo hi"}')])]],
        agent_mode="plan",
    )
    sid3 = await store3.create_session()
    ev3: list[dict] = []
    await a3._loop(sid3, [{"role": "user", "content": "跑个命令"}], [], lambda e: _append(ev3, e), None, "auto", 3, 0, {})
    check("Plan：emit plan 事件", any(e["type"] == "plan" for e in ev3), str([e["type"] for e in ev3]))
    check("Plan：done stop_reason=plan",
          any(e["type"] == "done" and e.get("stop_reason") == "plan" for e in ev3))
    check("Plan：**没有** tool_start（工具未执行）",
          not any(e["type"] == "tool_start" for e in ev3), str([e["type"] for e in ev3]))
    msgs3 = await store3.get_messages(sid3)
    check("Plan：计划已落库", msgs3 and "计划调用工具" in (msgs3[0].get("content") or ""), str(msgs3)[:100])

    # ---------- 4. 工具批准 → 执行完成 ----------
    rounds = [
        [(None, None, [tc_delta(0, "c1", "write_file", '{"path":"p18-out.txt","content":"x"}')])],
        [("完成了", None, None)],
    ]
    a4, store4, _ = await build_agent(rounds)
    sid4 = await store4.create_session()
    ev4: list[dict] = []
    approvals: list[str] = []

    async def ask_ok(tid, name, summary, risk):
        approvals.append(name)
        return "allow"

    from agentd.tools import schemas

    await a4._loop(sid4, [{"role": "user", "content": "列目录"}], schemas(), lambda e: _append(ev4, e),
                   ask_ok, "approve", 5, 0, {})
    check("批准路径：调用了审批回调（write 级工具必须问）", approvals.count("write_file") >= 1, str(approvals))
    check("批准路径：emit tool_start", any(e["type"] == "tool_start" and e["name"] == "write_file" for e in ev4))
    check("批准路径：tool_update status=completed",
          any(e["type"] == "tool_update" and e.get("status") == "completed" for e in ev4),
          str([e.get("status") for e in ev4 if e["type"] == "tool_update"]))
    msgs4 = await store4.get_messages(sid4)
    check("批准路径：tool 消息落库", any(m["role"] == "tool" for m in msgs4), str([m["role"] for m in msgs4]))
    check("批准路径：最终 assistant 收尾", msgs4[-1]["role"] == "assistant", str(msgs4[-1]["role"]))

    # ---------- 5. 拒绝 → 不执行 ----------
    a5, store5, _ = await build_agent(rounds)
    sid5 = await store5.create_session()
    ev5: list[dict] = []

    async def ask_deny(tid, name, summary, risk):
        return "deny"

    await a5._loop(sid5, [{"role": "user", "content": "x"}], schemas(), lambda e: _append(ev5, e),
                   ask_deny, "approve", 3, 0, {})
    check("拒绝路径：status=denied",
          any(e["type"] == "tool_update" and e.get("status") == "denied" for e in ev5),
          str([e.get("status") for e in ev5 if e["type"] == "tool_update"]))
    check("拒绝路径：结果文案说明用户拒绝",
          any("用户拒绝" in str(e.get("result", "")) for e in ev5 if e["type"] == "tool_update"))

    # ---------- 6. 超时 → denied 且文案区分 ----------
    a6, store6, _ = await build_agent(rounds)
    sid6 = await store6.create_session()
    ev6: list[dict] = []

    async def ask_timeout(tid, name, summary, risk):
        return "timeout"

    await a6._loop(sid6, [{"role": "user", "content": "x"}], schemas(), lambda e: _append(ev6, e),
                   ask_timeout, "approve", 3, 0, {})
    check("超时路径：status=denied",
          any(e["type"] == "tool_update" and e.get("status") == "denied" for e in ev6))
    check("超时路径：文案说明是超时而非用户拒绝",
          any("超时" in str(e.get("result", "")) for e in ev6 if e["type"] == "tool_update"),
          str([e.get("result") for e in ev6 if e["type"] == "tool_update"])[:100])

    # ---------- 7. 工具报错 → failed，循环继续 ----------
    bad_rounds = [
        [(None, None, [tc_delta(0, "c1", "read_file", '{"path":"不存在的文件xyz.txt"}')])],
        [("读失败了", None, None)],
    ]
    # 注：FakeLLM 剧本耗尽后会返回纯文本收尾，所以两轮足够
    a7, store7, _ = await build_agent(bad_rounds)
    sid7 = await store7.create_session()
    ev7: list[dict] = []
    await a7._loop(sid7, [{"role": "user", "content": "x"}], schemas(), lambda e: _append(ev7, e),
                   ask_ok, "approve", 5, 0, {})
    check("工具报错：status=failed",
          any(e["type"] == "tool_update" and e.get("status") == "failed" for e in ev7),
          str([e.get("status") for e in ev7 if e["type"] == "tool_update"]))
    check("工具报错：循环未中断（最终有 done）",
          any(e["type"] == "done" for e in ev7), str([e["type"] for e in ev7]))

    # ---------- 8. 轮数用尽 → error ----------
    # 这个用例需要"模型永远在调工具、从不收尾"——FakeLLM 剧本耗尽会返回纯文本，
    # 所以单独给一个永动的假 LLM。
    class AlwaysToolLLM(FakeLLM):
        async def chat(self, messages, tools):
            yield SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(
                content=None, reasoning_content=None,
                tool_calls=[tc_delta(0, "c1", "write_file", '{"path":"p18-loop.txt","content":"x"}')],
            ))])

    a8, store8, _ = await build_agent([[("never used", None, None)]])
    a8._llm = lambda: AlwaysToolLLM([[("never", None, None)]])  # 实例无所谓，chat 被重写
    sid8 = await store8.create_session()
    ev8: list[dict] = []
    await a8._loop(sid8, [{"role": "user", "content": "x"}], schemas(), lambda e: _append(ev8, e),
                   ask_ok, "approve", 2, 0, {})
    check("轮数用尽：emit error 说明工具调用过多",
          any(e["type"] == "error" and "工具调用次数过多" in e.get("message", "") for e in ev8),
          str([e.get("message") for e in ev8 if e["type"] == "error"]))

    # ---------- 9. mock 模式的 _stream ----------
    from agentd.agent import Agent
    from agentd.config import Settings

    td9 = Path(tempfile.mkdtemp(prefix="p18m-"))
    s9 = Settings(td9 / "config.json")
    a9 = Agent(None, s9, mock=True)  # type: ignore[arg-type]
    chunks = []
    async for c, _r, _t in a9._stream([{"role": "user", "content": "你好"}], []):
        chunks.append(c)
    check("mock _stream 能产出文本", any(chunks), str(chunks[:5]))

    print(f"\n结果: {len(passed)} 通过 / {len(failed)} 失败")
    if failed:
        print("失败项：" + "；".join(failed))
    return 1 if failed else 0


async def _append(bucket: list, ev: dict):
    bucket.append(ev)


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
