"""subagent 工具：把一个独立子任务交给子代理，只把结论带回主对话。

对标 Claude Code 的 Task 工具。存在意义是**省上下文**：一段需要十几次工具
调用的探索，如果由主模型自己做，每一步的原始输出都会永久留在主对话里；
交给子代理后，主对话只增加一段结论。

安全边界见 agentd/subagent.py 的模块注释：工具收窄、禁止嵌套、审批不豁免。
"""
from __future__ import annotations

from . import Tool, ctx, register

SCHEMA = {
    "type": "object",
    "properties": {
        "task": {
            "type": "string",
            "description": (
                "交给子代理的完整任务描述。子代理看不到主对话，"
                "所以必须把背景、要查什么、期望输出什么格式都写清楚。"
            ),
        },
        "max_steps": {
            "type": "integer",
            "description": "子代理最多执行几轮工具调用，默认 8，上限 8",
        },
    },
    "required": ["task"],
}


async def subagent(task: str, max_steps: int = 8) -> dict:
    from ..subagent import run_subagent

    agent = ctx.current_agent.get()
    if agent is None:
        return {"error": "当前环境不支持子代理（不在会话上下文中）"}

    ask = ctx.current_ask.get()
    return await run_subagent(
        agent,
        task,
        ask_approval=ask,
        max_iters=max_steps or 8,
    )


register(Tool(
    name="subagent",
    description=(
        "把一段独立的、需要多次工具调用的子任务交给子代理去做，只拿回结论。"
        "适合：翻找多个文件才得出结论、需要连续读取/检索的调研、与主线目标无关的杂活。"
        "不适合：一句话就能回答的问题、必须由你亲自决定的事、需要用户来回确认的交互。"
        "子代理看不到当前对话，任务描述必须自带全部背景。"
    ),
    parameters=SCHEMA,
    risk="safe",
    handler=subagent,
    summary="派发子任务给子代理",
    group="agent",
    timeout=300,
))
