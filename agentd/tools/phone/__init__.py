"""手机能力工具包：封装 termux-api 系统命令（电池、通信、感知、网络、媒体等）。

按职责拆分为 system / communication / sensors / network / media 子模块，
由本 __init__ 统一注册到工具注册表。
"""
from __future__ import annotations

from .. import Tool, register


def _t(name: str, description: str, properties: dict, risk: str, handler, summary: str,
       required: list | None = None, timeout: int = 20) -> None:
    register(Tool(
        name=name,
        description=description,
        parameters={"type": "object", "properties": properties,
                    **({"required": required} if required else {})},
        risk=risk,
        handler=handler,
        summary=summary,
        timeout=timeout,
        group="phone",
    ))


# 子模块导入（须在 _REGISTRY 定义之后 → tools/__init__.py 已保证）
from . import (  # noqa: E402,F401  # 子模块须在 _REGISTRY 定义之后导入
    communication,
    media,
    network,
    sensors,
    system,
)
