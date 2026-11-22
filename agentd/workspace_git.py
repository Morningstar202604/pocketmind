"""工作区 Git：状态、分支、预合并、冲突读取/解决、提交、patch。

这里不用 ``run_shell`` 拼命令：Git 参数来自 Web UI，拼 shell 会把分支名/路径
变成注入点。所有调用都走 ``subprocess.run(["git", "-C", repo, ...])``，参数
原样传递，且仓库根必须在 AGENT_WORKSPACE 内。

合并采用「先 ``git merge --no-commit --no-ff``，再让用户解决冲突/提交」的
两阶段流程。预览会真实进入 MERGING 状态，因此提供 abort；这比只读的
``merge-tree`` 更可靠，也让冲突文件直接落在工作区里供人查看。
"""
from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

from .paths import workspace_dir

MAX_DIFF = 200_000
MAX_FILE_PREVIEW = 40_000
MAX_CONFLICT_FILES = 80
MAX_BRANCHES = 100

_BRANCH_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,199}$")


class GitUnavailable(RuntimeError):
    pass


class GitOperationError(RuntimeError):
    def __init__(self, message: str, detail: str = "", status: int = 400):
        super().__init__(message)
        self.detail = detail
        self.status = status


def _git(repo: Path, args: list[str], *, input_text: str | None = None, timeout: int = 30) -> dict:
    cmd = ["git", "-C", str(repo), *args]
    env = dict(os.environ)
    env["GIT_TERMINAL_PROMPT"] = "0"
    env["GIT_PAGER"] = "cat"
    try:
        p = subprocess.run(
            cmd,
            input=input_text,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            env=env,
        )
    except FileNotFoundError:
        raise GitUnavailable("当前环境没有 git，可在 Termux 里 pkg install git") from None
    except subprocess.TimeoutExpired:
        return {"ok": False, "exit_code": None, "stdout": "", "stderr": "git 操作超时"}
    return {
        "ok": p.returncode == 0,
        "exit_code": p.returncode,
        # 不做 strip：冲突原文/补丁的前导空格必须保留；需要标量的调用方自己 strip。
        "stdout": p.stdout,
        "stderr": p.stderr,
    }


def _workspace_root() -> Path:
    root = workspace_dir().expanduser()
    root.mkdir(parents=True, exist_ok=True)
    return root.resolve()


def open_repo(cwd: str = ".") -> Path:
    """把 cwd 解析成仓库根；只允许操作 AGENT_WORKSPACE 内的仓库。"""
    raw = Path(cwd or ".").expanduser()
    if not raw.is_absolute():
        raw = _workspace_root() / raw
    try:
        base = raw.resolve()
    except OSError as exc:
        raise GitOperationError(f"目录不可访问：{exc}", status=400) from None
    if not base.is_dir():
        raise GitOperationError(f"不是目录：{base}", status=400)
    if not (base / ".git").exists():
        # 允许从仓库子目录进入，由 git 自己向上找。
        pass
    probe = _git(base, ["rev-parse", "--show-toplevel"])
    if not probe["ok"]:
        raise GitOperationError(
            "当前目录不在 Git 仓库内（或 git 不可用）",
            detail=probe.get("stderr", ""),
            status=400,
        )
    top = Path(probe["stdout"].strip()).resolve()
    root = _workspace_root()
    if top != root and root not in top.parents:
        raise GitOperationError("只能操作工作区内的 Git 仓库", status=403)
    return top


def _validate_branch(branch: str) -> str:
    branch = (branch or "").strip()
    if not branch or not _BRANCH_RE.match(branch) or ".." in branch or branch.endswith("/"):
        raise GitOperationError("分支名不合法")
    probe = _git(Path.cwd(), ["check-ref-format", "--branch", branch])
    if not probe["ok"]:
        raise GitOperationError("分支名不符合 Git 规则", detail=probe.get("stderr", ""), status=400)
    return branch


def _porcelain(repo: Path) -> list[dict]:
    r = _git(repo, ["-c", "core.quotePath=false", "status", "--porcelain=v1"])
    if not r["ok"]:
        raise GitOperationError("读取 git status 失败", detail=r.get("stderr", ""), status=400)
    files: list[dict] = []
    for line in r["stdout"].splitlines():
        if len(line) < 3:
            continue
        x, y = line[0], line[1]
        rest = line[3:]
        original = None
        if " -> " in rest:
            original, rest = rest.split(" -> ", 1)
        files.append({
            "path": rest,
            "original_path": original,
            "index": x,
            "worktree": y,
            "staged": x not in (" ", "?"),
            "untracked": x == "?",
            "unmerged": x == "U" or y == "U" or (x == "A" and y == "A") or (x == "D" and y == "D"),
        })
    return files


