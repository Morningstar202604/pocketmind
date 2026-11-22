#!/usr/bin/env python3
"""P40 Agent Harness：单轮预算 / 重复调用 / 非法参数 / 原子写 / Git 工具。

为什么单独一套：p18 证明的是「主循环分支」，这一套证明的是**运行时护栏**：
模型跑飞、参数坏掉、上下文顶爆时，Agent 不能把错误请求发给端点，也不能
把写文件执行成空内容。所有用例都不联网。
"""
from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

_HOME = tempfile.mkdtemp(prefix="p40-home-")
_AGENT_HOME = tempfile.mkdtemp(prefix="p40-agent-home-")
_WS = tempfile.mkdtemp(prefix="p40-ws-")
os.environ["HOME"] = _HOME
os.environ["USERPROFILE"] = _HOME
os.environ["AGENT_HOME"] = _AGENT_HOME
os.environ["AGENT_WORKSPACE"] = _WS
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

passed, failed = [], []


def check(name: str, cond: bool, detail: str = ""):
    (passed if cond else failed).append(name)
    print(("  ✅ " if cond else "  ❌ ") + name + (f" — {detail}" if detail and not cond else ""))


class FakeLLM:
    """按剧本返回 (content, reasoning, tool_calls)。"""

    def __init__(self, rounds: list[list[tuple]]):
        self.rounds = rounds
        self.i = 0

    async def chat(self, messages, tools):
        batch = self.rounds[self.i] if self.i < len(self.rounds) else [("（结束）", None, None)]
        self.i += 1
        for content, reasoning, tcs in batch:
            yield SimpleNamespace(choices=[SimpleNamespace(
                delta=SimpleNamespace(content=content, reasoning_content=reasoning, tool_calls=tcs)
            )])


def tc(index: int, call_id: str, name: str, args: str):
    return SimpleNamespace(
        index=index, id=call_id,
        function=SimpleNamespace(name=name, arguments=args),
    )


async def build_agent(rounds):
    from agentd.agent import Agent
    from agentd.config import Settings
    from agentd.memory import Store

    td = Path(tempfile.mkdtemp(prefix="p40-agent-"))
    store = Store(td / "agent.db")
    await store.start()
    s = Settings(td / "config.json")
    s.save({"agent_mode": "act", "permission_mode": "auto", "context": {"budget_tokens": 0}})
    a = Agent(store, s, mock=True)
    fake = FakeLLM(rounds)
    a._llm = lambda: fake
    return a, store


async def _events():
    events: list[dict] = []

    async def emit(ev):
        events.append(ev)

    return events, emit


