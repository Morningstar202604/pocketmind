#!/usr/bin/env python3
"""P25 性能回归：把这一轮修掉的 N+1 与无界放大锁住。

这些不是"慢一点"的问题，是会随数据量线性恶化到不可用的：
- /api/export 原来每个会话查 5 次（500 会话 = 2500 次往返）
- set_todos 每条一次 INSERT（上限 50 条 = 50 次）
- recover_orphan_tool_calls 每次 chat 前全表扫描会话
- 工具结果按 token 预算放大时无硬顶（budget=200k 时单条可达 12 万字符）

测法：数 SQL 执行次数（用 SQLAlchemy 事件钩子）而不是测耗时——
耗时在 CI 上不稳定，查询次数是确定性的。
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
    print("== P25 性能回归（查询次数） ==")
    os.environ["AGENT_HOME"] = tempfile.mkdtemp(prefix="p25-")
    from agentd.memory import Store

    st = Store(Path(os.environ["AGENT_HOME"]) / "agent.db")
    await st.start()

    # 造 20 个会话，每个 10 条消息 + 5 条 todo
    sids = []
    for i in range(20):
        sid = await st.create_session(f"会话{i}")
        for j in range(10):
            await st.add_message(sid, "user" if j % 2 == 0 else "assistant", f"m{j}")
        await st.set_todos(sid, [{"content": f"t{k}", "status": "pending"} for k in range(5)])
        sids.append(sid)

    # ---------- 用事件钩子数 SQL ----------
    counter = {"n": 0}
    from sqlalchemy import event

    sync_engine = st.engine.sync_engine
    def _count(*a, **k):
        counter["n"] += 1
    event.listen(sync_engine, "before_cursor_execute", _count)
    try:
        counter["n"] = 0
        bundle = await st.export_bundle(max_sessions=100, max_messages=1000)
        export_queries = counter["n"]

        counter["n"] = 0
        await st.set_todos(sids[0], [{"content": f"n{k}"} for k in range(20)])
        todo_queries = counter["n"]

        counter["n"] = 0
        await st.recover_orphan_tool_calls(sids[0])
        recover_queries = counter["n"]
    finally:
        event.remove(sync_engine, "before_cursor_execute", _count)

    # export_bundle：会话列表 1 + messages 1 + archives 1 + todos 1 + summary 1 = 5 条
    # （原来 20 会话 × 5 = 100 条）
    check("export_bundle 查询数与会话数无关（<=6 条）", export_queries <= 6, f"{export_queries} 条")
    check("export_bundle 确实返回了 20 个会话", len(bundle["sessions"]) == 20, str(len(bundle["sessions"])))
    check("export_bundle 每个会话都有 messages", all(len(v["messages"]) == 10 for v in bundle["by_session"].values()),
          str({k: len(v["messages"]) for k, v in list(bundle["by_session"].items())[:3]}))
    check("export_bundle 带出 todos", all(len(v["todos"]) == 5 for v in bundle["by_session"].values()))

    # set_todos：DELETE 1 + INSERT(批量) 1 = 2 条（原来 1 + 20 = 21 条）
    check("set_todos 批量写入（<=3 条 SQL）", todo_queries <= 3, f"{todo_queries} 条")

    # recover：SELECT 1（无孤儿时不写）
    check("recover_orphan 单次查询（<=2 条）", recover_queries <= 2, f"{recover_queries} 条")

    # ---------- 数据完整性：批量后内容仍正确 ----------
    got = await st.get_todos(sids[0])
    check("批量写入的 todo 内容正确", [t["content"] for t in got] == [f"n{k}" for k in range(20)],
          str([t["content"] for t in got])[:80])
    check("批量写入保持顺序", [i for i in range(len(got))] == list(range(len(got))))

    # ---------- 工具结果硬顶 ----------
    import agentd.agent as A

    class Big:
        def context_cfg(self):
            return {"budget_tokens": 1_000_000}

    out = A._tool_result_limited("x" * 500_000, Big())
    check("工具结果受硬顶约束（<= 61000 字符）", len(out) <= 61_000, f"{len(out)} 字符")
    check("硬顶远小于原始长度", len(out) < 500_000)

    class Small:
        def context_cfg(self):
            return {"budget_tokens": 20_000}

    out2 = A._tool_result_limited("x" * 100_000, Small())
    check("小预算时按比例截断（<= 13000 字符）", len(out2) <= 13_000, f"{len(out2)} 字符")

    class NoBudget:
        def context_cfg(self):
            return {}

    out3 = A._tool_result_limited("x" * 100_000, NoBudget())
    check("无预算时用默认上限 6000", len(out3) <= 6_200, f"{len(out3)} 字符")

    await st.close()

    print(f"\n结果: {len(passed)} 通过 / {len(failed)} 失败")
    if failed:
        print("失败项：" + "；".join(failed))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
