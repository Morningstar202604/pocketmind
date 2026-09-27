#!/usr/bin/env python3
"""M5 回归：审批/命名/级联/未知工具/打码/权限 —— 直连 API 验证"""
import json
import os
import subprocess
import sys
import time
import urllib.request

BASE = "http://127.0.0.1:8799"
passed, failed = [], []

def req(method, path, body=None, timeout=20):
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(BASE + path, data=data, method=method,
                               headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(r, timeout=timeout) as resp:
        return json.loads(resp.read().decode())

def chat(message, session_id=None, timeout=90, on_approval=None):
    """调用 /api/chat（SSE 流），边读边回调审批；返回最终聚合结果"""
    body = json.dumps({"session_id": session_id, "message": message}).encode()
    r = urllib.request.Request(BASE + "/api/chat", data=body, method="POST",
                               headers={"Content-Type": "application/json"})
    out, gate, sid, tool_ok = {"text": "", "status": "error"}, None, None, False
    with urllib.request.urlopen(r, timeout=timeout) as resp:
        while True:
            line = resp.readline()
            if not line:
                break
            if line.startswith(b"data: "):
                ev = json.loads(line[6:].decode())
                t = ev.get("type")
                if t == "session":
                    sid = ev.get("session_id")
                elif t == "text":
                    out["text"] += ev.get("text", "")
                elif t == "tool_start":
                    if ev.get("gate_id"):
                        gate = ev.get("gate_id")
                elif t == "approval":
                    gate = ev.get("id") or gate
                    if on_approval:
                        on_approval(sid, gate)
                elif t == "tool_update":
                    if ev.get("status") == "completed" and ev.get("result"):
                        tool_ok = True
                elif t == "done":
                    out["status"] = "completed" if ev.get("stop_reason") == "end_turn" else ev.get("stop_reason", "done")
    out["session_id"] = sid
    out["tool_ok"] = tool_ok
    if gate:
        out["gate_id"] = gate
    return out

def check(name, cond, detail=""):
    (passed if cond else failed).append(name)
    print(("  ✅ " if cond else "  ❌ ") + name + (f" — {detail}" if detail and not cond else ""))

print("== M5 安全加固回归 ==")

# 1. 打码：设置 32 位 key 后 GET 只回打码
req("PUT", "/api/settings", {"llm": {"api_key": "sk-abcdefghijklmnopqrstuvwxyz123456", "provider": "deepseek"}})
s = req("GET", "/api/settings")
check("API key 打码", s["llm"]["api_key"] == "sk-a***************************3456" and "sk-abcdefghij" not in s["llm"]["api_key"], s["llm"]["api_key"])

# 2. 数据文件权限（600/700）
import pathlib

home = pathlib.Path(os.environ.get("AGENT_HOME", "/tmp/pa-test"))
mode_db = (home / "agent.db").stat().st_mode & 0o777
mode_dir = home.stat().st_mode & 0o777
check("数据库 600", mode_db == 0o600, oct(mode_db))
check("目录 700", mode_dir == 0o700, oct(mode_dir))

# 3. 核心流程：电池工具（auto 模式直接跑）
req("PUT", "/api/settings", {"permission_mode": "auto"})
r = chat("查看电池电量")
sid = r.get("session_id")
check("电池工具完成", r.get("status") == "completed" and r.get("tool_ok"), r.get("status", "")[:40])

# 4. 审批：切回 approve，拨号应弹审批卡，流中回调 deny，随后会话应正常收尾
req("PUT", "/api/settings", {"permission_mode": "approve"})
denied = {"ok": False}
def deny(sid, tid):
    if sid and tid:
        req("POST", "/api/approval", {"session_id": sid, "tool_call_id": tid, "decision": "deny"})
        denied["ok"] = True
r = chat("给10086打个电话", on_approval=deny)
check("danger 工具弹审批", bool(r.get("gate_id")), f"gate={r.get('gate_id')}")
check("审批回调已发拒绝", denied["ok"])
check("拒绝后会话正常收尾", r.get("status") == "completed", str(r.get("status")))

# 4b. 审批超时：设 2s 超时，不审批 → 自动拒绝（工具不得执行）
req("PUT", "/api/settings", {"server": {"approval_timeout": 2}})
r = chat("给10086打个电话")
check("审批超时自动拒绝", r.get("status") == "completed" and not r.get("tool_ok"), f"status={r.get('status')} tool_ok={r.get('tool_ok')}")
req("PUT", "/api/settings", {"server": {"approval_timeout": 120}})

# 5. 会话自动命名
ss = req("GET", "/api/sessions")
names = [x.get("title", "") for x in ss if isinstance(x, dict)]
check("会话自动命名", any("电池" in n or "电量" in n for n in names), str(names)[:80])

# 6. 工具清单（31 项：30 内建 + tts_offline 离线语音）
tools = req("GET", "/api/tools")
check("工具清单 31 项", tools.get("count", 0) == 31, str(tools.get("count")))

print(f"\n结果: {len(passed)} 通过 / {len(failed)} 失败")
sys.exit(1 if failed else 0)
