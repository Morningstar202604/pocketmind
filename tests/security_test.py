#!/usr/bin/env python3
"""安全专项回归测试：路径穿越 / FTS5 注入 / SSRF scheme / chat 模式工具禁用 / token 打码。

这些用例不需要启动 HTTP 服务，直接在进程内调用工具与 Store，与 p0_regression 同级。
每条都对应一次真实攻击载荷，断言被服务端强制拦截。
"""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

passed, failed = [], []


def check(name: str, cond: bool, detail: str = ""):
    (passed if cond else failed).append(name)
    print(("  ✅ " if cond else "  ❌ ") + name + (f" — {detail}" if detail and not cond else ""))


async def test_path_traversal():
    print("== 安全-1: files 路径穿越 ==")
    from agentd.tools import files

    with tempfile.TemporaryDirectory() as td:
        # 把 $HOME 指到临时目录，避免真实主目录干扰
        with mock.patch.object(files.Path, "home", return_value=Path(td)):
            home = Path(td)
            (home / "normal.txt").write_text("hello", encoding="utf-8")
            # 1) 相对路径向上逃逸
            try:
                files._resolve("../../etc/passwd")
                check("路径穿越 ../../etc/passwd 被拒", False, "未抛异常")
            except ValueError:
                check("路径穿越 ../../etc/passwd 被拒", True)
            # 2) 绝对路径逃逸到 /etc
            try:
                files._resolve("/etc/passwd")
                check("绝对路径 /etc/passwd 被拒", False, "未抛异常")
            except ValueError:
                check("绝对路径 /etc/passwd 被拒", True)
            # 3) 符号链接逃逸：home 内建软链指向 /etc
            link = home / "evil_link"
            try:
                link.symlink_to("/etc")
            except (OSError, NotImplementedError):
                link = None
            if link is not None:
                try:
                    files._resolve(str(link / "passwd"))
                    check("symlink 逃逸被拒（resolve 跟随后越界）", False, "未抛异常")
                except ValueError:
                    check("symlink 逃逸被拒（resolve 跟随后越界）", True)
            else:
                check("symlink 逃逸被拒（resolve 跟随后越界）", True, "本环境不支持 symlink，跳过")
            # 4) 正常 home 内路径放行
            try:
                p = files._resolve("normal.txt")
                check("home 内正常路径放行", p.is_absolute() and "normal.txt" in str(p))
            except ValueError as e:
                check("home 内正常路径放行", False, str(e))


async def test_fts_injection():
    print("== 安全-2: FTS5 MATCH 注入 ==")
    from agentd.memory import Store

    # _fts_phrase 必须把任意词包成短语字面量，内部双引号翻倍
    phrase = Store._fts_phrase('a"b*c (d NEAR e):f')
    check("FTS 短语用双引号包裹且内部引号翻倍",
          phrase.startswith('"') and phrase.endswith('"') and '""' in phrase,
          phrase)

    with tempfile.TemporaryDirectory() as td:
        store = Store(Path(td) / "agent.db")
        await store.start()
        sid = await store.create_session()
        await store.set_summary(sid, "关于量子计算与密码学的一段普通摘要")
        # 用一堆 FTS 特殊字符搜索：不应抛 SQL 语法错误，且能回退/正常返回
        bad_queries = [
            '量子" OR 1=1 --',
            "量子 * NEAR(",
            "a:b)c[",
            "'; DROP TABLE memory;--",
            '引号"" 测试',
        ]
        ok = True
        for q in bad_queries:
            try:
                await store.search_memories(q, limit=3)
            except Exception as e:  # noqa: BLE001
                ok = False
                check(f"FTS 特殊字符搜索不崩溃: {q!r}", False, repr(e))
        check("FTS5 特殊字符注入搜索全部不抛错", ok)
        await store.close()


async def test_ssrf_scheme():
    print("== 安全-3: download/open URL scheme 白名单 ==")
    from agentd.tools.phone._termux import _safe_http_url

    check("拒绝 file://", _safe_http_url("file:///etc/passwd") is None)
    check("拒绝 javascript:", _safe_http_url("javascript:alert(1)") is None)
    check("拒绝 data:", _safe_http_url("data:text/html,<script>x</script>") is None)
    check("拒绝空 URL", _safe_http_url("") is None)
    check("拒绝无 scheme", _safe_http_url("example.com/x") is None)
    check("放行 http", _safe_http_url("http://example.com/a.bin") == "http://example.com/a.bin")
    check("放行 https", _safe_http_url("https://example.com/a.bin") == "https://example.com/a.bin")


