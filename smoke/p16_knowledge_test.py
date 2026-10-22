#!/usr/bin/env python3
"""P16 知识库工具：kb_add / kb_search / kb_list + 全部错误分支。

为什么测：知识库是"用户策展的长期资料"入口，覆盖率只有 31%。
已有 p7 只覆盖了「有/无会话上下文」的边界，没测内容长度、空值、
检索命中/未命中、FTS 与 LIKE 降级路径。
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
    print("== P16 知识库工具 ==")
    from agentd.memory import Store
    from agentd.tools import ctx
    from agentd.tools import knowledge as K

    # 无上下文时：三个工具都必须明确报"没有知识库"，而不是抛异常
    r = await K.kb_add("t", "c")
    check("无上下文 kb_add 返回 error", "error" in r, str(r))
    r = await K.kb_search("q")
    check("无上下文 kb_search 返回 error", "error" in r, str(r))
    r = await K.kb_list()
    check("无上下文 kb_list 返回 error", "error" in r, str(r))

    # 注入真实 store
    td = Path(tempfile.mkdtemp(prefix="p16-"))
    store = Store(td / "agent.db")
    await store.start()
    tok = ctx.current_store.set(store)
    try:
        # ---------- kb_add ----------
        r = await K.kb_add("保修卡", "笔记本保修到 2027-03-01，序列号 ABC123")
        check("kb_add 成功", r.get("ok") is True, str(r))
        check("kb_add 返回 id", isinstance(r.get("id"), int) or bool(r.get("id")), str(r))
        check("kb_add 返回字数", r.get("chars") == len("笔记本保修到 2027-03-01，序列号 ABC123"), str(r.get("chars")))

        r = await K.kb_add("空内容", "   ")
        check("kb_add 空内容被拒", "不能为空" in r.get("error", ""), str(r))

        r = await K.kb_add("超长", "x" * 20001)
        check("kb_add 超 20000 字被拒", "过长" in r.get("error", ""), str(r))

        # 边界等号：恰好 20000 字必须能存（变异测试发现 >/>= 互换没被测到）
        r = await K.kb_add("恰好20000", "y" * 20000)
        check("kb_add 恰好 20000 字可存", r.get("ok") is True, str(r)[:80])
        r = await K.kb_add("再多一字", "z" * 20001)
        check("kb_add 20001 字被拒（等号边界）", "过长" in r.get("error", ""), str(r))

        r = await K.kb_add("", "没有标题的内容")
        check("kb_add 空标题回退为「未命名」", r.get("title") == "未命名", str(r.get("title")))

        r = await K.kb_add("长标题" * 40, "内容")
        check("kb_add 标题截断到 120 字", len(r.get("title", "")) <= 120, str(len(r.get("title", ""))))

        # ---------- kb_search ----------
        r = await K.kb_search("序列号")
        check("kb_search 命中中文子串", r.get("count", 0) >= 1, str(r))
        check("kb_search 结果含标题", any("保修卡" in x["title"] for x in r.get("results", [])), str(r)[:120])
        check("kb_search 结果附带正文", any("ABC123" in x["content"] for x in r.get("results", [])))

        r = await K.kb_search("绝对不存在的词组xyz")
        check("kb_search 未命中返回 count=0 而非报错", r.get("ok") is True and r.get("count") == 0, str(r))

        r = await K.kb_search("")
        check("kb_search 空 query 被拒", "不能为空" in r.get("error", ""), str(r))

        r = await K.kb_search("保修", limit=100)
        check("kb_search limit 被钳制到 20（不报错）", r.get("ok") is True, str(r))

        r = await K.kb_search("保修", limit=0)
        check("kb_search limit=0 被钳到 1（不报错）", r.get("ok") is True, str(r))

        # ---------- kb_list ----------
        r = await K.kb_list()
        check("kb_list 返回全部条目", r.get("count", 0) >= 2, str(r.get("count")))
        check("kb_list 条目不返回正文（只给字数）",
              all("content" not in i for i in r.get("items", [])), str(r)[:100])
        check("kb_list 带 chars 字段", all("chars" in i for i in r.get("items", [])), str(r)[:100])

        # ---------- SQL 注入尝试（应被参数化挡住） ----------
        r = await K.kb_search("' OR 1=1 --")
        check("注入式检索不报错也不泄露全量", r.get("ok") is True, str(r))
        check("注入式检索不会返回所有条目", r.get("count", 99) <= 1, str(r.get("count")))

        r = await K.kb_add("x'); DROP TABLE knowledge; --", "注入标题")
        check("注入式标题正常入库（被转义）", r.get("ok") is True, str(r))
        r = await K.kb_list()
        check("注入后表仍存在", r.get("ok") is True and r.get("count", 0) >= 3, str(r.get("count")))
    finally:
        ctx.current_store.reset(tok)
        await store.close()

    print(f"\n结果: {len(passed)} 通过 / {len(failed)} 失败")
    if failed:
        print("失败项：" + "；".join(failed))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
