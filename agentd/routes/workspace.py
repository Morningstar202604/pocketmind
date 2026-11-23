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
import contextlib
import difflib
import os
import re
import shlex
import shutil
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query, WebSocket
from pydantic import BaseModel, Field
from sse_starlette.sse import EventSourceResponse

from ..paths import workspace_dir
from ..tools.files import _resolve
from ..tools.shell import run_shell
from ..workspace_changes import get_store
from ..workspace_git import GitOperationError, GitUnavailable, open_repo, read_merge_checkpoint
from ..workspace_git import apply_patch as git_apply_patch
from ..workspace_git import checkout as git_checkout
from ..workspace_git import commit as git_commit
from ..workspace_git import conflict_blocks as git_conflict_blocks
from ..workspace_git import conflicts as git_conflicts
from ..workspace_git import merge_abort as git_merge_abort
from ..workspace_git import merge_commit as git_merge_commit
from ..workspace_git import merge_preview as git_merge_preview
from ..workspace_git import resolve_conflict as git_resolve_conflict
from ..workspace_git import resolve_conflict_blocks as git_resolve_conflict_blocks
from ..workspace_git import status as git_status
from ..workspace_git import undo_merge as git_undo_merge
from ..workspace_locks import WorkspaceLockBusy, hold
from ..workspace_tasks import get_manager

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


class TaskBody(BaseModel):
    command: str = Field(min_length=1, max_length=10_000)
    cwd: str = "."
    timeout: int = Field(default=300, ge=1, le=300)


class PathBody(BaseModel):
    path: str = Field(min_length=1, max_length=4096)


class RenameBody(BaseModel):
    from_path: str = Field(min_length=1, max_length=4096)
    to_path: str = Field(min_length=1, max_length=4096)


class GitCheckoutBody(BaseModel):
    cwd: str = "."
    branch: str = Field(min_length=1, max_length=200)
    create: bool = False


class GitCommitBody(BaseModel):
    cwd: str = "."
    message: str = Field(default="", max_length=500)
    all: bool = True


class GitMergeBody(BaseModel):
    cwd: str = "."
    branch: str = Field(min_length=1, max_length=200)


class GitMergeCommitBody(BaseModel):
    cwd: str = "."
    message: str = Field(default="", max_length=500)


class GitResolveBody(BaseModel):
    cwd: str = "."
    path: str = Field(min_length=1, max_length=4096)
    strategy: str = "ours"
    content: str | None = Field(default=None, max_length=MAX_WRITE)


class ConflictChoice(BaseModel):
    index: int = Field(ge=0)
    side: str = "ours"
    content: str | None = Field(default=None, max_length=MAX_WRITE)


class GitResolveBlocksBody(BaseModel):
    cwd: str = "."
    path: str = Field(min_length=1, max_length=4096)
    choices: list[ConflictChoice] = Field(min_length=1, max_length=500)


class GitPatchBody(BaseModel):
    cwd: str = "."
    patch: str = Field(min_length=1, max_length=MAX_DIFF_CHARS)
    dry_run: bool = True


def _git_http_error(exc: Exception) -> HTTPException:
    if isinstance(exc, GitUnavailable):
        return HTTPException(503, str(exc))
    if isinstance(exc, GitOperationError):
        detail = str(exc)
        if exc.detail:
            detail = f"{detail}：{exc.detail[:300]}"
        return HTTPException(exc.status, detail)
    return HTTPException(400, f"Git 操作失败：{exc}")


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


