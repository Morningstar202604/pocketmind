"""工具执行上下文：把「当前会话 / 存储 / 事件回调」传给工具处理器。

工具处理器的契约是 `handler(**args)`——参数全部来自 LLM 生成的 JSON。但 todo、
知识库这类工具需要知道「当前是哪个会话」「往哪里落库」「怎么给前端推事件」。
这些是运行期上下文而非工具参数，因此用 contextvars 承载：

- Agent.chat 在进入循环前 set，退出时 reset（不泄漏到其它请求）；
- 同一 asyncio 任务内的 await 链天然继承同一份上下文，不会串话；
- 不改变 Tool 注册表的签名契约，第三方/内建工具零感知。
"""
from __future__ import annotations

from collections.abc import Awaitable, Callable
from contextvars import ContextVar
from typing import Any

# 当前会话 ID（空串 = 不在会话上下文中）
current_session: ContextVar[str] = ContextVar("current_session", default="")
# 当前 Store 实例（None = 不在会话上下文中）
current_store: ContextVar[Any] = ContextVar("current_store", default=None)
# 当前 SSE emit 回调（None = 无前端连接，如 Telegram / MCP 场景）
current_emit: ContextVar[Callable[[dict], Awaitable[None]] | None] = ContextVar(
    "current_emit", default=None
)
# 当前 Agent 实例（subagent 工具需要它来构造独立上下文的子任务）
current_agent: ContextVar[Any] = ContextVar("current_agent", default=None)
# 当前审批回调（子代理调用需审批工具时复用，不能因"在子代理里"就免确认）
current_ask: ContextVar[Callable | None] = ContextVar("current_ask", default=None)
