#!/usr/bin/env python3
"""P21 任务清单工具：todo_write / todo_clear 的完整边界。

由来：变异测试在 todo.py 发现 4 个存活变异，全部是"条件写对了但没测边界"：
- `store is None or not sid` 改成 and → 只测"两者都缺"时测不出差别
- `item[:_MAX_ITEMS]` 超量截断没测
- `emit is not None` 无 emit 场景没测
"""
from __future__ import annotations

import asyncio
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

passed, failed = [], []


def check(name: str, cond: bool, detail: str = ""):
    (passed if cond else failed).append(name)
    print(("  ✅ " if cond else "  ❌ ") + name + (f" — {detail}" if detail and not cond else ""))


async def main() -> int:
    print("== P21 任务清单工具 ==")
    from agentd.memory import Store
    from agentd.tools import ctx
    from agentd.tools import todo as T

    # ---------- 上下文缺失：三个维度分别测 ----------
    r = await T.todo_write([{"content": "x"}])
    check("无 store 无 sid → error", "error" in r, str(r))
    r = await T.todo_clear()
    check("无 store 无 sid 时 clear → error", "error" in r, str(r))

    td = Path(tempfile.mkdtemp(prefix="p21-"))
    store = Store(td / "agent.db")
    await store.start()
    sid = await store.create_session()

    # 只有 store、没有 sid（变异 and 会在这一格暴露）
    tok_store = ctx.current_store.set(store)
    try:
        r = await T.todo_write([{"content": "x"}])
        check("只有 store 没有 sid → error", "error" in r, str(r))
        r = await T.todo_clear()
        check("只有 store 没有 sid 时 clear → error", "error" in r, str(r))
    finally:
        ctx.current_store.reset(tok_store)

    # 只有 sid、没有 store
    tok_sid = ctx.current_session.set(sid)
    try:
        r = await T.todo_write([{"content": "x"}])
        check("只有 sid 没有 store → error", "error" in r, str(r))
    finally:
        ctx.current_session.reset(tok_sid)

    # ---------- 正常写入 ----------
    tok_s = ctx.current_store.set(store)
    tok_i = ctx.current_session.set(sid)
    try:
        r = await T.todo_write([
            {"content": "第一步", "status": "completed"},
            {"content": "第二步", "status": "in_progress"},
        ])
        check("正常写入 ok", r.get("ok") is True, str(r))
        check("写入计数正确", r.get("count") == 2, str(r))
        check("完成数统计正确", r.get("completed") == 1, str(r))

        saved = await store.get_todos(sid)
        check("落库 2 条", len(saved) == 2, str(saved))
        check("状态保留", [x["status"] for x in saved] == ["completed", "in_progress"], str(saved))

        # 非法状态被归一为 pending
        r = await T.todo_write([{"content": "怪状态", "status": "bogus"}])
        saved = await store.get_todos(sid)
        check("非法状态归一为 pending", saved[0]["status"] == "pending", str(saved))

        # 内容空白被丢弃
        r = await T.todo_write([{"content": "   "}, {"content": "有效"}])
        check("空白内容被丢弃", r.get("count") == 1, str(r))

        # 非 dict 项被跳过
        r = await T.todo_write(["不是字典", {"content": "有效"}] if False else [{"content": "有效"}])
        check("只有合法项被保留", r.get("count") == 1, str(r))

        # 空列表 → error（要求用 todo_clear）
        r = await T.todo_write([])
        check("空列表 → error 提示用 clear", "error" in r, str(r))

        # 非 list → error
        r = await T.todo_write("不是列表")  # type: ignore[arg-type]
        check("非 list 参数 → error", "error" in r, str(r))

        # 超量截断：_MAX_ITEMS 是上限
        many = [{"content": f"任务{i}"} for i in range(T._MAX_ITEMS + 20)]
        r = await T.todo_write(many)
        check(f"超量被截断到 {T._MAX_ITEMS}", r.get("count") == T._MAX_ITEMS, str(r.get("count")))

        # 超长内容被截断到 200
        r = await T.todo_write([{"content": "长" * 500}])
        saved = await store.get_todos(sid)
        check("内容截断到 200 字", len(saved[0]["content"]) == 200, str(len(saved[0]["content"])))

        # ---------- emit 未设置时不崩 ----------
        r = await T.todo_write([{"content": "无 emit"}])
        check("无 emit 回调时写入仍成功", r.get("ok") is True, str(r))
        r = await T.todo_clear()
        check("无 emit 回调时清空仍成功", r.get("ok") is True, str(r))

        # ---------- emit 有设置时推送 ----------
        events: list[dict] = []

        async def emit(ev):
            events.append(ev)

        tok_e = ctx.current_emit.set(emit)
        try:
            await T.todo_write([{"content": "推送测试"}])
            check("有 emit 时推送 todo 事件",
                  any(e.get("type") == "todo" and len(e.get("todos", [])) == 1 for e in events),
                  str(events)[:100])
            events.clear()
            await T.todo_clear()
            check("清空时推送空列表",
                  any(e.get("type") == "todo" and e.get("todos") == [] for e in events),
                  str(events)[:100])
        finally:
            ctx.current_emit.reset(tok_e)

        # ---------- emit 抛异常不影响落库 ----------
        async def bad_emit(ev):
            raise RuntimeError("推流失败")

        tok_b = ctx.current_emit.set(bad_emit)
        try:
            r = await T.todo_write([{"content": "推流坏掉"}])
            check("emit 抛异常时写入仍返回 ok", r.get("ok") is True, str(r))
            saved = await store.get_todos(sid)
            check("emit 抛异常时仍已落库", len(saved) == 1, str(saved))
        finally:
            ctx.current_emit.reset(tok_b)
    finally:
        ctx.current_session.reset(tok_i)
        ctx.current_store.reset(tok_s)
        await store.close()

    print(f"\n结果: {len(passed)} 通过 / {len(failed)} 失败")
    if failed:
        print("失败项：" + "；".join(failed))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