def _write_text(p: Path, content: str, expected_mtime: float | None = None) -> dict:
    data = str(content or "")
    try:
        encoded = data.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise HTTPException(400, f"内容不是合法 UTF-8：{exc}") from None
    if len(encoded) > MAX_WRITE:
        raise HTTPException(413, f"内容过大（{len(encoded)} 字节，上限 {MAX_WRITE}）")
    existed = p.exists() and p.is_file()
    if expected_mtime is not None and existed:
        try:
            current = p.stat().st_mtime
        except OSError as exc:
            raise HTTPException(400, f"读取失败：{exc}") from None
        if abs(current - expected_mtime) > 0.001:
            raise HTTPException(409, "文件已被其他程序修改，请刷新后再保存")
    backup = None
    if existed:
        # 覆盖前先备份原文件；这里不走 files.py 的 _write_file_sync，因为它会
        # 再用 $HOME 白名单校验一次，AGENT_WORKSPACE 指到 /sdcard 之类的目录时
        # 会被误拒。
        from ..checkpoints import backup_file

        backup = backup_file(p)
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(data, encoding="utf-8")
        mtime = p.stat().st_mtime
    except OSError as exc:
        raise HTTPException(400, f"写入失败：{exc}") from None
    change_id = None
    try:
        change = get_store().record(
            path=str(p),
            kind="overwrite" if existed else "create",
            backup=backup,
            after_mtime=mtime,
            after_size=len(encoded),
            bytes_changed=len(encoded),
        )
        change_id = change["id"]
    except Exception:  # noqa: BLE001 —— 变更记录失败不阻塞保存
        change_id = None
    return {
        "path": str(p), "bytes": len(encoded), "mtime": mtime,
        "backup": backup, "change_id": change_id,
    }


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
            "changes": True,
            "tasks": True,
            "locks": True,
            "git_merge": shutil.which("git") is not None,
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
    try:
        async with hold(p, "human:save"):
            out = await asyncio.to_thread(_write_text, p, body.content, body.expected_mtime)
    except WorkspaceLockBusy as exc:
        raise HTTPException(409, str(exc)) from None
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
    try:
        async with hold(p, "human:mkdir"):
            if p.exists():
                raise HTTPException(409, f"已存在：{p}")
            p.mkdir(parents=True, exist_ok=False)
    except WorkspaceLockBusy as exc:
        raise HTTPException(409, str(exc)) from None
    except OSError as exc:
        raise HTTPException(400, f"创建失败：{exc}") from None
    return {"ok": True, "path": str(p)}


@router.post("/rename")
async def rename(body: RenameBody) -> dict:
    src = _resolve_writable(body.from_path)
    dst = _resolve_writable(body.to_path)
    try:
        async with contextlib.AsyncExitStack() as stack:
            # 固定按路径排序加锁，避免两个反向重命名互相等待。
            for lock_path in sorted({str(src), str(dst)}):
                await stack.enter_async_context(hold(lock_path, "human:rename"))
            if not src.exists():
                raise HTTPException(404, f"不存在：{src}")
            if dst.exists():
                raise HTTPException(409, f"目标已存在：{dst}")
            src.rename(dst)
    except WorkspaceLockBusy as exc:
        raise HTTPException(409, str(exc)) from None
    except OSError as exc:
        raise HTTPException(400, f"重命名失败：{exc}") from None
    return {"ok": True, "from": str(src), "to": str(dst)}


@router.delete("/file")
async def delete_file(path: str = Query(..., min_length=1)) -> dict:
    p = _resolve_writable(path)
    if p == _root():
        raise HTTPException(400, "不能删除工作区根目录")
    try:
        async with hold(p, "human:delete"):
            if not p.exists():
                raise HTTPException(404, f"不存在：{p}")
            if p.is_dir():
                p.rmdir()  # 只允许删空目录，避免 Web UI 一键删掉整棵源码树
                return {"ok": True, "deleted": str(p), "type": "dir"}
            from ..checkpoints import backup_file

            bak = backup_file(p)
            p.unlink()
            change_id = None
            try:
                change = get_store().record(path=str(p), kind="delete", backup=bak)
                change_id = change["id"]
            except Exception:  # noqa: BLE001 —— 变更记录失败不阻塞删除
                change_id = None
            return {"ok": True, "deleted": str(p), "type": "file", "backup": bak, "change_id": change_id}
    except WorkspaceLockBusy as exc:
        raise HTTPException(409, str(exc)) from None
    except OSError as exc:
        raise HTTPException(400, f"删除失败：{exc}") from None


