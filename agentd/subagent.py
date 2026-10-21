"""子代理（subagent）：把一个独立子任务交给一份**全新上下文**去跑，只回结论。

为什么需要它（对标 Claude Code 的 Task / Codex 的子任务）：
主对话的上下文是稀缺资源。让主模型自己连做十几次工具调用去"翻遍目录找配置"，
中间每一步的工具输出都会永久留在主对话里，很快把上下文撑爆，之后的主线任务
就开始"失忆"。子代理把这段探索放进独立的 messages 列表里，跑完只把一段结论
交回来——主对话只增加一行，而不是几百行。

**安全边界（这不是可选项）**：
1. 工具集必须收窄（默认排除 danger 级），否则子代理等于一条绕过审批的提权通道。
2. 禁止嵌套：子代理不能再生成子代理，否则一个递归就能把进程打满。
3. 审批回调原样传递：子代理调用需要审批的工具时，仍然走用户那一次确认，
   不会因为"在子代理里"就免确认。
4. 轮数硬上限：子代理跑飞了不能拖住整轮对话。
"""
from __future__ import annotations

import asyncio
import json

MAX_SUB_ITERS = 8
MAX_RESULT_CHARS = 4000

# 子代理默认不可用的工具：危险操作与"会操纵用户可见状态"的工具。
# todo_* 会改主会话的任务清单，子代理改它会让用户看到莫名其妙的进度。
SUBAGENT_BLOCKED = {
    "run_shell",
    "delete_file",
    "make_call",
    "send_sms",
    "todo_write",
    "todo_clear",
    "subagent",
}


def _clip(text: str, limit: int = MAX_RESULT_CHARS) -> str:
    text = (text or "").strip()
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n…（已截断，原文 {len(text)} 字符）"


async def run_subagent(
    agent,
    task: str,
    ask_approval=None,
    max_iters: int = MAX_SUB_ITERS,
    allow_danger: bool = False,
) -> dict:
    """在独立上下文里跑一个子任务，返回 {ok, result, steps, tools_used}。

    agent 必须是**已构造好**的 Agent 实例（复用同一套 LLM 配置与连接池）；
    这里直接调它的 _loop，不经过 chat()——因为 chat() 会写主会话的消息、
    做摘要、跑 mem0，那些都属于"主线"，不属于子任务。
    """
    from .tools import all_tools, call_tool, get_tool, schemas

    if not isinstance(task, str) or not task.strip():
        return {"ok": False, "error": "子任务描述不能为空"}
    task = task.strip()[:8000]

    # 工具白名单：默认排除危险/状态类；显式 allow_danger 也不放开嵌套
    allowed = {
        t.name
        for t in all_tools()
        if t.name != "subagent" and (allow_danger or t.name not in SUBAGENT_BLOCKED)
    }
    tools = schemas(only=allowed)

    messages: list[dict] = [
        {
            "role": "system",
            "content": (
                "你是一个子代理，只负责完成下面这一个子任务。\n"
                "规则：\n"
                "- 你看到的是独立上下文，看不到主对话的历史；只依据本任务描述行动。\n"
                "- 能用一次工具解决就不要多绕；不要做任务之外的事。\n"
                "- 完成后用简洁的中文给出**结论本身**（结论、关键数据、文件路径），"
                "不要复述你调用了哪些工具、也不要贴大段原始输出。\n"
                "- 如果任务无法完成，直接说明卡在哪、需要什么信息。"
            ),
        },
        {"role": "user", "content": task},
    ]

    steps = 0
    tools_used: list[str] = []
    final_text = ""

    for _ in range(max(1, min(max_iters, MAX_SUB_ITERS))):
        steps += 1
        content_acc = ""
        tool_calls: dict[int, dict] = {}
        try:
            async for content, _reasoning, tc_delta in agent._stream(messages, tools):
                if content:
                    content_acc += content
                if tc_delta:
                    for tc in tc_delta:
                        acc = tool_calls.setdefault(tc.index, {"id": "", "name": "", "args": ""})
                        if tc.id:
                            acc["id"] = tc.id
                        if tc.function:
                            if tc.function.name:
                                acc["name"] = tc.function.name
                            if tc.function.arguments:
                                acc["args"] += tc.function.arguments
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001 —— 子代理失败不能炸掉主对话
            return {"ok": False, "error": f"子代理模型调用失败：{type(e).__name__}: {e}", "steps": steps}

        if content_acc.strip():
            final_text = content_acc.strip()

        if not tool_calls:
            break

        messages.append(
            {
                "role": "assistant",
                "content": content_acc or None,
                "tool_calls": [
                    {
                        "id": tc["id"] or f"sub_{i}",
                        "type": "function",
                        "function": {"name": tc["name"], "arguments": tc["args"] or "{}"},
                    }
                    for i, tc in sorted(tool_calls.items())
                ],
            }
        )

        for i, tc in sorted(tool_calls.items()):
            name = tc["name"]
            tid = tc["id"] or f"sub_{i}"
            if name not in allowed:
                result = {"error": f"子代理无权调用工具 {name}（可用工具已收窄）"}
            else:
                try:
                    args = json.loads(tc["args"] or "{}")
                except json.JSONDecodeError:
                    result = {"error": "工具参数不是合法 JSON"}
                else:
                    tool = get_tool(name)
                    # 需要审批的工具仍然走用户确认，不因子代理而豁免
                    decision = "allow"
                    if ask_approval is not None and tool is not None and tool.risk != "safe":
                        decision = await ask_approval(tid, name, tool.summary or name, tool.risk)
                    if decision != "allow":
                        result = {"denied": "用户拒绝了该操作（子代理）"}
                    else:
                        tools_used.append(name)
                        # **不转发**子代理内部的 tool_start/tool_update 给主对话：
                        # 那些是子任务的中间过程，转发会把主对话塞满工具卡（前端也不认
                        # `sub` 标记）。子代理的价值就是"只回结论"，过程留在独立上下文里。
                        result = await call_tool(name, args)
            messages.append({
                "role": "tool",
                "tool_call_id": tid,
                "content": _clip(json.dumps(result, ensure_ascii=False), 2000),
            })

    if not final_text:
        return {
            "ok": False,
            "error": "子代理未产出结论（可能轮数用尽仍在调用工具）",
            "steps": steps,
            "tools_used": tools_used,
        }
    return {"ok": True, "result": _clip(final_text), "steps": steps, "tools_used": tools_used}
