"""开发工作区 API：文件树、读写、预览、搜索、命令执行。

这层只服务「人」在 Web UI 里的显式操作，不替代 Agent 的工具调用：
- Agent 改代码仍走 `write_file` / `run_shell`，由审批中心拦截；
- 人在工作区里保存文件/跑命令，是已登录用户的直接动作，因此用 token + 工作区
  目录 jail 保护，不再重复弹 Agent 审批卡（否则每敲一行命令都要点一次，没法用）。

路径规则：
- 读：允许 `$HOME`、`~/storage`、`/sdcard`，复用 files.py 的黑名单；
- 写/删/改名：只允许 `AGENT_WORKSPACE`（默认 `~/pocketmind-workspace`）内部，
  防止 Web UI 被 XSS 或被误操作后改到 `~/.ssh`、配置目录或系统文件。
"""

from __future__ import annotations

import asyncio
import difflib
import json
import os
import re
import shlex
import shutil
import subprocess
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field

from ..paths import workspace_dir
from ..tools.files import _resolve
from ..tools.shell import run_shell

router = APIRouter(tags=["workspace"])

MAX_READ = 1_000_000
MAX_WRITE = 2_000_000
MAX_TREE_ENTRIES = 500
MAX_SEARCH_RESULTS = 200
MAX_DIFF_CHARS = 30_000

# 这些目录在搜索/树里默认跳过：不是项目源码，扫了只会拖慢手机。
SKIP_DIRS = {
    ".git",
    ".hg",
    ".svn",
    "node_modules",
    "dist",
    "build",
    ".venv",
    "venv",
    "__pycache__",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".gradle",
}

# 命令台是给人用的，不是给模型绕过审批的后门。明显破坏性的命令先拦一道；
# 真正的细粒度权限仍由 Agent 的 approval / 参数级规则负责。
_DENY_COMMAND = [
    re.compile(r"\brm\s+(-[a-zA-Z]*[rf][a-zA-Z]*\s+)?/(?:\s|$|\*)"),
    re.compile(r"\bmkfs(?:\.\w+)?\b"),
    re.compile(r"\bdd\s+[^\n]*\bof=/dev/"),
    re.compile(r":\(\)\s*\{\s*:\|:&\s*\};:"),
    re.compile(r"\b(?:reboot|shutdown|poweroff|halt)\b"),
]


class WriteBody(BaseModel):
    path: str = Field(min_length=1, max_length=4096)
    content: str = Field(max_length=MAX_WRITE)
    expected_mtime: float | None = None


class PreviewBody(BaseModel):
    path: str = Field(min_length=1, max_length=4096)
    content: str = Field(max_length=MAX_WRITE)


class ExecBody(BaseModel):
    command: str = Field(min_length=1, max_length=10_000)
    cwd: str = "."
    timeout: int = Field(default=60, ge=1, le=300)


class PathBody(BaseModel):
    path: str = Field(min_length=1, max_length=4096)


class RenameBody(BaseModel):
    from_path: str = Field(min_length=1, max_length=4096)
    to_path: str = Field(min_length=1, max_length=4096)


def _root() -> Path:
    root = workspace_dir().expanduser()
    root.mkdir(parents=True, exist_ok=True)
    return root.resolve()


def _resolve_any(raw: str) -> Path:
    """解析读路径：先走 files.py 的白名单，再对 AGENT_WORKSPACE 做例外放行。"""
    raw = (raw or ".").strip() or "."
    p = Path(raw).expanduser()
    if not p.is_absolute():
        p = _root() / p
    try:
        return _resolve(str(p))
    except (ValueError, OSError) as exc:
        # AGENT_WORKSPACE 可能不在 $HOME 下（例如 /sdcard/projects），
        # files.py 的白名单不会认它；这里只对工作区根做例外，不放宽其他路径。
        root = _root()
        try:
            rp = p.resolve()
        except OSError:
            raise HTTPException(400, str(exc)) from None
        if rp == root or root in rp.parents:
            return rp
        raise HTTPException(400, str(exc)) from None


def _resolve_writable(raw: str) -> Path:
    p = _resolve_any(raw)
    root = _root()
    if p != root and root not in p.parents:
        raise HTTPException(
            403, "只能修改工作区目录内的文件；请把项目放到工作区，或用 AGENT_WORKSPACE 指定目录"
        )
    return p


