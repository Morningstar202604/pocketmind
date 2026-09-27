#!/usr/bin/env python3
"""P0 排队验收（并发：第一轮挂审批时发第二轮）"""
import json
import sys
import threading
import time
import urllib.request

BASE = "http://127.0.0.1:8799"
passed, failed = [], []

def req(method, path, body=None, timeout=30):
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(BASE + path, data=data, method=method,
                               headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(r, timeout=timeout) as resp:
        return json.loads(resp.read().decode())

def chat(message, session_id, timeout=30):
    body = json.dumps({"session_id": session_id, "message": message}).encode()
    r = urllib.request.Request(BASE + "/api/chat", data=body, method="POST",
                               headers={"Content-Type": "application/json"})
    events = []
    try:
        with urllib.request.urlopen(r, timeout=timeout) as resp:
            while True:
                line = resp.readline()
                if not line:
                    break
                if line.startswith(b"data: "):
                    events.append(json.loads(line[6:].decode()))
    except Exception as e:
        events.append({"type": "_err", "message": str(e)})
    return events

def check(name, cond, detail=""):
    (passed if cond else failed).append(name)
    print(("  ✅ " if cond else "  ❌ ") + name + (f" — {detail}" if detail and not cond else ""))

print("== P0 排队验收 ==")

# 准备：approve + 长审批超时（挂住第一轮）
req("PUT", "/api/settings", {"permission_mode": "approve", "server": {"approval_timeout": 120}})
sid = req("POST", "/api/sessions", {})["session_id"]

r1_box = {}
def run1():
    r1_box["events"] = chat("给10086打个电话", sid, timeout=90)
t = threading.Thread(target=run1, daemon=True)
t.start()
time.sleep(3)  # 等第一轮发出 approval

# 第二轮：应 queued
r2 = chat("再看一下电池电量", sid, timeout=30)
types2 = [e["type"] for e in r2]
check("第二轮返回 queued", "queued" in types2, str(types2))

# 审批放行第一轮
req("POST", "/api/approval", {"session_id": sid, "tool_call_id": "call_mock_1", "decision": "allow_once"})
t.join(timeout=30)
types1 = [e["type"] for e in r1_box.get("events", [])]
check("第一轮审批后完成", "done" in types1 and "approval" in types1, str(types1))

# 排队消息自动执行（服务端队列：第二条在第一条结束后续跑）
deadline = time.time() + 40
ok = False
while time.time() < deadline:
    msgs = req("GET", f"/api/sessions/{sid}/messages")
    users = [m for m in msgs if m["role"] == "user"]
    if len(users) >= 2:
        ok = True
        break
    time.sleep(2)
check("排队消息自动执行", ok, f"user 消息={len(users)}")

print(f"\n结果: {len(passed)} 通过 / {len(failed)} 失败")
sys.exit(1 if failed else 0)