def _branch_list(repo: Path, current: str) -> list[dict]:
    fmt = "%(refname:short)%00%(objectname:short)%00%(upstream:short)"
    r = _git(repo, ["for-each-ref", "--sort=-committerdate", f"--format={fmt}", "refs/heads"])
    if not r["ok"]:
        return []
    out: list[dict] = []
    for line in r["stdout"].splitlines():
        parts = line.split("\x00")
        if len(parts) < 3:
            continue
        name, sha, upstream = parts[0], parts[1], parts[2]
        out.append({"name": name, "sha": sha, "upstream": upstream, "current": name == current})
        if len(out) >= MAX_BRANCHES:
            break
    return out


def _ahead_behind(repo: Path, upstream: str) -> tuple[int | None, int | None]:
    if not upstream:
        return None, None
    r = _git(repo, ["rev-list", "--left-right", "--count", f"HEAD...{upstream}"])
    if not r["ok"]:
        return None, None
    parts = r["stdout"].split()
    if len(parts) != 2:
        return None, None
    try:
        return int(parts[0]), int(parts[1])
    except ValueError:
        return None, None


def _unmerged_paths(repo: Path) -> list[str]:
    r = _git(repo, ["diff", "--name-only", "--diff-filter=U", "-z"])
    if not r["ok"]:
        return []
    return [p for p in r["stdout"].split("\x00") if p][:MAX_CONFLICT_FILES]


def _merging(repo: Path) -> bool:
    return _git(repo, ["rev-parse", "-q", "--verify", "MERGE_HEAD"])["ok"]


def status(cwd: str = ".") -> dict:
    try:
        repo = open_repo(cwd)
    except (GitUnavailable, GitOperationError) as exc:
        # 工作区页打开 Git 面板时，非仓库目录应是空状态而不是红色报错。
        return {
            "available": False,
            "cwd": str(Path(cwd or ".").expanduser()),
            "message": str(exc),
            "files": [],
            "branches": [],
            "conflicts": [],
            "clean": True,
            "merging": False,
        }
    files = _porcelain(repo)
    current = _git(repo, ["rev-parse", "--abbrev-ref", "HEAD"]).get("stdout", "").strip() or "HEAD"
    upstream = _git(repo, ["rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}"]).get("stdout", "").strip()
    ahead, behind = _ahead_behind(repo, upstream)
    merging = _merging(repo)
    commit = _git(repo, ["log", "-1", "--format=%h %s"]).get("stdout", "").strip()
    diffstat = _git(repo, ["diff", "--stat"]).get("stdout", "").strip()
    return {
        "available": True,
        "cwd": str(repo),
        "repo_root": str(repo),
        "branch": current,
        "detached": current == "HEAD",
        "commit": commit,
        "upstream": upstream,
        "ahead": ahead,
        "behind": behind,
        "clean": not files,
        "merging": merging,
        "merge_head": _git(repo, ["rev-parse", "--short", "MERGE_HEAD"]).get("stdout", "").strip() if merging else "",
        "files": files,
        "branches": _branch_list(repo, current),
        "conflicts": _unmerged_paths(repo) if merging else [],
        "diffstat": diffstat[:MAX_DIFF],
    }


def checkout(cwd: str, branch: str, create: bool = False) -> dict:
    repo = open_repo(cwd)
    branch = _validate_branch(branch)
    if _merging(repo):
        raise GitOperationError("正在合并中，先解决/中止合并再切换分支", status=409)
    if _porcelain(repo):
        raise GitOperationError("工作区有未提交改动，先提交或 stash 后再切换分支", status=409)
    current = _git(repo, ["rev-parse", "--abbrev-ref", "HEAD"]).get("stdout", "").strip()
    if branch == current and not create:
        return {"ok": True, "branch": branch, "changed": False}
    args = ["checkout", "-b", branch] if create else ["checkout", branch]
    r = _git(repo, args)
    if not r["ok"]:
        raise GitOperationError("切换分支失败", detail=r.get("stderr") or r.get("stdout", ""), status=409)
    return {"ok": True, "branch": branch, "changed": True, "output": r.get("stdout", "")}


def merge_preview(cwd: str, branch: str) -> dict:
    repo = open_repo(cwd)
    branch = _validate_branch(branch)
    current = _git(repo, ["rev-parse", "--abbrev-ref", "HEAD"]).get("stdout", "").strip()
    if branch == current:
        raise GitOperationError("不能把分支合并到它自己")
    if _merging(repo):
        raise GitOperationError("已有未完成的合并，先继续提交或中止", status=409)
    if _porcelain(repo):
        raise GitOperationError("工作区有未提交改动，先提交或 stash 后再合并", status=409)
    # 真实进入 MERGING 状态；冲突文件会留在工作区，UI 用 abort 退出。
    r = _git(repo, ["merge", "--no-commit", "--no-ff", "--no-edit", branch])
    state = status(str(repo))
    return {
        "ok": bool(r["ok"]),
        "merge_exit": r["exit_code"],
        "branch": branch,
        "clean": not state["conflicts"],
        "conflicts": state["conflicts"],
        "stdout": r.get("stdout", "")[:MAX_DIFF],
        "stderr": r.get("stderr", "")[:MAX_DIFF],
        "state": state,
    }


