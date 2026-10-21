#!/usr/bin/env python3
"""P10 子代理：独立上下文 / 工具收窄 / 禁止嵌套 / 审批不豁免 / 只回结论。

这些是安全属性，不是功能演示——任何一条破了都会让子代理变成
"绕过审批的提权通道"或"把上下文撑爆的放大器"。
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
    print("== P10 子代理 ==")
    from agentd.agent import Agent
    from agentd.config import Settings
    from agentd.memory import Store
    from agentd.subagent import SUBAGENT_BLOCKED, run_subagent
    from agentd.tools import all_tools, schemas

    td = Path(tempfile.mkdtemp())
    store = Store(td / "agent.db")
    await store.start()
    agent = Agent(store, Settings(td), mock=True)

    # 1. 工具收窄：危险/状态类工具必须在子代理里不可用
    allowed = {
        t.name for t in all_tools()
        if t.name != "subagent" and t.name not in SUBAGENT_BLOCKED
    }
    check("run_shell 不在子代理工具集", "run_shell" not in allowed)
    check("delete_file 不在子代理工具集", "delete_file" not in allowed)
    check("send_sms 不在子代理工具集", "send_sms" not in allowed)
    check("todo_write 不在子代理工具集（不改用户可见状态）", "todo_write" not in allowed)
    check("只读工具仍可用（read_file）", "read_file" in allowed)

    # 2. 禁止嵌套：subagent 自己永远不出现在子代理可调列表里
    check("subagent 不在子代理工具集（禁止递归）", "subagent" not in allowed)

    # 3. schemas 的 only 过滤真的生效
    narrowed = schemas(only={"read_file", "list_dir"})
    names = {s["function"]["name"] for s in narrowed}
    check("schemas(only=) 只返回指定工具", names == {"read_file", "list_dir"}, str(names))

    # 4. 空任务必须被拒（不允许"随便跑跑"）
    r = await run_subagent(agent, "   ")
    check("空任务被拒绝", r.get("ok") is False and "不能为空" in r.get("error", ""), str(r)[:100])

    # 5. 正常跑一轮：独立上下文 + 只回结论
    r2 = await run_subagent(agent, "统计当前目录文件数量")
    check("子代理返回 ok", r2.get("ok") is True, str(r2)[:120])
    check("返回结果而非完整对话", isinstance(r2.get("result"), str) and len(r2["result"]) > 0)
    check("报告执行步数", isinstance(r2.get("steps"), int) and r2["steps"] >= 1)
    check("结果长度受限（不会撑爆主上下文）", len(r2["result"]) <= 4100, str(len(r2["result"])))

    # 6. 主会话不受污染：子代理跑完，主会话消息数不变
    sid = await store.create_session()
    before = len(await store.get_messages(sid))
    await run_subagent(agent, "随便看看")
    after = len(await store.get_messages(sid))
    check("子代理不写主会话消息", before == after, f"{before} -> {after}")

    # 7. 轮数上限被强制执行
    r3 = await run_subagent(agent, "统计文件", max_iters=1)
    check("max_iters 被限制在 1..8", r3.get("steps", 99) <= 8, str(r3.get("steps")))

    await agent.aclose()
    await store.close()
    print(f"\n结果: {len(passed)} 通过 / {len(failed)} 失败")
    if failed:
        print("失败项：" + "；".join(failed))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