def _read_text(p: Path) -> dict:
    if not p.exists():
        raise HTTPException(404, f"不存在：{p}")
    if not p.is_file():
        raise HTTPException(400, f"不是文件：{p}")
    try:
        size = p.stat().st_size
    except OSError as exc:
        raise HTTPException(400, f"读取失败：{exc}") from None
    if size > MAX_READ:
        raise HTTPException(413, f"文件过大（{size} 字节，上限 {MAX_READ}）")
    try:
        content = p.read_bytes().decode("utf-8", errors="replace")
        mtime = p.stat().st_mtime
    except OSError as exc:
        raise HTTPException(400, f"读取失败：{exc}") from None
    return {"path": str(p), "content": content, "size": size, "mtime": mtime}


def _write_text(p: Path, content: str) -> dict:
    data = str(content or "")
    try:
        encoded = data.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise HTTPException(400, f"内容不是合法 UTF-8：{exc}") from None
    if len(encoded) > MAX_WRITE:
        raise HTTPException(413, f"内容过大（{len(encoded)} 字节，上限 {MAX_WRITE}）")
    try:
        # 覆盖前先备份原文件；这里不走 files.py 的 _write_file_sync，因为它会
        # 再用 $HOME 白名单校验一次，AGENT_WORKSPACE 指到 /sdcard 之类的目录时
        # 会被误拒。
        backup = None
        if p.exists() and p.is_file():
            from ..checkpoints import backup_file

            backup = backup_file(p)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(data, encoding="utf-8")
        mtime = p.stat().st_mtime
    except OSError as exc:
        raise HTTPException(400, f"写入失败：{exc}") from None
    return {"path": str(p), "bytes": len(encoded), "mtime": mtime, "backup": backup}


def _tree(p: Path) -> dict:
    if not p.exists():
        raise HTTPException(404, f"不存在：{p}")
    if not p.is_dir():
        raise HTTPException(400, f"不是目录：{p}")
    entries: list[dict] = []
    try:
        for entry in sorted(p.iterdir(), key=lambda x: (x.is_file(), x.name.lower())):
            if entry.name in SKIP_DIRS:
                continue
            try:
                st = entry.stat()
                entries.append(
                    {
                        "name": entry.name,
                        "type": "dir" if entry.is_dir() else "file",
                        "size": st.st_size if entry.is_file() else None,
                    }
                )
            except OSError:
                entries.append({"name": entry.name, "type": "unknown", "size": None})
            if len(entries) >= MAX_TREE_ENTRIES:
                break
    except OSError as exc:
        raise HTTPException(400, f"读取目录失败：{exc}") from None
    return {"path": str(p), "entries": entries, "truncated": len(entries) >= MAX_TREE_ENTRIES}


def _search(root: Path, q: str, case_sensitive: bool) -> dict:
    needle = q if case_sensitive else q.lower()
    results: list[dict] = []
    truncated = False
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS and not d.startswith(".")]
        for name in filenames:
            p = Path(dirpath) / name
            try:
                if p.stat().st_size > MAX_READ:
                    continue
                text = p.read_bytes().decode("utf-8", errors="replace")
            except OSError:
                continue
            if "\x00" in text[:1024]:
                continue
            for lineno, line in enumerate(text.splitlines(), start=1):
                hay = line if case_sensitive else line.lower()
                if needle in hay:
                    results.append({"path": str(p), "line": lineno, "text": line[:400]})
                    if len(results) >= MAX_SEARCH_RESULTS:
                        truncated = True
                        break
            if truncated:
                break
        if truncated:
            break
    return {"query": q, "root": str(root), "results": results, "truncated": truncated}


def _deny_command(command: str) -> None:
    for pattern in _DENY_COMMAND:
        if pattern.search(command):
            raise HTTPException(
                400, "该命令看起来会破坏系统或工作区，已被拦截；请换一种更安全的写法"
            )


@router.get("/info")
async def get_info() -> dict:
    root = _root()
    home = Path.home().resolve()
    roots = [{"path": str(root), "label": "PocketMind 工作区"}]
    if home != root:
        roots.append({"path": str(home), "label": "主目录（只读为主）"})
    for p, label in ((home / "storage", "Termux 共享存储"), (Path("/sdcard"), "内部存储")):
        try:
            if p.exists():
                rp = p.resolve()
                if all(rp != Path(x["path"]) for x in roots):
                    roots.append({"path": str(rp), "label": label})
        except OSError:
            continue
    runtime = (
        "termux"
        if "com.termux" in (os.environ.get("PREFIX", "") + os.environ.get("HOME", ""))
        else "python"
    )
    return {
        "ok": True,
        "root": str(root),
        "roots": roots,
        "runtime": runtime,
        "features": {
            "tree": True,
            "read": True,
            "write": True,
            "preview": True,
            "search": True,
            "exec": True,
            "git": shutil.which("git") is not None,
        },
    }