def merge_abort(cwd: str) -> dict:
    repo = open_repo(cwd)
    if not _merging(repo):
        raise GitOperationError("当前没有进行中的合并", status=409)
    r = _git(repo, ["merge", "--abort"])
    if not r["ok"]:
        raise GitOperationError("中止合并失败", detail=r.get("stderr", ""), status=409)
    return {"ok": True, "state": status(str(repo))}


def merge_commit(cwd: str, message: str = "") -> dict:
    repo = open_repo(cwd)
    if not _merging(repo):
        raise GitOperationError("当前没有进行中的合并", status=409)
    if _unmerged_paths(repo):
        raise GitOperationError("还有未解决的冲突文件", status=409)
    msg = (message or "").strip() or "Merge branch"
    if len(msg) > 500:
        raise GitOperationError("提交说明过长")
    r = _git(repo, ["commit", "-m", msg])
    if not r["ok"]:
        raise GitOperationError("提交合并失败", detail=r.get("stderr") or r.get("stdout", ""), status=409)
    return {"ok": True, "output": r.get("stdout", ""), "state": status(str(repo))}


def _safe_repo_path(repo: Path, rel: str) -> Path:
    rel = (rel or "").replace("\\", "/").lstrip("/")
    if not rel or ".." in Path(rel).parts:
        raise GitOperationError("文件路径不合法")
    p = (repo / rel).resolve()
    if p != repo and repo not in p.parents:
        raise GitOperationError("文件路径越出仓库")
    return p


def conflicts(cwd: str) -> dict:
    repo = open_repo(cwd)
    paths = _unmerged_paths(repo)
    items: list[dict] = []
    for rel in paths:
        _safe_repo_path(repo, rel)
        stages: dict[str, str] = {}
        binary = False
        for label, stage in (("base", 1), ("ours", 2), ("theirs", 3)):
            r = _git(repo, ["show", f":{stage}:{rel}"])
            if not r["ok"]:
                stages[label] = ""
                continue
            text = r["stdout"]
            if "\x00" in text[:2048]:
                binary = True
                stages[label] = ""
                continue
            stages[label] = text[:MAX_FILE_PREVIEW]
        work = repo / rel
        work_text = ""
        if work.is_file():
            try:
                raw = work.read_bytes()
                if b"\x00" not in raw[:2048]:
                    work_text = raw.decode("utf-8", errors="replace")[:MAX_FILE_PREVIEW]
                else:
                    binary = True
            except OSError:
                pass
        items.append({"path": rel, "binary": binary, "work": work_text, **stages})
    return {"items": items, "count": len(items)}


def resolve_conflict(cwd: str, path: str, strategy: str = "ours", content: str | None = None) -> dict:
    repo = open_repo(cwd)
    rel = (path or "").replace("\\", "/")
    _safe_repo_path(repo, rel)
    if rel not in _unmerged_paths(repo):
        raise GitOperationError("该文件当前不是冲突文件", status=409)
    strategy = (strategy or "ours").strip()
    if strategy not in {"ours", "theirs", "manual"}:
        raise GitOperationError("strategy 只能是 ours / theirs / manual")
    if strategy in {"ours", "theirs"}:
        r = _git(repo, ["checkout", f"--{strategy}", "--", rel])
        if not r["ok"]:
            raise GitOperationError("取冲突版本失败", detail=r.get("stderr", ""), status=409)
    else:
        target = _safe_repo_path(repo, rel)
        if target.is_symlink():
            raise GitOperationError("拒绝写入符号链接冲突文件", status=409)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content or "", encoding="utf-8")
    add = _git(repo, ["add", "--", rel])
    if not add["ok"]:
        raise GitOperationError("标记冲突已解决失败", detail=add.get("stderr", ""), status=409)
    return {"ok": True, "path": rel, "remaining": _unmerged_paths(repo), "state": status(str(repo))}


def apply_patch(cwd: str, patch: str, dry_run: bool = False) -> dict:
    repo = open_repo(cwd)
    if not patch or len(patch) > MAX_DIFF:
        raise GitOperationError(f"patch 为空或超过 {MAX_DIFF} 字符")
    if _merging(repo):
        raise GitOperationError("合并冲突未解决，不能应用 patch", status=409)
    check = _git(repo, ["apply", "--check", "-"], input_text=patch)
    if not check["ok"]:
        raise GitOperationError("patch 预检失败", detail=check.get("stderr") or check.get("stdout", ""), status=409)
    if dry_run:
        return {"ok": True, "dry_run": True, "check": check.get("stdout", "")}
    r = _git(repo, ["apply", "-"], input_text=patch)
    if not r["ok"]:
        raise GitOperationError("patch 应用失败", detail=r.get("stderr") or r.get("stdout", ""), status=409)
    return {"ok": True, "dry_run": False, "state": status(str(repo))}
