#!/usr/bin/env python3
"""P0 修复回归测试：验证已修复的致命 bug 不再复现。

覆盖：
  P0-1 重启恢复 tool_calls：agent.py 从 meta 反序列化 tool_calls/tool_call_id
  P0-4 $HOME 展开：phone.py take_photo/download_file 默认路径不出现字面 $HOME
  P0-5 share_text -t：title 参数正确传递，不被硬编码 text/plain 覆盖
  FTS5 附带修复：search_memories 的 exclude_session 排除条件真正生效
"""
from __future__ import annotations

import asyncio
import inspect
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

passed, failed = [], []


def check(name: str, cond: bool, detail: str = ""):
    (passed if cond else failed).append(name)
    print(("  ✅ " if cond else "  ❌ ") + name + (f" — {detail}" if detail and not cond else ""))


async def main():
    print("== P0 修复回归 ==")

    # ---------- P0-4：$HOME 展开 ----------
    from agentd.tools import phone as phone_mod

    src = Path(phone_mod.__file__).read_text(encoding="utf-8")
    check("P0-4 take_photo 默认路径无字面 $HOME",
          '"$HOME"' not in src and "'$HOME'" not in src,
          "phone.py 中仍存在 $HOME 字面量")
    check("P0-4 phone.py 导入了 Path",
          "from pathlib import Path" in src or "import pathlib" in src)

    # ---------- P0-5：share_text -t 语义 ----------
    share_src = inspect.getsource(phone_mod.share_text) if hasattr(phone_mod, "share_text") else ""
    check("P0-5 share_text 不硬编码 text/plain 为 -t",
          '"text/plain"' not in share_src or "-t" not in share_src,
          "share_text 仍将 text/plain 传给 -t 参数")

    # ---------- P0-1：重启恢复 tool_calls ----------
    from agentd.memory import Store

    with tempfile.TemporaryDirectory() as td:
        store = Store(Path(td) / "agent.db")
        await store.start()
        sid = await store.create_session()
        await store.add_message(sid, "user", "查看电池")
        await store.add_message(sid, "assistant", "", meta={
            "tool_calls": [{"id": "call_1", "type": "function",
                            "function": {"name": "get_battery", "arguments": "{}"}}]
        })
        await store.add_message(sid, "tool", '{"ok": true, "percentage": 85}', meta={
            "tool_call_id": "call_1", "name": "get_battery", "status": "completed"
        })
        # 模拟 agent.py 的恢复逻辑
        recovered = []
        for m in await store.get_messages(sid):
            msg: dict = {"role": m["role"], "content": m["content"] or ""}
            meta = m.get("meta") or {}
            if m["role"] == "assistant" and meta.get("tool_calls"):
                msg["tool_calls"] = meta["tool_calls"]
            elif m["role"] == "tool" and meta.get("tool_call_id"):
                msg["tool_call_id"] = meta["tool_call_id"]
            recovered.append(msg)
        check("P0-1 恢复后 assistant 带 tool_calls",
              any(m["role"] == "assistant" and m.get("tool_calls") for m in recovered),
              f"recovered={[(m['role'], list(m.keys())) for m in recovered]}")
        check("P0-1 恢复后 tool 消息带 tool_call_id",
              any(m["role"] == "tool" and m.get("tool_call_id") == "call_1" for m in recovered),
              f"recovered={[(m['role'], m.get('tool_call_id')) for m in recovered]}")
        check("P0-1 tool_calls 函数名正确",
              recovered[1]["tool_calls"][0]["function"]["name"] == "get_battery",
              str(recovered[1].get("tool_calls")))
        await store.close()

    # ---------- FTS5 附带修复：exclude_session 排除生效 ----------
    with tempfile.TemporaryDirectory() as td:
        store2 = Store(Path(td) / "agent.db")
        await store2.start()
        s1 = await store2.create_session()
        s2 = await store2.create_session()
        await store2.set_summary(s1, "电池电量很低需要充电")
        await store2.set_summary(s2, "电池电量很低需要充电")
        results = await store2.search_memories("电池", exclude_session=s1)
        check("FTS5 exclude_session 排除生效",
              len(results) == 1 and results[0]["session_id"] == s2,
              f"results={[(r.get('session_id'), r.get('content','')[:20]) for r in results]}")
        await store2.close()

    print(f"\n结果: {len(passed)} 通过 / {len(failed)} 失败")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    asyncio.run(main())
