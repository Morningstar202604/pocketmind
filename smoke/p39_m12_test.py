#!/usr/bin/env python3
"""P39 M1.2：文件锁 + Git 分支/合并/冲突 + patch 预检。

在临时 AGENT_WORKSPACE 里建真实 Git 仓库，不用 mock git。
"""
from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import tempfile
from pathlib import Path

_HOME = tempfile.mkdtemp(prefix="p39-home-")
_WS = tempfile.mkdtemp(prefix="p39-ws-")
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
    git(repo, "config", "user.email", "p39@example.com")
    git(repo, "config", "user.name", "p39")
    (repo / "hello.txt").write_text("base\n", encoding="utf-8")
    git(repo, "add", "hello.txt")
    git(repo, "commit", "-qm", "base")
    git(repo, "checkout", "-qb", "feature")
    (repo / "hello.txt").write_text("feature\n", encoding="utf-8")
    git(repo, "commit", "-qam", "feature edit")
    git(repo, "checkout", "-q", "main")
    (repo / "hello.txt").write_text("main\n", encoding="utf-8")
    git(repo, "commit", "-qam", "main edit")


def main() -> int:
    print("== P39 M1.2 文件锁 + Git 合并 + patch ==")
    import agentd.main as M
    from agentd.workspace_locks import WorkspaceLockBusy, get_locks, hold

    M.MOCK = True
    from fastapi.testclient import TestClient

    repo = Path(_WS) / "repo"
    make_repo(repo)

    async def lock_checks():
        registry = get_locks()
        busy = False
        seen_holder = []

        async def holder():
            async with hold(repo / "hello.txt", "agent:edit_file", wait=5):
                seen_holder.append(registry.snapshot())
                await asyncio.sleep(0.35)

        async def waiter():
            nonlocal busy
            try:
                async with hold(repo / "hello.txt", "human:save", wait=0.12):
                    pass
            except WorkspaceLockBusy:
                busy = True

        await asyncio.gather(holder(), waiter())
        return busy, seen_holder

    busy, snapshots = asyncio.run(lock_checks())
    snapshot = snapshots[0] if snapshots else []
    check("锁被占用时短等待会失败", busy, str(snapshot)[:200])
    check("锁快照能看到持有者", any(x["owner"] == "agent:edit_file" for x in snapshot), str(snapshot)[:200])

    with TestClient(M.create_app()) as c:
        st = c.get("/api/workspace/git/status", params={"cwd": "repo"})
        data = st.json() if st.status_code == 200 else {}
        check("Git status 返回分支", st.status_code == 200 and data.get("branch") == "main", str(st.text[:200]))
        check("Git status 识别干净工作区", data.get("clean") is True, str(data)[:200])
        check("Git 分支列表包含 feature", any(b["name"] == "feature" for b in data.get("branches", [])), str(data)[:300])

        preview = c.post("/api/workspace/git/merge/preview", json={"cwd": "repo", "branch": "feature"})
        pdata = preview.json() if preview.status_code == 200 else {}
        check("预合并返回 200", preview.status_code == 200, str(preview.text[:200]))
        check("预合并检测冲突", pdata.get("clean") is False and "hello.txt" in pdata.get("conflicts", []), str(pdata)[:300])

        conf = c.get("/api/workspace/git/conflicts", params={"cwd": "repo"})
        cdata = conf.json() if conf.status_code == 200 else {}
        item = (cdata.get("items") or [{}])[0]
        check("冲突读取 base/ours/theirs", all(k in item for k in ("base", "ours", "theirs")), str(cdata)[:300])
        check("冲突内容正确", item.get("ours", "").strip() == "main" and item.get("theirs", "").strip() == "feature", str(item)[:300])

        bad = c.post("/api/workspace/git/checkout", json={"cwd": "repo", "branch": "feature"})
        check("合并中拒绝切分支", bad.status_code == 409, str(bad.text[:200]))

        resolved = c.post("/api/workspace/git/conflicts/resolve", json={
            "cwd": "repo", "path": "hello.txt", "strategy": "ours",
        })
        check("解决冲突返回 200", resolved.status_code == 200, str(resolved.text[:200]))
        check("解决后无剩余冲突", not (resolved.json().get("remaining") if resolved.status_code == 200 else ["?"]), str(resolved.text[:200]))

        commit = c.post("/api/workspace/git/merge/commit", json={"cwd": "repo", "message": "merge feature"})
        check("提交合并返回 200", commit.status_code == 200, str(commit.text[:200]))
        after = c.get("/api/workspace/git/status", params={"cwd": "repo"}).json()
        check("合并后工作区干净", after.get("clean") is True and after.get("merging") is False, str(after)[:200])

        # 分支切换：干净时允许
        co = c.post("/api/workspace/git/checkout", json={"cwd": "repo", "branch": "feature"})
        check("干净工作区可切分支", co.status_code == 200 and co.json().get("branch") == "feature", str(co.text[:200]))
        c.post("/api/workspace/git/checkout", json={"cwd": "repo", "branch": "main"})

        patch = (
            "diff --git a/hello.txt b/hello.txt\n"
            "--- a/hello.txt\n"
            "+++ b/hello.txt\n"
            "@@ -1 +1 @@\n"
            "-main\n"
            "+patched\n"
        )
        dry = c.post("/api/workspace/patch", json={"cwd": "repo", "patch": patch, "dry_run": True})
        check("patch 预检 200", dry.status_code == 200, str(dry.text[:200]))
        applied = c.post("/api/workspace/patch", json={"cwd": "repo", "patch": patch, "dry_run": False})
        check("patch 应用 200", applied.status_code == 200, str(applied.text[:200]))
        check("patch 内容已改", (repo / "hello.txt").read_text(encoding="utf-8") == "patched\n")

        # 并发保存：第二个人拿旧 mtime 保存，必须 409
        got = c.get("/api/workspace/file", params={"path": "repo/hello.txt"}).json()
        first = c.put("/api/workspace/file", json={
            "path": "repo/hello.txt", "content": "one\n", "expected_mtime": got["mtime"],
        })
        check("按 mtime 保存成功", first.status_code == 200, str(first.text[:200]))
        second = c.put("/api/workspace/file", json={
            "path": "repo/hello.txt", "content": "two\n", "expected_mtime": got["mtime"],
        })
        check("旧 mtime 保存被 409 拒绝", second.status_code == 409, str(second.text[:200]))

    print(f"\n结果: {len(passed)} 通过 / {len(failed)} 失败")
    if failed:
        print("失败项：" + "、".join(failed))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
