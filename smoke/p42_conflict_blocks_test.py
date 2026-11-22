#!/usr/bin/env python3
"""P42：三路冲突按块解决（ours/base/theirs），不依赖真实模型。"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

_HOME = tempfile.mkdtemp(prefix="p42-home-")
_WS = tempfile.mkdtemp(prefix="p42-ws-")
os.environ["AGENT_HOME"] = _HOME
os.environ["AGENT_WORKSPACE"] = _WS
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

passed, failed = [], []


def check(name: str, cond: bool, detail: str = ""):
    (passed if cond else failed).append(name)
    print(("  ✅ " if cond else "  ❌ ") + name + (f" — {detail}" if detail and not cond else ""))


def git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, text=True)


def main() -> int:
    print("== P42 三路冲突块 ==")
    import agentd.main as M
    M.MOCK = True
    from fastapi.testclient import TestClient

    repo = Path(_WS) / "blocks-repo"
    repo.mkdir()
    git(repo, "init", "-q")
    git(repo, "config", "user.email", "p42@example.com")
    git(repo, "config", "user.name", "p42")
    (repo / "conflict.txt").write_text("head\nalpha\nmiddle\nbeta\ntail\n", encoding="utf-8")
    git(repo, "add", "conflict.txt")
    git(repo, "commit", "-qm", "base")
    git(repo, "branch", "-M", "main")
    git(repo, "checkout", "-qb", "feature")
    (repo / "conflict.txt").write_text("head\nalpha-feature\nmiddle\nbeta\ntail-feature\n", encoding="utf-8")
    git(repo, "commit", "-qam", "feature")
    git(repo, "checkout", "-q", "main")
    (repo / "conflict.txt").write_text("head\nalpha-main\nmiddle\nbeta\ntail-main\n", encoding="utf-8")
    git(repo, "commit", "-qam", "main")

    with TestClient(M.create_app()) as c:
        preview = c.post("/api/workspace/git/merge/preview", json={"cwd": str(repo), "branch": "feature"})
        check("进入合并", preview.status_code == 200 and preview.json().get("conflicts"), str(preview.text[:200]))
        blocks = c.get("/api/workspace/git/conflicts/blocks", params={"cwd": str(repo), "path": "conflict.txt"})
        data = blocks.json()
        check("读到冲突块", blocks.status_code == 200 and data.get("count", 0) >= 1, str(data)[:250])
        first = (data.get("blocks") or [{}])[0]
        check("块里有 ours/base/theirs", "alpha-main" in first.get("ours", "") and "alpha-feature" in first.get("theirs", "") and "alpha" == first.get("base", "").strip(), str(first)[:400])

        # 第一块选对方，其余块保持 ours；对两处冲突都显式选择。
        choices = []
        for b in data.get("blocks", []):
            choices.append({"index": b["index"], "side": "theirs" if b["index"] == 0 else "ours"})
        resolved = c.post("/api/workspace/git/conflicts/blocks/resolve", json={"cwd": str(repo), "path": "conflict.txt", "choices": choices})
        check("按块解决返回 200", resolved.status_code == 200, str(resolved.text[:200]))
        check("冲突已清空", resolved.status_code == 200 and not resolved.json().get("remaining"), str(resolved.text[:200]))
        text = (repo / "conflict.txt").read_text(encoding="utf-8")
        check("第一块采用 theirs", "alpha-feature" in text and "alpha-main" not in text, text)
        check("第二块采用 ours", "tail-main" in text and "tail-feature" not in text, text)

        commit = c.post("/api/workspace/git/merge/commit", json={"cwd": str(repo), "message": "merge blocks"})
        check("提交合并", commit.status_code == 200 and commit.json().get("ok") is not False, str(commit.text[:200]))

    shutil.rmtree(_WS, ignore_errors=True)
    shutil.rmtree(_HOME, ignore_errors=True)
    print(f"\n结果: {len(passed)} 通过 / {len(failed)} 失败")
    if failed:
        print("失败: " + ", ".join(failed))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
