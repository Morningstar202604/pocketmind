#!/usr/bin/env python3
"""P38 M1.1：代码工具 + 变更撤销 + 后台任务 + PTY 会话复用。

不依赖真实模型：直接调工具 handler 与工作区 API；git 测试在临时仓库里跑。
"""
from __future__ import annotations

import asyncio
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

_HOME = tempfile.mkdtemp(prefix="p38-home-")
_WS = tempfile.mkdtemp(prefix="p38-ws-")
os.environ["AGENT_HOME"] = _HOME
os.environ["AGENT_WORKSPACE"] = _WS
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

passed, failed = [], []


def check(name: str, cond: bool, detail: str = ""):
    (passed if cond else failed).append(name)
    print(("  ✅ " if cond else "  ❌ ") + name + (f" — {detail}" if detail and not cond else ""))


def main() -> int:
    print("== P38 M1.1 代码工具 + 变更/任务 + PTY 复用 ==")
    import agentd.main as M
    import agentd.tools.code as C

    M.MOCK = True
    from fastapi.testclient import TestClient

    tool_root = Path(tempfile.mkdtemp(dir=Path.home(), prefix="p38-code-"))
    try:
        sample = tool_root / "calc.py"
        sample.write_text("def add(a, b):\n    return a - b\n", encoding="utf-8")
        edited = asyncio.run(C.edit_file(str(sample), "return a - b", "return a + b"))
        check("edit_file 精确替换", edited.get("ok") is True and "return a + b" in sample.read_text(encoding="utf-8"))
        check("edit_file 返回 diff", "-    return a - b" in edited.get("diff", ""))
        check("edit_file 带备份凭证", bool(edited.get("_ckpt", {}).get("backup")))

        grep = asyncio.run(C.grep("return a", str(tool_root)))
        check("grep 命中行号", grep.get("matches") and grep["matches"][0]["line"] == 2)
        globbed = asyncio.run(C.glob_files("**/*.py", str(tool_root)))
        check("glob 命中 py 文件", any(x["path"].endswith("calc.py") for x in globbed.get("matches", [])))

        repo = tool_root / "gitrepo"
        repo.mkdir()
        subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
        subprocess.run(["git", "config", "user.email", "p38@example.com"], cwd=repo, check=True)
        subprocess.run(["git", "config", "user.name", "p38"], cwd=repo, check=True)
        (repo / "a.txt").write_text("one\n", encoding="utf-8")
        subprocess.run(["git", "add", "a.txt"], cwd=repo, check=True)
        subprocess.run(["git", "commit", "-qm", "init"], cwd=repo, check=True)
        (repo / "a.txt").write_text("two\n", encoding="utf-8")
        gd = asyncio.run(C.git_diff(str(repo)))
        check("git_diff 返回 diff", "diff --git" in gd.get("stdout", ""))

        rt = asyncio.run(C.run_tests(str(tool_root), command="python3 -c 'print(42)'", timeout=30))
        check("run_tests 自定义命令", rt.get("ok") is True and "42" in rt.get("stdout", ""))

        from agentd.diff_preview import preview_tool_call
        prev = preview_tool_call("edit_file", {"path": str(sample), "old_string": "return a + b", "new_string": "return a * b"})
        check("审批卡 diff 预览", "+    return a * b" in prev and "-    return a + b" in prev, prev[:160])
    finally:
        shutil.rmtree(tool_root, ignore_errors=True)

    with TestClient(M.create_app()) as c:
        first = c.put("/api/workspace/file", json={"path": "note.txt", "content": "one\n"})
        cid = first.json().get("change_id")
        check("保存返回 change_id", bool(cid), str(first.text[:120]))
        check("变更列表可见", any(x["id"] == cid for x in c.get("/api/workspace/changes").json()["items"]))
        undone = c.post(f"/api/workspace/changes/{cid}/undo")
        check("撤销新建返回 200", undone.status_code == 200, str(undone.text[:120]))
        check("撤销新建后文件不存在", c.get("/api/workspace/file", params={"path": "note.txt"}).status_code == 404)

        c.put("/api/workspace/file", json={"path": "note.txt", "content": "one\n"})
        second = c.put("/api/workspace/file", json={"path": "note.txt", "content": "two\n"})
        cid2 = second.json().get("change_id")
        undone2 = c.post(f"/api/workspace/changes/{cid2}/undo")
        check("撤销覆盖返回 200", undone2.status_code == 200, str(undone2.text[:120]))
        check("撤销覆盖恢复原文", c.get("/api/workspace/file", params={"path": "note.txt"}).json().get("content") == "one\n")

        task = c.post("/api/workspace/tasks", json={
            "command": "python3 -u -c \"print('TASK_START', flush=True); import time; time.sleep(2); print('TASK_OK', flush=True)\"", "cwd": ".", "timeout": 30,
        })
        tid = task.json().get("id")
        check("后台任务创建", task.status_code == 200 and bool(tid), str(task.text[:120]))
        early = {}
        for _ in range(10):
            early = c.get(f"/api/workspace/tasks/{tid}").json()
            if "TASK_START" in (early.get("stdout") or ""):
                break
            time.sleep(0.1)
        check("任务未结束就能看到增量输出", early.get("status") == "running" and "TASK_START" in early.get("stdout", ""), str(early)[:200])
        detail = {}
        for _ in range(30):
            detail = c.get(f"/api/workspace/tasks/{tid}").json()
            if detail.get("status") not in ("running", "cancelling"):
                break
            time.sleep(0.1)
        check("后台任务完成", detail.get("status") == "done" and "TASK_OK" in detail.get("stdout", ""), str(detail)[:200])

        slow = c.post("/api/workspace/tasks", json={"command": "sleep 5", "cwd": ".", "timeout": 30})
        slow_id = slow.json().get("id")
        time.sleep(0.2)
        cancel = c.post(f"/api/workspace/tasks/{slow_id}/cancel")
        check("取消任务返回 200", cancel.status_code == 200, str(cancel.text[:120]))
        slow_detail = {}
        for _ in range(30):
            slow_detail = c.get(f"/api/workspace/tasks/{slow_id}").json()
            if slow_detail.get("status") not in ("running", "cancelling"):
                break
            time.sleep(0.1)
        check("任务状态 cancelled", slow_detail.get("status") == "cancelled", str(slow_detail)[:200])

        with c.websocket_connect("/api/workspace/term?cwd=.") as w1:
            ready = w1.receive_text()
            check("PTY 返回 ready 控制帧", '"type": "ready"' in ready and "session_id" in ready, ready[:160])
            sid = json.loads(ready).get("session_id", "")
            w1.send_text(json.dumps({"type": "input", "data": "echo P38_PTY_OK\n"}))
            text = ""
            for _ in range(12):
                text += w1.receive_text()
                if text.count("P38_PTY_OK") >= 2:
                    break
            check("PTY 输出正常", text.count("P38_PTY_OK") >= 2, text[-160:])
        with c.websocket_connect(f"/api/workspace/term?cwd=.&session={sid}") as w2:
            ready2 = w2.receive_text()
            check("PTY 断线重连接管会话", '"reconnected": true' in ready2 and sid in ready2, ready2[:160])
            w2.send_text(json.dumps({"type": "input", "data": "exit\n"}))

    print(f"\n结果: {len(passed)} 通过 / {len(failed)} 失败")
    if failed:
        print("失败项：" + "、".join(failed))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
