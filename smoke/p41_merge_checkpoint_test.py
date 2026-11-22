#!/usr/bin/env python3
"""P41 M1.6：合并前检查点 + 已提交合并可撤销。

真实 Git 仓库，不用 mock；验证 ref 落在合并前、提交后仍可 reset、abort 清理。
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

_HOME = tempfile.mkdtemp(prefix="p41-home-")
_WS = tempfile.mkdtemp(prefix="p41-ws-")
os.environ["AGENT_HOME"] = _HOME
os.environ["AGENT_WORKSPACE"] = _WS
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

passed, failed = [], []


def check(name: str, cond: bool, detail: str = ""):
    (passed if cond else failed).append(name)
    print(("  ✅ " if cond else "  ❌ ") + name + (f" — {detail}" if detail and not cond else ""))


def git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True)


def make_repo(repo: Path) -> None:
    repo.mkdir(parents=True, exist_ok=True)
    git(repo, "init", "-q", "-b", "main")
    git(repo, "config", "user.email", "p41@example.com")
    git(repo, "config", "user.name", "p41")
    (repo / "base.txt").write_text("base\n", encoding="utf-8")
    git(repo, "add", "base.txt")
    git(repo, "commit", "-qm", "base")
    git(repo, "checkout", "-qb", "feature")
    (repo / "feature.txt").write_text("feature\n", encoding="utf-8")
    git(repo, "add", "feature.txt")
    git(repo, "commit", "-qm", "feature add")
    git(repo, "checkout", "-q", "main")


def main() -> int:
    print("== P41 M1.6 合并检查点 / 撤销合并 ==")
    from fastapi.testclient import TestClient

    import agentd.main as M

    M.MOCK = True
    repo = Path(_WS) / "repo"
    make_repo(repo)
    before = git(repo, "rev-parse", "HEAD").stdout.strip()

    with TestClient(M.create_app()) as c:
        preview = c.post("/api/workspace/git/merge/preview", json={"cwd": "repo", "branch": "feature"})
        pdata = preview.json() if preview.status_code == 200 else {}
        cp = pdata.get("checkpoint") or {}
        check("预合并返回 200", preview.status_code == 200, str(preview.text[:200]))
        check("预合并附带检查点", cp.get("sha") == before and cp.get("ref", "").startswith("refs/pocketmind/premerge/"), str(cp))
        check("检查点指向合并前 HEAD", git(repo, "rev-parse", cp.get("ref", "HEAD")).stdout.strip() == before, cp.get("ref", ""))

        got = c.get("/api/workspace/git/merge/checkpoint", params={"cwd": "repo"})
        check("检查点可查询", got.status_code == 200 and (got.json().get("checkpoint") or {}).get("sha") == before, str(got.text[:200]))

        commit = c.post("/api/workspace/git/merge/commit", json={"cwd": "repo", "message": "merge feature"})
        cdata = commit.json() if commit.status_code == 200 else {}
        check("合并提交返回 200", commit.status_code == 200, str(commit.text[:200]))
        check("提交后仍保留检查点", (cdata.get("checkpoint") or {}).get("sha") == before, str(cdata.get("checkpoint")))
        parents = git(repo, "rev-list", "--parents", "-n", "1", "HEAD").stdout.split()
        check("合并提交是双亲提交", len(parents) == 3, str(parents))

        undo = c.post("/api/workspace/git/merge/undo", json={"cwd": "repo"})
        udata = undo.json() if undo.status_code == 200 else {}
        check("撤销合并返回 200", undo.status_code == 200, str(undo.text[:200]))
        check("HEAD 回到合并前", git(repo, "rev-parse", "HEAD").stdout.strip() == before, str(udata)[:300])
        check("撤销后工作区干净", (udata.get("state") or {}).get("clean") is True, str(udata.get("state"))[:300])
        check("撤销后检查点已清理", c.get("/api/workspace/git/merge/checkpoint", params={"cwd": "repo"}).json().get("checkpoint") is None)

        # 第二次：预合并后 abort，检查点也要清理
        preview2 = c.post("/api/workspace/git/merge/preview", json={"cwd": "repo", "branch": "feature"})
        cp2 = (preview2.json() if preview2.status_code == 200 else {}).get("checkpoint") or {}
        check("第二次预合并检查点", cp2.get("sha") == before, str(cp2))
        ab = c.post("/api/workspace/git/merge/abort", json={"cwd": "repo"})
        check("中止合并返回 200", ab.status_code == 200, str(ab.text[:200]))
        check("中止后检查点已清理", c.get("/api/workspace/git/merge/checkpoint", params={"cwd": "repo"}).json().get("checkpoint") is None)

        # 非合并提交不能撤销
        c.post("/api/workspace/git/merge/undo", json={"cwd": "repo"})
        no_cp = c.post("/api/workspace/git/merge/undo", json={"cwd": "repo"})
        check("无检查点时撤销被拒绝", no_cp.status_code == 409, str(no_cp.text[:200]))

    print(f"\n结果: {len(passed)} 通过 / {len(failed)} 失败")
    if failed:
        print("失败项：" + "、".join(failed))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
