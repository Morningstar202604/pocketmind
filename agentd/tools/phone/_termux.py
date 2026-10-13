"""termux-api 工具函数：通用 subprocess 封装与二进制校验。

所有 phone 子模块共享此文件的基础设施（_run, _bin, _unavailable 等）。
"""
from __future__ import annotations

import asyncio
import shutil
from urllib.parse import urlparse


async def _run(args: list[str], input_text: str | None = None, timeout: int = 20) -> tuple[int, str, str]:
    proc = await asyncio.create_subprocess_exec(
        *args,
        stdin=asyncio.subprocess.PIPE if input_text is not None else None,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        out, err = await asyncio.wait_for(
            proc.communicate(input_text.encode() if input_text is not None else None),
            timeout=timeout,
        )
    except TimeoutError:
        _kill_proc(proc)
        out, err = await proc.communicate()
        raise
    except asyncio.CancelledError:
        _kill_proc(proc)
        raise
    return proc.returncode or 0, out.decode("utf-8", errors="replace").strip(), err.decode("utf-8", errors="replace").strip()


def _kill_proc(proc: asyncio.subprocess.Process) -> None:
    if proc.returncode is not None:
        return
    try:
        proc.kill()
    except ProcessLookupError:
        pass


def _bin(name: str) -> str | None:
    return shutil.which(name)


def _unavailable(tool_name: str) -> dict:
    return {"ok": False, "error": f"termux-api 未安装（{tool_name}），请先执行：pkg install termux-api"}


def _ok(**kw) -> dict:
    return {"ok": True, **kw}


def _fail(msg: str) -> dict:
    return {"ok": False, "error": msg}


def _safe_http_url(url: str) -> str | None:
    """校验 URL 仅允许 http/https scheme，拒绝 file:// / javascript: / data: 等。"""
    if not isinstance(url, str) or not url.strip():
        return None
    u = urlparse(url.strip())
    if u.scheme not in ("http", "https") or not u.netloc:
        return None
    return url.strip()
