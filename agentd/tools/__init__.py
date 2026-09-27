"""工具注册表。

每个工具 = 名称 + 描述 + JSON Schema 参数 + 危险度 + 异步执行函数。
LLM 通过 OpenAI 兼容的 function calling 拿到结构化定义，
不再像旧实现那样"靠猜 shell 命令 + 自己解析 JSON"。
"""
from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

Handler = Callable[..., Awaitable[dict]]


@dataclass
class Tool:
    name: str
    description: str
    parameters: dict
    risk: str
    handler: Handler
    summary: str = ""          # 审批卡片上展示的一句话说明
    timeout: int = 120         # 单次执行超时（秒）
    group: str = "other"       # 工具分组：phone/files/system/voice/mcp


_REGISTRY: dict[str, Tool] = {}


def register(tool: Tool) -> None:
    _REGISTRY[tool.name] = tool


def get_tool(name: str) -> Tool | None:
    return _REGISTRY.get(name)


def all_tools() -> list[Tool]:
    return list(_REGISTRY.values())


def schemas() -> list[dict]:
    """转成 OpenAI function calling 需要的 tools 参数。"""
    return [
        {
            "type": "function",
            "function": {
                "name": t.name,
                "description": t.description,
                "parameters": t.parameters,
            },
        }
        for t in all_tools()
    ]


async def call_tool(name: str, args: dict) -> dict:
    """执行工具，统一捕获异常与超时，永远返回结构化结果。"""
    tool = get_tool(name)
    if tool is None:
        return {"error": f"未知工具：{name}"}
    if not isinstance(args, dict):
        args = {}
    # P1：pydantic 参数校验（类型强制 + 必填检查），模型幻觉参数直接拦下，不执行
    err = validate_args(tool, args)
    if err:
        return {"error": f"参数不合法：{err}"}
    try:
        return await asyncio.wait_for(tool.handler(**args), timeout=tool.timeout)
    except TimeoutError:
        return {"error": f"工具 {name} 执行超时（>{tool.timeout}s）"}
    except Exception as e:  # noqa: BLE001 —— 工具失败必须回传给模型
        return {"error": f"{type(e).__name__}: {e}"}


# ---------- P1：参数校验（pydantic，fastapi 已带，零新增依赖） ----------
_TYPE_MAP = {
    "string": str,
    "integer": int,
    "number": float,
    "boolean": bool,
    "array": list,
    "object": dict,
}

_model_cache: dict[str, Any] = {}


def _build_model(tool: Tool):
    """由 JSON Schema 构造 pydantic 模型：类型强制、必填校验、未知字段忽略。"""
    if tool.name in _model_cache:
        return _model_cache[tool.name]
    from pydantic import ConfigDict, Field, create_model

    params = tool.parameters or {}
    props = params.get("properties", {}) or {}
    required = set(params.get("required", []) or [])
    fields: dict[str, Any] = {}
    for key, spec in props.items():
        t = _TYPE_MAP.get(str(spec.get("type", "string")), str)
        desc = str(spec.get("description", ""))
        if key in required:
            fields[key] = (t, Field(..., description=desc))
        else:
            fields[key] = (t | None, Field(default=None, description=desc))
    model = create_model(
        f"{tool.name}Args",
        __config__=ConfigDict(extra="ignore"),
        **fields,
    )
    _model_cache[tool.name] = model
    return model


def validate_args(tool: Tool, args: dict) -> str:
    """返回错误描述字符串；空字符串 = 通过。"""
    if not (tool.parameters or {}).get("properties"):
        return ""
    try:
        _build_model(tool).model_validate(args)
        return ""
    except Exception as e:  # noqa: BLE001
        return str(e).split("\n")[0][:200]


# 导入子模块完成注册（必须在 _REGISTRY 定义之后）
from . import files, phone, shell  # noqa: E402,F401

# 可选能力：离线语音（sherpa-onnx 未安装时注册仍成功，调用时返回安装指引）
from . import voice as _voice  # noqa: E402

_voice.register_voice_tool()