@router.post("/tasks")
async def start_workspace_task(body: TaskBody) -> dict:
    """把长命令放到后台跑；HTTP 立即返回 task_id，前端轮询状态/取消。"""
    cwd = _resolve_any(body.cwd)
    if not cwd.is_dir():
        raise HTTPException(400, f"工作目录不存在：{cwd}")
    _deny_command(body.command)
    task = await get_manager().start(command=body.command, cwd=str(cwd), timeout_s=body.timeout)
    return {"ok": True, **task}


@router.get("/tasks")
async def list_workspace_tasks(limit: int = Query(20, ge=1, le=50)) -> dict:
    return {"items": get_manager().list(limit)}


@router.get("/tasks/{task_id}")
async def get_workspace_task(task_id: str) -> dict:
    task = get_manager().get(task_id)
    if not task:
        raise HTTPException(404, "任务不存在")
    return task


@router.get("/tasks/{task_id}/stream")
async def stream_workspace_task(task_id: str):
    """任务增量 SSE；EventSource 不能带 Authorization，前端仍用轮询，此端点给脚本/调试。"""
    manager = get_manager()
    if manager.get(task_id) is None:
        raise HTTPException(404, "任务不存在")
    return EventSourceResponse(manager.stream(task_id), ping=15)


@router.post("/tasks/{task_id}/cancel")
async def cancel_workspace_task(task_id: str) -> dict:
    result = get_manager().cancel(task_id)
    if "error" in result:
        msg = str(result["error"])
        raise HTTPException(404 if "不存在" in msg else 409, msg)
    return result


@router.get("/changes")
async def list_changes(limit: int = Query(50, ge=1, le=200), include_undone: bool = Query(False)) -> dict:
    """最近的工作区写/删/新建记录；默认只显示未撤销。"""
    items = await asyncio.to_thread(get_store().list, limit, include_undone)
    return {"items": items}


@router.post("/changes/{change_id}/undo")
async def undo_change(change_id: str) -> dict:
    item = get_store().get(change_id)
    if not item:
        raise HTTPException(404, "变更记录不存在")
    try:
        async with hold(str(item.get("path") or ""), "human:undo"):
            result = await asyncio.to_thread(get_store().undo, change_id)
    except WorkspaceLockBusy as exc:
        raise HTTPException(409, str(exc)) from None
    if "error" in result:
        msg = str(result["error"])
        if "不存在" in msg:
            raise HTTPException(404, msg)
        if "已拒绝" in msg or "已被修改" in msg:
            raise HTTPException(409, msg)
        raise HTTPException(400, msg)
    return result


@router.get("/git")
async def git_raw_status(cwd: str = Query(".")) -> dict:
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




@router.get("/git/status")
async def get_git_status(cwd: str = Query(".")) -> dict:
    """结构化 Git 状态：分支、脏文件、ahead/behind、合并状态。"""
    try:
        return await asyncio.to_thread(git_status, cwd)
    except Exception as exc:  # noqa: BLE001
        raise _git_http_error(exc) from None


@router.post("/git/checkout")
async def checkout_branch(body: GitCheckoutBody) -> dict:
    try:
        return await asyncio.to_thread(git_checkout, body.cwd, body.branch, body.create)
    except Exception as exc:  # noqa: BLE001
        raise _git_http_error(exc) from None


@router.post("/git/commit")
async def commit_git_changes(body: GitCommitBody) -> dict:
    """提交工作区改动；all=true 时把当前目录全部改动加入暂存区（受 .gitignore 保护）。"""
    try:
        return await asyncio.to_thread(
            git_commit, body.cwd, body.message, "." if body.all else ""
        )
    except Exception as exc:  # noqa: BLE001
        raise _git_http_error(exc) from None


@router.post("/git/merge/preview")
async def preview_merge(body: GitMergeBody) -> dict:
    """进入合并状态（--no-commit）；冲突文件由 /git/conflicts 读取。"""
    try:
        return await asyncio.to_thread(git_merge_preview, body.cwd, body.branch)
    except Exception as exc:  # noqa: BLE001
        raise _git_http_error(exc) from None


@router.post("/git/merge/abort")
async def abort_merge(body: GitMergeCommitBody) -> dict:
    try:
        return await asyncio.to_thread(git_merge_abort, body.cwd)
    except Exception as exc:  # noqa: BLE001
        raise _git_http_error(exc) from None


