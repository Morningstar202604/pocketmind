"""文件工具：列目录 / 读文件 / 写文件 / 删除。

路径白名单：默认只允许访问 $HOME、~/storage（Termux 存储）与 /sdcard，
防止 agent 越权读写系统敏感目录。所有路径先 resolve 再校验。
"""
from __future__ import annotations

from pathlib import Path

from . import Tool, register

MAX_TEXT = 100_000


def _roots() -> list[Path]:
    home = Path.home()
    roots = [home]
    for p in (home / "storage", Path("/sdcard")):
        if p.exists():
            roots.append(p)
    return roots


def _resolve(raw: str) -> Path:
    p = Path(raw).expanduser()
    if not p.is_absolute():
        p = Path.home() / p
    p = p.resolve()
    for r in _roots():
        rp = r.resolve()
        if p == rp or rp in p.parents:
            return p
    raise ValueError(f"路径不在允许范围内（仅限主目录与存储目录）：{p}")


async def list_dir(path: str = ".") -> dict:
    try:
        p = _resolve(path)
    except ValueError as e:
        return {"error": str(e)}
    if not p.is_dir():
        return {"error": f"不是目录：{p}"}
    try:
        entries = []
        for e in sorted(p.iterdir(), key=lambda x: (x.is_file(), x.name.lower())):
            try:
                st = e.stat()
                entries.append(
                    {
                        "name": e.name,
                        "type": "dir" if e.is_dir() else "file",
                        "size": st.st_size if e.is_file() else None,
                    }
                )
            except OSError:
                entries.append({"name": e.name, "type": "unknown", "size": None})
        return {"path": str(p), "entries": entries[:500]}
    except OSError as e:
        return {"error": f"读取目录失败：{e}"}


async def read_file(path: str) -> dict:
    try:
        p = _resolve(path)
    except ValueError as e:
        return {"error": str(e)}
    if not p.is_file():
        return {"error": f"不是文件：{p}"}
    try:
        data = p.read_bytes()
        if len(data) > MAX_TEXT:
            return {"error": f"文件过大（>{MAX_TEXT} 字节），请改用 shell 查看"}
        text = data.decode("utf-8", errors="replace")
        return {"path": str(p), "content": text[:MAX_TEXT]}
    except OSError as e:
        return {"error": f"读取失败：{e}"}


async def write_file(path: str, content: str) -> dict:
    try:
        p = _resolve(path)
    except ValueError as e:
        return {"error": str(e)}
    data = str(content or "")
    if len(data.encode("utf-8", errors="replace")) > 1_000_000:
        return {"error": "内容过大（>1MB），请分段写入"}
    try:
        # 覆盖已有文件前自动备份，支持前端一键撤销
        _ckpt = None
        if p.exists() and p.is_file():
            from ..checkpoints import backup_file

            bak = backup_file(p)
            if bak:
                _ckpt = {"path": str(p), "backup": bak, "kind": "overwrite"}
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(data, encoding="utf-8")
        out = {"ok": True, "path": str(p), "bytes": len(data)}
        if _ckpt:
            out["_ckpt"] = _ckpt
        return out
    except OSError as e:
        return {"error": f"写入失败：{e}"}


async def delete_file(path: str) -> dict:
    try:
        p = _resolve(path)
    except ValueError as e:
        return {"error": str(e)}
    if not p.exists():
        return {"error": f"不存在：{p}"}
    try:
        # 删除文件前自动备份，支持一键撤销
        _ckpt = None
        if p.is_file():
            from ..checkpoints import backup_file

            bak = backup_file(p)
            if bak:
                _ckpt = {"path": str(p), "backup": bak, "kind": "restore"}
        if p.is_dir():
            p.rmdir()
        else:
            p.unlink()
        out = {"ok": True, "deleted": str(p)}
        if _ckpt:
            out["_ckpt"] = _ckpt
        return out
    except OSError as e:
        return {"error": f"删除失败：{e}"}


register(Tool(
    name="list_dir",
    description="列出目录内容（名称、类型、大小）。默认当前目录，可用绝对路径。",
    parameters={
        "type": "object",
        "properties": {"path": {"type": "string", "description": "目录路径，默认当前目录"}},
    },
    risk="safe",
    handler=list_dir,
    summary="查看目录内容",
    group="files",
))

register(Tool(
    name="read_file",
    description="读取文本文件内容（UTF-8，最多 100KB）。",
    parameters={
        "type": "object",
        "properties": {"path": {"type": "string", "description": "文件绝对路径"}},
        "required": ["path"],
    },
    risk="safe",
    handler=read_file,
    summary="读取文件",
    group="files",
))

register(Tool(
    name="write_file",
    description="写入或覆盖一个文本文件（自动创建父目录）。",
    parameters={
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "文件绝对路径"},
            "content": {"type": "string", "description": "要写入的内容"},
        },
        "required": ["path", "content"],
    },
    risk="write",
    handler=write_file,
    summary="写入文件",
    group="files",
))

register(Tool(
    name="delete_file",
    description="删除文件（或空目录）。危险操作，删除前请确认路径。",
    parameters={
        "type": "object",
        "properties": {"path": {"type": "string", "description": "要删除的文件/空目录绝对路径"}},
        "required": ["path"],
    },
    risk="danger",
    handler=delete_file,
    summary="删除文件（不可恢复）",
    group="files",
))
