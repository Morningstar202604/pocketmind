#!/usr/bin/env python3
"""P1 定时任务验收：创建 interval 任务 → 触发执行 → 会话产出结果 → 启停/删除"""
import json, sys, time, urllib.request
BASE = "http://127.0.0.1:8799"
passed, failed = [], []

def req(method, path, body=None, timeout=30):
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(BASE + path, data=data, method=method, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(r, timeout=timeout) as resp:
        return json.loads(resp.read().decode())

def check(name, cond, detail=""):
    (passed if cond else failed).append(name)
    print(("  ✅ " if cond else "  ❌ ") + name + (f" — {detail}" if detail and not cond else ""))

print("== P1 定时任务验收 ==")
# 1. 空列表
r = req("GET", "/api/jobs")
check("初始无任务", r["jobs"] == [], str(r))
# 2. 创建 interval 5s 任务（条件 battery < 100 应满足）
j = req("POST", "/api/jobs", {"name": "电池巡检", "trigger_type": "interval", "expr": "5", "message": "看一下电池电量", "condition": "battery < 100"})
jid = j["id"]
check("创建任务", j["name"] == "电池巡检" and j["enabled"], j)
# 3. 等待触发并轮询直到有会话完成（interval 任务可能多次触发，取第一个完成的）
sessions = []
found_result = False
detail = ""
for _ in range(20):  # 最多等 20s，每 1s 轮询
    time.sleep(1)
    sessions = req("GET", "/api/sessions")
    if sessions:
        for s in sessions:
            msgs = req("GET", f"/api/sessions/{s['id']}/messages")
            if any(m["role"] == "assistant" and "工具已执行完毕" in (m["content"] or "") for m in msgs):
                found_result = True
                break
        if found_result:
            break
if not found_result and sessions:
    sid = sessions[0]["id"]
    msgs = req("GET", f"/api/sessions/{sid}/messages")
    detail = str([m["content"][:20] for m in msgs])
check("触发产生会话", len(sessions) >= 1, str(sessions))
check("会话有执行结果", found_result, detail)
# 4. 启停
j2 = req("PUT", f"/api/jobs/{jid}", {"enabled": False})
check("停用任务", j2["enabled"] is False)
# 5. 非法条件拒绝
try:
    req("POST", "/api/jobs", {"name": "x", "trigger_type": "interval", "expr": "5", "message": "m", "condition": "foo == 1"})
    check("非法条件被拒", False)
except Exception:
    check("非法条件被拒", True)
# 6. 删除
check("删除任务", req("DELETE", f"/api/jobs/{jid}")["ok"] is True)
# 7. 重启持久化：再创建一个，模拟重启后仍在（服务已带 jobs.db）
j3 = req("POST", "/api/jobs", {"name": "持久化", "trigger_type": "interval", "expr": "3600", "message": "m", "enabled": False})
check("创建停用任务", j3["enabled"] is False)
print(f"\n结果: {len(passed)} 通过 / {len(failed)} 失败")
sys.exit(1 if failed else 0)