@router.post("/git/merge/commit")
async def commit_merge(body: GitMergeCommitBody) -> dict:
    try:
        return await asyncio.to_thread(git_merge_commit, body.cwd, body.message)
    except Exception as exc:  # noqa: BLE001
        raise _git_http_error(exc) from None


@router.get("/git/merge/checkpoint")
async def get_merge_checkpoint(cwd: str = Query(".")) -> dict:
    try:
        repo = await asyncio.to_thread(open_repo, cwd)
        cp = await asyncio.to_thread(read_merge_checkpoint, repo)
        return {"ok": True, "checkpoint": cp}
    except Exception as exc:  # noqa: BLE001
        raise _git_http_error(exc) from None


@router.post("/git/merge/undo")
async def undo_merge(body: GitMergeCommitBody) -> dict:
    try:
        return await asyncio.to_thread(git_undo_merge, body.cwd)
    except Exception as exc:  # noqa: BLE001
        raise _git_http_error(exc) from None


@router.get("/git/conflicts")
async def get_git_conflicts(cwd: str = Query(".")) -> dict:
    try:
        return await asyncio.to_thread(git_conflicts, cwd)
    except Exception as exc:  # noqa: BLE001
        raise _git_http_error(exc) from None


@router.get("/git/conflicts/blocks")
async def get_git_conflict_blocks(cwd: str = Query("."), path: str = Query(..., min_length=1)) -> dict:
    """三路冲突块：ours/base/theirs，供前端逐块选择。"""
    try:
        return await asyncio.to_thread(git_conflict_blocks, cwd, path)
    except Exception as exc:  # noqa: BLE001
        raise _git_http_error(exc) from None


@router.post("/git/conflicts/blocks/resolve")
async def resolve_git_conflict_blocks(body: GitResolveBlocksBody) -> dict:
    try:
        repo = await asyncio.to_thread(open_repo, body.cwd)
        target = Path(repo) / body.path
        async with hold(target, "human:conflict-blocks"):
            return await asyncio.to_thread(
                git_resolve_conflict_blocks,
                body.cwd,
                body.path,
                [c.model_dump() for c in body.choices],
            )
    except WorkspaceLockBusy as exc:
        raise HTTPException(409, str(exc)) from None
    except Exception as exc:  # noqa: BLE001
        raise _git_http_error(exc) from None


@router.post("/git/conflicts/resolve")
async def resolve_git_conflict(body: GitResolveBody) -> dict:
    try:
        repo = await asyncio.to_thread(open_repo, body.cwd)
        target = Path(repo) / body.path
        async with hold(target, "human:conflict"):
            return await asyncio.to_thread(
                git_resolve_conflict, body.cwd, body.path, body.strategy, body.content
            )
    except WorkspaceLockBusy as exc:
        raise HTTPException(409, str(exc)) from None
    except Exception as exc:  # noqa: BLE001
        raise _git_http_error(exc) from None


@router.post("/patch")
async def apply_workspace_patch(body: GitPatchBody) -> dict:
    """应用 unified diff；默认只预检（dry_run=true），确认后再落盘。"""
    try:
        return await asyncio.to_thread(git_apply_patch, body.cwd, body.patch, body.dry_run)
    except Exception as exc:  # noqa: BLE001
        raise _git_http_error(exc) from None



# ---------------------------------------------------------------------------
# PTY 终端：会话池在 agentd/workspace_terms.py；这里只做路由兼容包装。
# ---------------------------------------------------------------------------


async def serve_terminal(websocket: WebSocket, cwd: str) -> None:
    from ..workspace_terms import get_hub

    try:
        root = _resolve_any(cwd)
    except HTTPException as exc:
        await websocket.close(code=1008, reason=str(exc.detail)[:120])
        return
    if not root.is_dir():
        await websocket.close(code=1008, reason=f"工作目录不存在：{root}")
        return
    await get_hub().serve(
        websocket, str(root), websocket.query_params.get("session") or None
    )
