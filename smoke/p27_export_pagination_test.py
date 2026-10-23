#!/usr/bin/env python3
"""P27 导出完整性：/api/export 不得在会话数超过一页时静默丢数据。

`/api/export` 的产品承诺是「本机数据的完整备份」。原实现调
`list_sessions(limit=max_sessions)`（默认 500）后就结束，第 501 个会话起
**无声消失**——导出文件看起来完整，换机恢复时才发现少了一半历史。
本套件用 `page_size=2` 强制翻页，断言全部会话都被取回且不重复。
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
    print("== P27 导出完整性 ==")
    from agentd.memory import Store

    home = Path(tempfile.mkdtemp(prefix="p27-"))
    store = Store(home / "agent.db")
    await store.start()
    sids = []
    for i in range(5):
        sid = await store.create_session(f"会话{i}")
        await store.add_message(sid, "user", f"消息{i}")
        sids.append(sid)

    # 页大小 2 < 会话数 5：必须翻页取全，而不是只返回第一页
    bundle = await store.export_bundle(max_sessions=100, page_size=2)
    got = [s["id"] for s in bundle["sessions"]]
    check("分页后返回全部 5 个会话", len(got) == 5, f"{len(got)}: {got}")
    check("会话 id 无重复", len(set(got)) == len(got), str(got))
    check("会话 id 集合与库内一致", set(got) == set(sids), f"{set(got)} vs {set(sids)}")
    check("每个会话都带自己的消息",
          all(len(bundle["by_session"][sid]["messages"]) == 1 for sid in sids),
          str({k: len(v["messages"]) for k, v in bundle["by_session"].items()}))

    # 边界：页大小大于总数时一次取完（不应因为循环条件写错而空转）
    bundle2 = await store.export_bundle(max_sessions=100, page_size=100)
    check("单页可取完全部会话", len(bundle2["sessions"]) == 5, str(len(bundle2["sessions"])))

    # max_sessions 是总量上限（不是页大小）：截断必须显式且只截到上限
    capped = await store.export_bundle(max_sessions=3, page_size=2)
    check("max_sessions 作为总量上限生效", len(capped["sessions"]) == 3, str(len(capped["sessions"])))
    check("截断时 by_session 与 sessions 一致",
          set(capped["by_session"]) == {s["id"] for s in capped["sessions"]},
          str(sorted(capped["by_session"])))

    # 空库
    empty = Store(home / "empty.db")
    await empty.start()
    bundle3 = await empty.export_bundle(max_sessions=10, page_size=10)
    check("空库返回空列表", bundle3["sessions"] == [] and bundle3["by_session"] == {}, str(bundle3))
    await empty.close()
    await store.close()

    print(f"\n结果: {len(passed)} 通过 / {len(failed)} 失败")
    if failed:
        print("失败：" + "；".join(failed))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