@router.get("/tree")
async def get_tree(path: str = Query(".")) -> dict:
    p = _resolve_any(path)
    return await asyncio.to_thread(_tree, p)


@router.get("/file")
async def get_file(path: str = Query(..., min_length=1)) -> dict:
    p = _resolve_any(path)
    return await asyncio.to_thread(_read_text, p)


@router.post("/preview")
async def preview_file(body: PreviewBody) -> dict:
    p = _resolve_writable(body.path)
    old = ""
    if p.exists():
        if not p.is_file():
            raise HTTPException(400, f"不是文件：{p}")
        old = (await asyncio.to_thread(_read_text, p))["content"]
    diff = "".join(
        difflib.unified_diff(
            old.splitlines(keepends=True),
            body.content.splitlines(keepends=True),
            fromfile=f"a/{p.name}",
            tofile=f"b/{p.name}",
            lineterm="\n",
        )
    )
    truncated = len(diff) > MAX_DIFF_CHARS
    return {
        "path": str(p),
        "changed": old != body.content,
        "diff": diff[:MAX_DIFF_CHARS],
        "truncated": truncated,
    }


@router.put("/file")
async def put_file(body: WriteBody) -> dict:
    p = _resolve_writable(body.path)
    if body.expected_mtime is not None and p.exists():
        try:
            current = p.stat().st_mtime
        except OSError as exc:
            raise HTTPException(400, f"读取失败：{exc}") from None
        if abs(current - body.expected_mtime) > 0.001:
            raise HTTPException(409, "文件已被其他程序修改，请刷新后再保存")
    out = await asyncio.to_thread(_write_text, p, body.content)
    return {"ok": True, **out}


@router.get("/search")
async def search(
    q: str = Query(..., min_length=1, max_length=200),
    path: str = Query("."),
    case_sensitive: bool = Query(False),
) -> dict:
    root = _resolve_any(path)
    if not root.is_dir():
        raise HTTPException(400, f"不是目录：{root}")
    return await asyncio.to_thread(_search, root, q, case_sensitive)


@router.post("/exec")
async def exec_command(body: ExecBody) -> dict:
    cwd = _resolve_any(body.cwd)
    if not cwd.is_dir():
        raise HTTPException(400, f"工作目录不存在：{cwd}")
    _deny_command(body.command)
    # 用 cd + 原命令拼一条，复用 run_shell 的超时、进程组回收和输出截断；
    # shlex.quote 保证 cwd 里带空格/引号也不会破坏命令。
    full = f"cd {shlex.quote(str(cwd))} && {body.command}"
    result = await run_shell(full, body.timeout)
    return {"cwd": str(cwd), "command": body.command, **result}


@router.post("/mkdir")
async def make_dir(body: PathBody) -> dict:
    p = _resolve_writable(body.path)
    if p.exists():
        raise HTTPException(409, f"已存在：{p}")
    try:
        p.mkdir(parents=True, exist_ok=False)
    except OSError as exc:
        raise HTTPException(400, f"创建失败：{exc}") from None
    return {"ok": True, "path": str(p)}


@router.post("/rename")
async def rename(body: RenameBody) -> dict:
    src = _resolve_writable(body.from_path)
    dst = _resolve_writable(body.to_path)
    if not src.exists():
        raise HTTPException(404, f"不存在：{src}")
    if dst.exists():
        raise HTTPException(409, f"目标已存在：{dst}")
    try:
        src.rename(dst)
    except OSError as exc:
        raise HTTPException(400, f"重命名失败：{exc}") from None
    return {"ok": True, "from": str(src), "to": str(dst)}


@router.delete("/file")
async def delete_file(path: str = Query(..., min_length=1)) -> dict:
    p = _resolve_writable(path)
    if not p.exists():
        raise HTTPException(404, f"不存在：{p}")
    if p == _root():
        raise HTTPException(400, "不能删除工作区根目录")
    try:
        if p.is_dir():
            p.rmdir()  # 只允许删空目录，避免 Web UI 一键删掉整棵源码树
            return {"ok": True, "deleted": str(p), "type": "dir"}
        from ..checkpoints import backup_file

        bak = backup_file(p)
        p.unlink()
        return {"ok": True, "deleted": str(p), "type": "file", "backup": bak}
    except OSError as exc:
        raise HTTPException(400, f"删除失败：{exc}") from None


