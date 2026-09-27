"""MCP 客户端（P1-4：官方 mcp Python SDK 替换手写 JSON-RPC stdio）。

仍保持与旧手写客户端完全一致的对外接口：
  client = MCPClient(name, command, args)
  tools = await client.connect()          # -> [{name, description, inputSchema}]
  result = await client.call_tool(name, arguments)  # -> {"mcp_result": str} / {"error": ...}
  await client.close()

协议版本协商、stderr 日志、错误处理、子进程生命周期全部交给官方 SDK；
本类只做薄封装：把 SDK 的 Tool / CallToolResult 转成 main.py 期望的 dict 形状。
"""
from __future__ import annotations

import asyncio
from contextlib import AsyncExitStack


class MCPClient:
    """连接一个 stdio MCP 服务器（官方 SDK 管理子进程 + JSON-RPC 会话）。"""

    def __init__(self, name: str, command: str, args: list[str] | None = None, timeout: float = 20.0):
        self.name = name
        self.command = command
        self.args = args or []
        self.timeout = timeout
        self.tools: list[str] = []  # 由 main.py 连接成功后回填
        # AsyncExitStack 负责长期持有 stdio_client 与 ClientSession 两个异步上下文，
        # 使连接在整个应用生命周期内保持打开，而不是每次调用都重连。
        self._stack: AsyncExitStack | None = None
        self._session = None

    async def connect(self) -> list[dict]:
        """启动子进程并完成 initialize 握手；返回 tools/list 结果（dict 形状）。"""
        from mcp import ClientSession
        from mcp.client.stdio import StdioServerParameters, stdio_client

        params = StdioServerParameters(command=self.command, args=list(self.args))
        self._stack = AsyncExitStack()
        read, write = await self._stack.enter_async_context(stdio_client(params))
        self._session = await self._stack.enter_async_context(ClientSession(read, write))
        # 官方 SDK 在此完成协议版本协商 / 能力交换
        await self._session.initialize()
        result = await self._session.list_tools()
        return [self._tool_to_dict(t) for t in (result.tools or [])]

    @staticmethod
    def _tool_to_dict(t) -> dict:
        """把 SDK 的 Tool 对象转成 main.py 期望的 dict（inputSchema 驼峰对齐）。"""
        return {
            "name": t.name,
            "description": t.description or "",
            "inputSchema": t.input_schema or {"type": "object", "properties": {}},
        }

    async def call_tool(self, tool_name: str, arguments: dict) -> dict:
        """调用 MCP 工具，返回结构化结果（兼容内建工具的输出形状）。"""
        if self._session is None:
            return {"error": "MCP 客户端未连接"}
        try:
            result = await asyncio.wait_for(
                self._session.call_tool(tool_name, arguments or {}),
                timeout=self.timeout,
            )
        except asyncio.TimeoutError:
            return {"error": f"MCP 工具 {tool_name} 超时"}
        except Exception as e:  # noqa: BLE001 —— 调用异常转成结构化错误，不冒泡崩服务
            return {"error": f"MCP 调用失败：{e}"}
        # SDK 的 CallToolResult：is_error 标记业务错误，content 为内容块列表
        if getattr(result, "is_error", False):
            texts = [
                c.text for c in (result.content or [])
                if getattr(c, "type", "") == "text"
            ]
            return {"error": "\n".join(t for t in texts if t) or "MCP 调用失败"}
        texts = [
            c.text for c in (result.content or [])
            if getattr(c, "type", "") == "text"
        ]
        return {"mcp_result": "\n".join(texts)}

    async def close(self) -> None:
        """关闭 stdio 子进程（由 SDK 负责优雅退出 + 清理）。"""
        if self._stack is not None:
            try:
                await self._stack.aclose()
            except Exception:  # noqa: BLE001
                pass
        self._stack = None
        self._session = None
