"""审批卡里的文件 diff 预览。

Agent 的审批卡不能只给一句「写入文件」——用户要在点「允许」之前看见到底改了什么。
这里在工具执行前读当前文件并算 unified diff，只做展示，不参与执行；读不到就返回空，
不阻塞审批。
"""
from __future__ import annotations

import difflib
from pathlib import Path

MAX_PREVIEW = 8_000


def _read(path: Path) -> str:
    try:
        if not path.is_file() or path.stat().st_size > 1_000_000:
            return ""
        data = path.read_bytes()
        if b"\x00" in data[:1024]:
            return ""
        return data.decode("utf-8", errors="replace")
    except OSError:
        return ""


def _diff(old: str, new: str, path: Path) -> str:
    return "".join(
        difflib.unified_diff(
            old.splitlines(keepends=True),
            new.splitlines(keepends=True),
            fromfile=f"a/{path.name}" if old else "/dev/null",
            tofile=f"b/{path.name}",
            lineterm="\n",
        )
    )[:MAX_PREVIEW]


def preview_tool_call(name: str, args: dict) -> str:
    if name not in {"write_file", "edit_file", "delete_file"}:
        return ""
    try:
        from .tools.files import _resolve
        raw_path = str(args.get("path") or "")
        path = _resolve(raw_path) if raw_path else None
    except (ValueError, OSError):
        return ""
    if path is None:
        return ""
    if name == "delete_file":
        return f"删除 {path}\n执行前会备份，可在工具卡里撤销。" if path.exists() else ""
    if name == "write_file":
        old = _read(path)
        new = str(args.get("content") or "")
        if not old and not new:
            return ""
        return _diff(old, new, path)
    # edit_file：按参数模拟一次替换；匹配不唯一时只提示，不假装预览
    old_text = _read(path)
    old_string = str(args.get("old_string") or "")
    new_string = str(args.get("new_string") or "")
    if not old_string or old_string not in old_text:
        return ""
    count = old_text.count(old_string)
    if count > 1 and not bool(args.get("replace_all")):
        return f"{path.name} 中 old_string 匹配 {count} 处；请加更多上下文后再批准。"
    new_text = old_text.replace(old_string, new_string) if args.get("replace_all") else old_text.replace(old_string, new_string, 1)
    return _diff(old_text, new_text, path)
