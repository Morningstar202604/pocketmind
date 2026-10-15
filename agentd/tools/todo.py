"""todo 清单工具：让模型在 act 过程中持续维护任务进度（对标 Claude Code 的 TodoWrite）。

为什么需要：plan 模式只是一次性输出计划；长任务里模型需要一份「跨 step 存活」
的清单来对抗忘记与跑偏，前端也需要一个可渲染的进度面。清单按会话落库
（store.todos 表），刷新页面/重启后仍在。

分级为 safe：它只写智能体自己的草稿清单，没有外部副作用，不值得打断用户审批；
真正的危险动作仍由各自的工具审批把关。
"""
from __future__ import annotations

from . import Tool, ctx, register

_VALID_STATUS = ("pending", "in_progress", "completed")

SCHEMA = {
    "type": "object",
    "properties": {
        "todos": {
            "type": "array",
            "description": "完整清单（整表覆盖，不是增量）。至少 2 项的任务才值得建清单。",
            "items": {
                "type": "object",
                "properties": {
                    "content": {"type": "string", "description": "任务描述（祈使句，如「查电量」）"},
                    "status": {
                        "type": "string",
                        "enum": list(_VALID_STATUS),
                        "description": "pending / in_progress / completed，同一时刻至多一项 in_progress",
                    },
                },
                "required": ["content", "status"],
            },
        }
    },
    "required": ["todos"],
}

_MAX_ITEMS = 50


async def todo_write(todos: list) -> dict:
    """整表覆盖当前会话的任务清单，并推给前端渲染。"""
    store = ctx.current_store.get()
    sid = ctx.current_session.get()
    if store is None or not sid:
        return {"error": "当前不在会话上下文中，无法维护任务清单"}
    if not isinstance(todos, list):
        return {"error": "todos 必须是数组"}

    norm: list[dict] = []
    for item in todos[:_MAX_ITEMS]:
        if not isinstance(item, dict):
            continue
        content = " ".join(str(item.get("content", "")).split())[:200]
        if not content:
            continue
        status = str(item.get("status", "pending")).strip().lower()
        if status not in _VALID_STATUS:
            status = "pending"
        norm.append({"content": content, "status": status})
    if not norm:
        return {"error": "todos 不能为空（要清空请调用 todo_clear）"}

    await store.set_todos(sid, norm)
    emit = ctx.current_emit.get()
    if emit is not None:
        try:
            await emit({"type": "todo", "todos": norm})
        # aqg: top-level boundary —— 推流失败不影响清单落库
        except Exception:  # noqa: BLE001
            pass
    done = sum(1 for t in norm if t["status"] == "completed")
    return {"ok": True, "count": len(norm), "completed": done, "todos": norm}


async def todo_clear() -> dict:
    """清空当前会话的任务清单（全部完成或任务取消时调用）。"""
    store = ctx.current_store.get()
    sid = ctx.current_session.get()
    if store is None or not sid:
        return {"error": "当前不在会话上下文中"}
    await store.clear_todos(sid)
    emit = ctx.current_emit.get()
    if emit is not None:
        try:
            await emit({"type": "todo", "todos": []})
        # aqg: top-level boundary —— 推流失败不影响清空的落库结果
        except Exception:  # noqa: BLE001
            pass
    return {"ok": True, "count": 0}


register(Tool(
    name="todo_write",
    description=(
        "维护当前任务清单（整表覆盖）。多步任务开始时先建清单，每完成一步就更新状态，"
        "让用户看到进度。同一时刻至多一项 in_progress。"
    ),
    parameters=SCHEMA,
    risk="safe",
    handler=todo_write,
    summary="更新任务清单",
    group="agent",
))

register(Tool(
    name="todo_clear",
    description="清空任务清单（全部完成或任务被取消时调用）。",
    parameters={"type": "object", "properties": {}},
    risk="safe",
    handler=todo_clear,
    summary="清空任务清单",
    group="agent",
))
