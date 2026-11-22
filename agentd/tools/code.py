"""代码工具：精确编辑、检索、Glob、Git diff、测试运行。

这些工具是「Agent 能改代码」和「只会聊天」的分界线。所有写操作都先备份，
并把备份凭证放在 `_ckpt` 里交给 Agent 的 checkpoint 链路，前端可一键撤销。

路径规则沿用 `files.py`：只允许 `$HOME`、`~/storage`、`/sdcard`，拒绝
`.agent`、`.ssh`、`.gnupg`。工作区之外的项目请用 `AGENT_WORKSPACE` 指到
主目录内，或由用户在命令台显式操作。
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

from . import Tool, register
from .files import _denied_dirs, _resolve
from .shell import run_shell

MAX_EDIT_DIFF = 20_000
MAX_GREP_RESULTS = 200
MAX_GLOB_RESULTS = 200
MAX_SCAN_BYTES = 1_000_000

SKIP_DIRS = {
    ".git", ".hg", ".svn", "node_modules", "dist", "build", ".venv", "venv",
    "__pycache__", ".mypy_cache", ".pytest_cache", ".ruff_cache", ".gradle",
    ".idea", ".next", ".turbo",
}


def _is_unsafe_path(p: Path) -> bool:
    """符号链接可能指向 $HOME 之外；grep/glob 不能只看名字，必须 resolve 后复查。"""
    try:
        _resolve(str(p))
    except (ValueError, OSError):
        return True
    return False


def _read_text(p: Path) -> str:
    if p.stat().st_size > MAX_SCAN_BYTES:
        raise ValueError(f"文件过大（>{MAX_SCAN_BYTES} 字节）：{p}")
    data = p.read_bytes()
    if b"\x00" in data[:1024]:
        raise ValueError(f"疑似二进制文件：{p}")
    return data.decode("utf-8", errors="replace")


def _unified(old: str, new: str, path: Path) -> str:
    return "".join(
        difflib.unified_diff(
            old.splitlines(keepends=True),
            new.splitlines(keepends=True),
            fromfile=f"a/{path.name}",
            tofile=f"b/{path.name}",
            lineterm="\n",
        )
    )


def _edit_file_sync(
    path: str,
    old_string: str,
    new_string: str,
    replace_all: bool = False,
    expected_mtime: float | None = None,
) -> dict:
    try:
        p = _resolve(path)
    except ValueError as exc:
        return {"error": str(exc)}
    if not p.is_file():
        return {"error": f"不是文件：{p}"}
    if not old_string:
        return {"error": "old_string 不能为空；插入内容请用 write_file，或在唯一锚点后替换"}
    try:
        if expected_mtime is not None:
            current = p.stat().st_mtime
            if abs(current - float(expected_mtime)) > 0.001:
                return {"error": "文件已被其他程序修改，请先重新读取再编辑"}
        old = _read_text(p)
    except (OSError, ValueError) as exc:
        return {"error": str(exc)}

    count = old.count(old_string)
    if count == 0:
        return {"error": "没有找到 old_string；请确认缩进、换行和原文完全一致"}
    if count > 1 and not replace_all:
        return {
            "error": f"old_string 出现 {count} 次，无法确定改哪一处；请加更多上下文，或传 replace_all=true",
            "occurrences": count,
        }

    new = old.replace(old_string, new_string) if replace_all else old.replace(old_string, new_string, 1)
    if len(new.encode("utf-8", errors="replace")) > 1_000_000:
        return {"error": "修改后内容过大（>1MB），请拆成多次编辑"}
    diff = _unified(old, new, p)
    try:
        from ..checkpoints import backup_file

        backup = backup_file(p)
        p.write_text(new, encoding="utf-8")
    except OSError as exc:
        return {"error": f"写入失败：{exc}"}
    out = {
        "ok": True,
        "path": str(p),
        "replacements": count if replace_all else 1,
        "bytes": len(new.encode("utf-8", errors="replace")),
        "mtime": p.stat().st_mtime,
        "diff": diff[:MAX_EDIT_DIFF],
        "truncated": len(diff) > MAX_EDIT_DIFF,
    }
    if backup:
        out["_ckpt"] = {"path": str(p), "backup": backup, "kind": "overwrite"}
    return out


async def edit_file(
    path: str,
    old_string: str,
    new_string: str,
    replace_all: bool = False,
    expected_mtime: float | None = None,
) -> dict:
    return await asyncio.to_thread(
        _edit_file_sync, path, old_string, new_string, replace_all, expected_mtime
    )


def _grep_sync(
    pattern: str,
    path: str = ".",
    case_sensitive: bool = False,
    max_results: int = MAX_GREP_RESULTS,
) -> dict:
    try:
        root = _resolve(path)
        regex = re.compile(pattern, 0 if case_sensitive else re.IGNORECASE)
    except (ValueError, re.error) as exc:
        return {"error": f"参数不合法：{exc}"}
    files: list[Path] = []
    if root.is_file():
        files = [root]
    elif root.is_dir():
        denied = {str(d) for d in _denied_dirs()}
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [
                d for d in dirnames
                if d not in SKIP_DIRS and str((Path(dirpath) / d).resolve()) not in denied
            ]
            files.extend(Path(dirpath) / name for name in filenames)
    else:
        return {"error": f"不是文件或目录：{root}"}

    results: list[dict] = []
    truncated = False
    for p in files:
        if _is_unsafe_path(p):
            continue
        try:
            if p.stat().st_size > MAX_SCAN_BYTES:
                continue
            text = p.read_bytes()
            if b"\x00" in text[:1024]:
                continue
            decoded = text.decode("utf-8", errors="replace")
        except OSError:
            continue
        for lineno, line in enumerate(decoded.splitlines(), start=1):
            if regex.search(line):
                results.append({"path": str(p), "line": lineno, "text": line[:300]})
                if len(results) >= max_results:
                    truncated = True
                    break
        if truncated:
            break
    return {"pattern": pattern, "root": str(root), "matches": results, "truncated": truncated}


async def grep(
    pattern: str,
    path: str = ".",
    case_sensitive: bool = False,
    max_results: int = MAX_GREP_RESULTS,
) -> dict:
    max_results = max(1, min(int(max_results or MAX_GREP_RESULTS), MAX_GREP_RESULTS))
    return await asyncio.to_thread(_grep_sync, pattern, path, case_sensitive, max_results)


def _glob_sync(pattern: str, path: str = ".", max_results: int = MAX_GLOB_RESULTS) -> dict:
    try:
        root = _resolve(path)
    except ValueError as exc:
        return {"error": str(exc)}
    if not root.is_dir():
        return {"error": f"不是目录：{root}"}
    if not pattern or pattern.startswith("/") or ".." in Path(pattern).parts:
        return {"error": "pattern 必须是非空相对路径，且不能包含 .."}
    out: list[dict] = []
    truncated = False
    try:
        for p in root.glob(pattern):
            if _is_unsafe_path(p):
                continue
            if any(part in SKIP_DIRS for part in p.relative_to(root).parts):
                continue
            try:
                st = p.stat()
            except OSError:
                continue
            out.append({
                "path": str(p),
                "type": "dir" if p.is_dir() else "file",
                "size": st.st_size if p.is_file() else None,
            })
            if len(out) >= max_results:
                truncated = True
                break
    except (OSError, ValueError) as exc:
        return {"error": f"glob 失败：{exc}"}
    return {"pattern": pattern, "root": str(root), "matches": out, "truncated": truncated}


async def glob_files(pattern: str, path: str = ".", max_results: int = MAX_GLOB_RESULTS) -> dict:
    max_results = max(1, min(int(max_results or MAX_GLOB_RESULTS), MAX_GLOB_RESULTS))
    return await asyncio.to_thread(_glob_sync, pattern, path, max_results)


def _git_diff_sync(root: Path, staged: bool, path: str, timeout: int) -> dict:
    def run(*args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["git", "-C", str(root), *args],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )

    try:
        branch = run("rev-parse", "--abbrev-ref", "HEAD")
        status = run("status", "--porcelain=v1")
        diff_args = ["diff", "--no-color"]
        if staged:
            diff_args.append("--cached")
        if path:
            diff_args.extend(["--", path])
        diff = run(*diff_args)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"cwd": str(root), "staged": staged, "ok": False, "error": f"git 执行失败：{exc}"}
    stdout = "\n".join(x for x in (branch.stdout.strip(), status.stdout.strip(), diff.stdout.rstrip()) if x)
    stderr = "\n".join(x for x in (branch.stderr.strip(), status.stderr.strip(), diff.stderr.strip()) if x)
    out = {
        "cwd": str(root),
        "staged": staged,
        "ok": diff.returncode == 0,
        "exit_code": diff.returncode,
        "stdout": stdout[:30_000],
        "stderr": stderr[:10_000],
    }
    if diff.returncode != 0:
        out["error"] = f"git diff 失败（exit {diff.returncode}）：{stderr[:200]}"
    return out


async def git_diff(
    cwd: str = ".",
    staged: bool = False,
    path: str = "",
    timeout: int = 30,
) -> dict:
    try:
        root = _resolve(cwd)
    except ValueError as exc:
        return {"error": str(exc)}
    if not root.is_dir():
        return {"error": f"不是目录：{root}"}
    if not shutil.which("git"):
        return {"error": "当前环境没有 git"}
    return await asyncio.to_thread(_git_diff_sync, root, staged, path, max(1, min(int(timeout or 30), 120)))


def _detect_test_command(root: Path) -> tuple[str, str] | None:
    if (root / "package.json").is_file():
        try:
            pkg = json.loads((root / "package.json").read_text(encoding="utf-8"))
            if "test" in (pkg.get("scripts") or {}):
                return "npm", "npm test"
        except (OSError, json.JSONDecodeError):
            pass
    if (root / "gradlew").is_file():
        return "gradle", "./gradlew test"
    if (root / "go.mod").is_file():
        return "go", "go test ./..."
    if (root / "Cargo.toml").is_file():
        return "cargo", "cargo test"
    pytest_markers = ("pyproject.toml", "pytest.ini", "tox.ini", "setup.cfg")
    if any((root / name).is_file() for name in pytest_markers) or (root / "tests").is_dir():
        return "pytest", "python3 -m pytest -q"
    if any(root.glob("test_*.py")) or any(root.glob("*_test.py")):
        return "pytest", "python3 -m pytest -q"
    return None


async def run_tests(
    cwd: str = ".",
    command: str = "",
    timeout: int = 300,
) -> dict:
    try:
        root = _resolve(cwd)
    except ValueError as exc:
        return {"error": str(exc)}
    if not root.is_dir():
        return {"error": f"不是目录：{root}"}
    cmd = command.strip()
    framework = "custom" if cmd else ""
    if not cmd:
        detected = _detect_test_command(root)
        if not detected:
            return {"error": "没有识别到测试框架；请传 command，例如 python3 -m pytest -q 或 npm test"}
        framework, cmd = detected
    full = f"cd {shlex.quote(str(root))} && {cmd}"
    result = await run_shell(full, max(1, min(int(timeout or 300), 300)))
    return {"cwd": str(root), "framework": framework, "command": cmd, **result}


register(Tool(
    name="edit_file",
    description=(
        "精确编辑文本文件：把 old_string 替换为 new_string。old_string 必须与文件内容完全一致；"
        "默认只允许唯一匹配，多处匹配时请加更多上下文或传 replace_all=true。"
        "修改前自动备份，支持撤销。"
    ),
    parameters={
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "文件绝对路径"},
            "old_string": {"type": "string", "description": "要被替换的原文，必须唯一且非空"},
            "new_string": {"type": "string", "description": "替换后的内容，可为空（表示删除这段原文）"},
            "replace_all": {"type": "boolean", "description": "是否替换所有匹配；默认 false"},
            "expected_mtime": {"type": "number", "description": "可选：读取时的 mtime，用于并发冲突检测"},
        },
        "required": ["path", "old_string", "new_string"],
    },
    risk="write",
    handler=edit_file,
    summary="精确编辑文件（先备份，可撤销）",
    group="files",
    timeout=120,
))

register(Tool(
    name="grep",
    description="按正则表达式搜索文件内容，返回文件:行号；自动跳过 .git/node_modules/dist 等目录。",
    parameters={
        "type": "object",
        "properties": {
            "pattern": {"type": "string", "description": "正则表达式，例如 TODO|FIXME 或 def .*handler"},
            "path": {"type": "string", "description": "搜索根目录，默认当前目录"},
            "case_sensitive": {"type": "boolean", "description": "是否区分大小写，默认 false"},
            "max_results": {"type": "integer", "description": "最多返回条数，1-200，默认 200"},
        },
        "required": ["pattern"],
    },
    risk="safe",
    handler=grep,
    summary="全文搜索代码",
    group="files",
    timeout=120,
))

register(Tool(
    name="glob",
    description="按 glob 模式查找文件，例如 **/*.py、src/**/*.tsx；返回相对路径、类型和大小。",
    parameters={
        "type": "object",
        "properties": {
            "pattern": {"type": "string", "description": "相对 glob 模式，例如 **/*.py"},
            "path": {"type": "string", "description": "起始目录，默认当前目录"},
            "max_results": {"type": "integer", "description": "最多返回条数，1-200，默认 200"},
        },
        "required": ["pattern"],
    },
    risk="safe",
    handler=glob_files,
    summary="按模式查找文件",
    group="files",
    timeout=120,
))

register(Tool(
    name="git_diff",
    description="查看当前分支、工作区状态和 unified diff；可只看已暂存改动或指定路径。",
    parameters={
        "type": "object",
        "properties": {
            "cwd": {"type": "string", "description": "Git 仓库目录，默认当前目录"},
            "staged": {"type": "boolean", "description": "是否查看已暂存（--cached）的 diff，默认 false"},
            "path": {"type": "string", "description": "可选：只查看某个路径"},
            "timeout": {"type": "integer", "description": "超时秒数，默认 30，最大 120"},
        },
    },
    risk="safe",
    handler=git_diff,
    summary="查看 Git 状态与 diff",
    group="files",
    timeout=120,
))

register(Tool(
    name="run_tests",
    description=(
        "在项目目录运行测试。不传 command 时自动识别 pytest / npm test / gradle / go / cargo；"
        "测试会执行项目代码，属于危险操作。"
    ),
    parameters={
        "type": "object",
        "properties": {
            "cwd": {"type": "string", "description": "项目目录，默认当前目录"},
            "command": {"type": "string", "description": "可选：自定义测试命令；不传则自动识别"},
            "timeout": {"type": "integer", "description": "超时秒数，默认 300，最大 300"},
        },
    },
    risk="danger",
    handler=run_tests,
    summary="运行项目测试",
    group="files",
    timeout=300,
))