async def check_loop_guards() -> None:
    print("\n== 循环护栏 ==")
    from agentd.harness import TurnGuard, fit_context, tool_fingerprint

    g = TurnGuard(max_seconds=0, repeat_tool_limit=2)
    first = g.note_tool("read_file", {"path": "a"})
    second = g.note_tool("read_file", {"path": "a"})
    third = g.note_tool("read_file", {"path": "a"})
    check("重复调用：前两次放行", first is None and second is None, f"{first} {second}")
    check("重复调用：第三次被拦", third is not None and "连续" in third, str(third))
    check("不同参数重置计数", g.note_tool("read_file", {"path": "b"}) is None)
    check("同参数不同键顺序指纹一致",
          tool_fingerprint("x", {"a": 1, "b": 2}) == tool_fingerprint("x", {"b": 2, "a": 1}))
    g2 = TurnGuard(max_seconds=0.01, repeat_tool_limit=0)
    await asyncio.sleep(0.02)
    check("墙钟超时判定生效", g2.timed_out() is True)

    msgs = [
        {"role": "system", "content": "固定" * 100},
        {"role": "system", "content": "可选记忆" * 100},
        {"role": "assistant", "content": None, "tool_calls": [{"id": "c1", "type": "function", "function": {"name": "read_file", "arguments": "{\"path\":\"a\"}"}}]},
        {"role": "tool", "tool_call_id": "c1", "content": "工具结果" * 200},
        {"role": "user", "content": "当前问题"},
        {"role": "assistant", "content": "收尾"},
    ]
    out, est = fit_context(msgs, 80)
    check("上下文裁剪返回非 None", out is not None, f"est={est} out={out}")
    if out is not None:
        check("裁剪保留第一条 system", out[0]["role"] == "system" and out[0]["content"].startswith("固定"))
        check("裁剪保留最后一条 user", any(m.get("role") == "user" and m.get("content") == "当前问题" for m in out))
        tool_ok = True
        for i, m in enumerate(out):
            if m.get("role") == "tool":
                prev = out[i - 1] if i > 0 else {}
                if not (prev.get("role") == "assistant" and prev.get("tool_calls")):
                    tool_ok = False
        check("裁剪不产生孤立 tool 消息", tool_ok, str([m.get("role") for m in out]))
    none_out, est2 = fit_context([{"role": "system", "content": "x" * 400}, {"role": "user", "content": "y" * 400}], 10)
    check("预算连固定前缀都放不下时返回 None", none_out is None and est2 > 10, f"est={est2}")

    # 非法 JSON：绝不能让 write_file 用空参数执行
    invalid_target = Path(_HOME) / "p40-invalid.txt"
    a, store = await build_agent([
        [(None, None, [tc(0, "bad", "write_file", f'{{"path":"{invalid_target}","content":')])],
        [("参数坏了，我没有写文件。", None, None)],
    ])
    sid = await store.create_session()
    events, emit = await _events()
    from agentd.tools import schemas

    await a._loop(sid, [{"role": "user", "content": "写文件"}], schemas(), emit,
                  None, "auto", 3, 0, {})
    check("非法 JSON 产生 invalid_arguments",
          any(e.get("result", {}).get("error_code") == "invalid_arguments"
              for e in events if e.get("type") == "tool_update"), str(events)[:200])
    check("非法 JSON 不执行 write_file", not invalid_target.exists(), str(invalid_target))

    # 连续相同调用：第 3 次（limit=2）被拦，后续收尾仍正常
    a2, store2 = await build_agent([
        [(None, None, [
            tc(0, "r1", "read_file", f'{{"path":"{invalid_target}"}}'),
            tc(1, "r2", "read_file", f'{{"path":"{invalid_target}"}}'),
            tc(2, "r3", "read_file", f'{{"path":"{invalid_target}"}}'),
        ])],
        [("已停止重复读取。", None, None)],
    ])
    sid2 = await store2.create_session()
    ev2, emit2 = await _events()
    # 通过 Settings 把 repeat limit 压到 2，让第 3 个调用被拦
    a2.settings.save({"agent": {"repeat_tool_limit": 2}})
    await a2._loop(sid2, [{"role": "user", "content": "读"}], schemas(), emit2,
                   None, "auto", 3, 0, {})
    check("循环中重复调用被拦并返回可恢复错误",
          any(e.get("result", {}).get("error_code") == "repeat_tool_call"
              for e in ev2 if e.get("type") == "tool_update"), str(ev2)[:200])
    check("重复调用后循环仍能收尾",
          any(e.get("type") == "done" and e.get("stop_reason") == "end_turn" for e in ev2),
          str([(e.get("type"), e.get("stop_reason"), e.get("message")) for e in ev2])[:300])
    await store.close()
    await store2.close()