async def test_chat_mode_no_tool_exec():
    print("== 安全-4: chat 模式强制禁用工具 ==")
    from agentd.agent import Agent

    class FakeStore:
        path = Path("/tmp/nonexistent.db")

        async def first_user_message(self, sid):
            return None

        async def create_session(self):
            return "sid"

        async def touch_session(self, sid, title=None):
            return None

        async def search_memories(self, *a, **kw):
            return []

        async def get_summary(self, sid):
            return None

        async def get_archives(self, sid, limit=5):
            return []

        async def archive_messages(self, sid, old_msgs, cfg, llm_client=None):
            return None

        async def get_messages(self, sid):
            return []

        async def add_message(self, *a, **kw):
            return {}

        async def set_summary(self, sid, s):
            return None

    class FakeSettings:
        def permission_mode(self):
            return "chat"

        def agent_mode(self):
            return "act"

        def get(self):
            return {"llm": {"api_key": "", "model": "", "base_url": ""}, "user_prefs": ""}

    agent = Agent(FakeStore(), FakeSettings(), mock=True)

    # 伪造流式输出：模型在 chat 模式下仍强行吐出一个 run_shell tool_call（注入场景）
    async def fake_stream(messages, tools):
        # chat 模式下传给 LLM 的 tools 必须为空
        check("chat 模式不向 LLM 暴露 tools", tools == [], f"tools={tools}")
        # 第一帧：普通文本
        yield "好的", None, None
        # 第二帧：强行吐出 tool_call（模拟被注入后的模型）
        yield "", None, [mock_tool_call("run_shell", {"command": "echo pwned"})]

    def mock_tool_call(name, args):
        class TC:
            index = 0
            id = "call_1"
        tc = TC()

        class FN:
            function = type("F", (), {"name": name, "arguments": __import__("json").dumps(args)})()
        tc.function = FN.function
        return tc

    executed = []

    async def fake_call_tool(name, args):
        executed.append(name)
        return {"ok": True, "stdout": "pwned"}

    emits = []

    async def fake_emit(ev):
        emits.append(ev)

    agent._stream = fake_stream  # type: ignore[method-assign]
    with mock.patch("agentd.agent.call_tool", side_effect=fake_call_tool):
        await agent.chat("sid", "你好", emit=fake_emit, ask_approval=None)

    check("chat 模式下模型被注入后 run_shell 未被执行", executed == [], f"executed={executed}")
    check("chat 模式下有 denied 记录",
          any(e.get("type") == "tool_update" and e.get("status") == "denied" for e in emits),
          str([e.get("type") for e in emits]))


async def test_token_masking():
    print("== 安全-5: 敏感 token 打码 ==")
    from agentd.config import mask_key

    fixture_key = "fixture-api-key-alpha-1234567890"
    masked = mask_key(fixture_key)
    check("mask_key 不透明明文", fixture_key not in masked and "fixture-api-key-alpha" not in masked,
          masked)
    check("mask_key 空串返回空", mask_key("") == "")


async def test_memory_search_auth():
    print("== 安全-6: memory/search 路由鉴权依赖存在 ==")
    from agentd.main import app as _app

    target = None
    for r in _app.routes:
        if getattr(r, "path", None) == "/api/memory/search":
            target = r
            break
    check("找到 /api/memory/search 路由", target is not None)
    if target is not None:
        names = [getattr(d.dependency, "__name__", "") for d in target.dependencies]
        check("memory/search 带 check_token 依赖", "check_token" in names, str(names))


async def test_approval_scope():
    print("== 安全-6: auto 模式黑名单与危险工具始终允许 ==")
    from types import SimpleNamespace

    from agentd.agent import Agent
    from agentd.approval import ApprovalCenter

    with tempfile.TemporaryDirectory() as td:
        center = ApprovalCenter(timeout_cb=lambda: 1, persist_path=Path(td) / "approval.json")

        gate = asyncio.create_task(center.ask("s1", "t1", "run_shell", "shell", "danger"))
        await asyncio.sleep(0)
        center.decide("s1", "t1", "deny_always")
        decision = await gate
        check("deny_always 写入全局黑名单", decision == "deny" and center.is_auto_denied("run_shell"))

        gate2 = asyncio.create_task(center.ask("s1", "t2", "danger_tool", "shell", "danger"))
        await asyncio.sleep(0)
        center.decide("s1", "t2", "allow_always")
        decision2 = await gate2
        check("危险工具始终允许不落白名单", decision2 == "allow" and not center.is_auto_allowed("s1", "danger_tool"))

        # 模拟旧版本留下的危险工具全局白名单：不能让 run_shell 静默放行
        center._global.add("legacy_danger")
        gate3 = asyncio.create_task(center.ask("s1", "t3", "legacy_danger", "shell", "danger"))
        await asyncio.sleep(0.05)
        pending = any(x["tool_call_id"] == "t3" for x in center.pending())
        center.decide("s1", "t3", "allow_once")
        await gate3
        check("旧全局白名单中的危险工具仍需逐次确认", pending)

        class AutoSettings:
            def permission_mode(self):
                return "auto"

        agent = Agent(
            None, AutoSettings(), mock=True, deny_check=lambda name: name == "run_shell"
        )  # type: ignore[arg-type]
        got = await agent._decide_tool(
            tid="t3", name="run_shell", tool=SimpleNamespace(risk="danger", summary="shell"),
            mode="auto", ask_approval=None,
        )
        check("auto 模式仍拒绝全局黑名单工具", got == "deny", got)



async def main():
    await test_path_traversal()
    await test_memory_search_auth()
    await test_fts_injection()
    await test_ssrf_scheme()
    await test_chat_mode_no_tool_exec()
    await test_approval_scope()
    await test_token_masking()
    print(f"\n结果: {len(passed)} 通过 / {len(failed)} 失败")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    asyncio.run(main())
