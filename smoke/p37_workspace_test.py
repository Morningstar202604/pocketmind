#!/usr/bin/env python3
"""P37 开发工作区 API 与 PTY 终端（1.6.x）：

1. /api/workspace/info → tree → file → preview → exec 全链路；
2. 路径 jail：/etc/passwd 写入、rm -rf /、越界 cwd 被拒；
3. WebSocket PTY：起 shell、执行 echo、收到输出。
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

_HOME = tempfile.mkdtemp(prefix="p37-home-")
_WS = tempfile.mkdtemp(prefix="p37-ws-")
os.environ["AGENT_HOME"] = _HOME
os.environ["AGENT_WORKSPACE"] = _WS
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

passed, failed = [], []


def check(name: str, cond: bool, detail: str = ""):
    (passed if cond else failed).append(name)
    print(("  ✅ " if cond else "  ❌ ") + name + (f" — {detail}" if detail and not cond else ""))


def main() -> int:
    print("== P37 开发工作区 API 与 PTY ==")
    import agentd.main as M

    M.MOCK = True
    from fastapi.testclient import TestClient

    app = M.create_app()
    with TestClient(app) as c:
        info = c.get("/api/workspace/info")
        check("info 返回 200", info.status_code == 200, str(info.text[:120]))
        root = info.json().get("root", "") if info.status_code == 200 else ""
        check("工作区根指向临时目录", root == str(Path(_WS).resolve()), root)

        put = c.put("/api/workspace/file", json={"path": "hello.py", "content": 'print("hi")\n'})
        check("写文件 200", put.status_code == 200, str(put.text[:120]))
        got = c.get("/api/workspace/file", params={"path": "hello.py"})
        check("读文件内容正确", got.status_code == 200 and got.json().get("content") == 'print("hi")\n', str(got.text[:120]))

        preview = c.post("/api/workspace/preview", json={"path": "hello.py", "content": 'print("hello")\n'})
        check("preview 识别改动", preview.status_code == 200 and preview.json().get("changed") is True, str(preview.text[:120]))
        check("preview 有 unified diff", "--- a/hello.py" in preview.text and "+++ b/hello.py" in preview.text, str(preview.text[:200]))

        tree = c.get("/api/workspace/tree", params={"path": "."})
        check("文件树包含 hello.py", tree.status_code == 200 and any(e["name"] == "hello.py" for e in tree.json().get("entries", [])), str(tree.text[:160]))

        ex = c.post("/api/workspace/exec", json={"command": "python3 hello.py", "cwd": "."})
        check("命令执行 200", ex.status_code == 200, str(ex.text[:120]))
        check("命令 stdout 正确", ex.json().get("stdout") == "hi", str(ex.text[:160]))

        bad_write = c.put("/api/workspace/file", json={"path": "/etc/passwd", "content": "x"})
        check("拒绝写 /etc/passwd", bad_write.status_code in (400, 403), str(bad_write.text[:120]))
        bad_cmd = c.post("/api/workspace/exec", json={"command": "rm -rf /", "cwd": "."})
        check("拒绝 rm -rf /", bad_cmd.status_code == 400, str(bad_cmd.text[:120]))
        bad_cwd = c.post("/api/workspace/exec", json={"command": "pwd", "cwd": "/etc"})
        check("拒绝越界 cwd", bad_cwd.status_code in (400, 403), str(bad_cwd.text[:120]))

        try:
            with c.websocket_connect("/api/workspace/term?cwd=.") as ws:
                ws.send_text('{"type":"input","data":"echo P37_PTY_OK\\n"}')
                got_text = ""
                for _ in range(20):
                    got_text += ws.receive_text()
                    if got_text.count("P37_PTY_OK") >= 2:
                        break
                check("PTY 返回 echo 输出", got_text.count("P37_PTY_OK") >= 2, got_text[-200:])
                ws.send_text('{"type":"input","data":"exit\\n"}')
        except Exception as exc:  # noqa: BLE001 —— 测试脚本要给出可读失败原因
            check("PTY WebSocket 可用", False, f"{type(exc).__name__}: {exc}")

    print(f"\n结果: {len(passed)} 通过 / {len(failed)} 失败")
    if failed:
        print("失败项：" + "、".join(failed))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