async def check_tools() -> None:
    print("\n== 工具契约 ==")
    import agentd.tools.files as F
    from agentd.tools import call_tool, ctx

    target = Path(_HOME) / "p40-atomic.txt"
    target.write_text("old", encoding="utf-8")
    old_mtime = target.stat().st_mtime - 10
    os.utime(target, (old_mtime, old_mtime))
    target.write_text("changed by someone else", encoding="utf-8")
    r = await F.write_file(str(target), "mine", expected_mtime=old_mtime)
    check("write_file 旧 mtime 拒绝覆盖", "已被其他程序修改" in r.get("error", ""), str(r))
    check("write_file 冲突时原内容未被覆盖", target.read_text(encoding="utf-8") == "changed by someone else")

    target.write_text("atomic-base", encoding="utf-8")
    before_mode = target.stat().st_mode & 0o777
    r2 = await F.write_file(str(target), "atomic-new")
    check("write_file 原子写成功", r2.get("ok") is True and target.read_text(encoding="utf-8") == "atomic-new", str(r2))
    check("write_file 保留原文件权限", target.stat().st_mode & 0o777 == before_mode,
          f"{before_mode:o} -> {target.stat().st_mode & 0o777:o}")

    # Git 工具：真实临时仓库（同步建仓放到线程，避免在事件循环里阻塞）
    def make_repo() -> Path:
        r = Path(_WS) / "repo"
        r.mkdir(parents=True, exist_ok=True)
        for args in (
            ["git", "init", "-q", "-b", "main"],
            ["git", "config", "user.email", "p40@example.com"],
            ["git", "config", "user.name", "p40"],
        ):
            subprocess.run(args, cwd=r, check=True)
        (r / "hello.txt").write_text("hello\n", encoding="utf-8")
        subprocess.run(["git", "add", "hello.txt"], cwd=r, check=True)
        subprocess.run(["git", "commit", "-qm", "base"], cwd=r, check=True)
        return r

    repo = await asyncio.to_thread(make_repo)
    st = await call_tool("git_status", {"cwd": str(repo)})
    check("git_status 可用且识别 main", st.get("available") is True and st.get("branch") == "main", str(st)[:180])
    check("git_status 包含提交摘要", isinstance(st.get("commit"), str) and "base" in st.get("commit", ""), str(st.get("commit")))
    log = await call_tool("git_log", {"cwd": str(repo), "limit": 3})
    check("git_log 返回最近提交", log.get("ok") is True and log.get("commits"), str(log)[:180])

    def make_patch() -> str:
        (repo / "hello.txt").write_text("hello world\n", encoding="utf-8")
        diff = subprocess.run(["git", "diff", "--", "hello.txt"], cwd=repo,
                              capture_output=True, text=True).stdout
        (repo / "hello.txt").write_text("hello\n", encoding="utf-8")
        return diff

    patch = await asyncio.to_thread(make_patch)
    dry = await call_tool("apply_patch", {"cwd": str(repo), "patch": patch, "dry_run": True})
    check("apply_patch dry-run 预检成功", dry.get("ok") is True and dry.get("dry_run") is True, str(dry)[:180])
    check("apply_patch dry-run 不写文件", (repo / "hello.txt").read_text(encoding="utf-8") == "hello\n")
    applied = await call_tool("apply_patch", {"cwd": str(repo), "patch": patch, "dry_run": False})
    check("apply_patch 真正应用", applied.get("ok") is True and (repo / "hello.txt").read_text(encoding="utf-8") == "hello world\n", str(applied)[:180])
    committed = await call_tool("git_commit", {"cwd": str(repo), "message": "p40 apply", "path": "hello.txt"})
    check("git_commit 提交指定路径", committed.get("ok") is True and "p40 apply" in committed.get("commit", ""), str(committed)[:180])

    # todo_read：上下文里写入后能读回
    from agentd.memory import Store
    store = Store(Path(_AGENT_HOME) / "todo.db")
    await store.start()
    sid = await store.create_session()
    tok_s = ctx.current_session.set(sid)
    tok_st = ctx.current_store.set(store)
    try:
        await call_tool("todo_write", {"todos": [{"content": "查状态", "status": "completed"}]})
        read = await call_tool("todo_read", {})
    finally:
        ctx.current_session.reset(tok_s)
        ctx.current_store.reset(tok_st)
    check("todo_read 读回清单", read.get("count") == 1 and read.get("todos")[0]["content"] == "查状态", str(read))
    await store.close()


async def main() -> int:
    print("== P40 Agent Harness ==")
    await check_loop_guards()
    await check_tools()
    print(f"\n结果: {len(passed)} 通过 / {len(failed)} 失败")
    if failed:
        print("失败项：" + "；".join(failed))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