@router.get("/git")
async def git_status(cwd: str = Query(".")) -> dict:
    p = _resolve_any(cwd)
    if not p.is_dir():
        raise HTTPException(400, f"不是目录：{p}")
    if not shutil.which("git"):
        return {
            "available": False,
            "cwd": str(p),
            "message": "当前环境没有 git，可在 Termux 里 pkg install git",
        }
    result = await run_shell(
        f"cd {shlex.quote(str(p))} && "
        "git rev-parse --abbrev-ref HEAD 2>&1; "
        "git status --porcelain=v1 2>&1; "
        "git diff --stat 2>&1",
        30,
    )
    return {"available": True, "cwd": str(p), **result}


# ---------------------------------------------------------------------------
# PTY 终端：WebSocket + 真 shell
# ---------------------------------------------------------------------------


def _set_winsize(fd: int, cols: int, rows: int) -> None:
    """把终端尺寸同步给 PTY；cols/rows 由 xterm.js 的 FitAddon 上报。"""
    import fcntl
    import struct
    import termios

    cols = max(20, min(int(cols or 80), 500))
    rows = max(5, min(int(rows or 24), 200))
    fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))


def _kill_term(proc) -> None:
    import signal

    if proc.poll() is not None:
        return
    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
    except (ProcessLookupError, OSError):
        try:
            proc.kill()
        except ProcessLookupError:
            pass


def _spawn_shell(cwd: str):
    """同步启动 PTY shell；放到线程里调用，避免阻塞事件循环。"""
    import pty

    shell = shutil.which("bash") or "/bin/sh"
    env = os.environ.copy()
    env["TERM"] = "xterm-256color"
    env["PWD"] = cwd
    master, slave = pty.openpty()
    try:
        _set_winsize(master, 80, 24)
        proc = subprocess.Popen(
            [shell, "-i"],
            stdin=slave,
            stdout=slave,
            stderr=slave,
            cwd=cwd,
            env=env,
            start_new_session=True,
            close_fds=True,
        )
    except OSError:
        os.close(master)
        os.close(slave)
        raise
    os.close(slave)
    return master, proc


async def serve_terminal(websocket: WebSocket, cwd: str) -> None:
    """在指定工作目录开一个 PTY shell，把字节流双向转发给 xterm.js。

    仅 POSIX（Termux/Linux）可用；Android 原生 APK 走的是 Kotlin 引擎，不经过这里。
    """
    try:
        root = _resolve_any(cwd)
    except HTTPException as exc:
        await websocket.close(code=1008, reason=str(exc.detail)[:120])
        return
    if not root.is_dir():
        await websocket.close(code=1008, reason=f"工作目录不存在：{root}")
        return
    if os.name != "posix":
        await websocket.close(code=1008, reason="当前运行时没有 POSIX PTY；请用 Termux/Linux")
        return

    try:
        master, proc = await asyncio.to_thread(_spawn_shell, str(root))
    except OSError as exc:
        await websocket.close(code=1008, reason=f"启动 shell 失败：{exc}")
        return

    await websocket.accept()

    async def pump() -> None:
        loop = asyncio.get_running_loop()
        try:
            while True:
                data = await loop.run_in_executor(None, os.read, master, 4096)
                if not data:
                    break
                await websocket.send_text(data.decode("utf-8", errors="replace"))
        except (OSError, WebSocketDisconnect, RuntimeError):
            pass

    pump_task = asyncio.create_task(pump())
    try:
        while True:
            raw = await websocket.receive_text()
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                continue
            kind = msg.get("type")
            if kind == "input":
                data = msg.get("data")
                if isinstance(data, str) and data:
                    try:
                        os.write(master, data.encode("utf-8", errors="replace"))
                    except OSError:
                        break
            elif kind == "resize":
                try:
                    _set_winsize(master, int(msg.get("cols", 80)), int(msg.get("rows", 24)))
                except (OSError, ValueError, TypeError):
                    pass
    except WebSocketDisconnect:
        pass
    finally:
        pump_task.cancel()
        _kill_term(proc)
        try:
            os.close(master)
        except OSError:
            pass
        try:
            proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            proc.kill()
