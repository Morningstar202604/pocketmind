"""Shell 工具：在 Termux 里执行命令。

任意 shell 属于最高危险级（danger）：
- approve 模式下必须用户在界面确认后才能执行；
- 输出做长度截断，防止超长输出撑爆上下文。
"""
from __future__ import annotations

import asyncio
import shutil

from . import Tool, register

MAX_OUTPUT = 30000

SCHEMA = {
    "type": "object",
    "properties": {
        "command": {
            "type": "string",
            "description": "要执行的完整 shell 命令，例如 ls -la ~/storage",
        },
        "timeout": {
            "type": "integer",
            "description": "超时秒数，默认 60，最大 300",
            "minimum": 1,
            "maximum": 300,
        },
    },
    "required": ["command"],
}


async def run_shell(command: str, timeout: int = 60) -> dict:
    if not isinstance(command, str) or not command.strip():
        return {"error": "命令不能为空"}
    shell = shutil.which("bash") or "/bin/sh"
    timeout = max(1, min(int(timeout or 60), 300))
    proc = await asyncio.create_subprocess_exec(
        shell, "-lc", command,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    timed_out = False
    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except TimeoutError:
        timed_out = True
        try:
            proc.kill()
        except ProcessLookupError:
            pass
        out, err = await proc.communicate()
    stdout = out.decode("utf-8", errors="replace").strip()
    stderr = err.decode("utf-8", errors="replace").strip()
    result = {
        "ok": proc.returncode == 0,
        "exit_code": proc.returncode,
        "stdout": stdout[:MAX_OUTPUT],
        "stderr": stderr[:MAX_OUTPUT],
        "timed_out": timed_out,
    }
    if len(stdout) > MAX_OUTPUT:
        result["truncated"] = True
    return result


register(
    Tool(
        name="run_shell",
        description="在 Termux 终端执行一条 shell 命令并返回输出。用于查系统信息、管理文件、运行脚本等。",
        parameters=SCHEMA,
        risk="danger",
        handler=run_shell,
        summary="执行 shell 命令（可运行任意命令，请确认是你想要的）",
        group="system",
        timeout=300,
    )
)
