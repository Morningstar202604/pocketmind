#!/usr/bin/env python3
"""P2 验收：Plan/Act 双模式 + Recipe 一键实例化。

覆盖：
  - PUT /api/settings 切到 plan 模式后，聊天 SSE 返回 plan 事件且不执行任何工具；
  - plan 模式 done 事件 stop_reason == "plan"；
  - 切回 act 模式后工具正常执行（回归）；
  - GET /api/recipes 返回预设配方列表；
  - POST /api/recipes/{id}/apply 幂等地创建定时任务，列表里 applied 变 true。
"""
import json
import sys
import urllib.request

BASE = "http://127.0.0.1:8799"
passed, failed = [], []


def req(method, path, body=None, timeout=30):
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(BASE + path, data=data, method=method,
                               headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(r, timeout=timeout) as resp:
        return json.loads(resp.read().decode())


def chat(message, timeout=30):
    """发一条聊天，边读 SSE 边聚合事件类型与关键字段。"""
    body = json.dumps({"message": message}).encode()
    r = urllib.request.Request(BASE + "/api/chat", data=body, method="POST",
                               headers={"Content-Type": "application/json"})
    events = []
    with urllib.request.urlopen(r, timeout=timeout) as resp:
        while True:
            line = resp.readline()
            if not line:
                break
            if line.startswith(b"data: "):
                events.append(json.loads(line[6:].decode()))
    return events


def check(name, cond, detail=""):
    (passed if cond else failed).append(name)
    print(("  ✅ " if cond else "  ❌ ") + name + (f" — {detail}" if detail and not cond else ""))


print("== P2 Plan/Act + Recipe 验收 ==")

# ---------- Plan/Act ----------
# 切到 plan 模式
req("PUT", "/api/settings", {"agent_mode": "plan"})
s = req("GET", "/api/settings")
check("GET settings 返回 agent_mode=plan", s.get("agent_mode") == "plan", str(s.get("agent_mode")))

events = chat("查看电池电量")
types = [e["type"] for e in events]
plan_ev = next((e for e in events if e["type"] == "plan"), None)
done_ev = next((e for e in events if e["type"] == "done"), None)
check("plan 模式返回 plan 事件", plan_ev is not None, str(types))
check("plan 事件含计划工具调用",
      plan_ev is not None and isinstance(plan_ev.get("tool_calls"), list) and len(plan_ev["tool_calls"]) >= 1,
      str(plan_ev))
check("plan 模式不执行工具（无 tool_start/tool_update）",
      "tool_start" not in types and "tool_update" not in types, str(types))
check("plan 模式 done.stop_reason == plan",
      done_ev is not None and done_ev.get("stop_reason") == "plan", str(done_ev))

# 切回 act 模式：工具应正常执行（回归）
req("PUT", "/api/settings", {"agent_mode": "act", "permission_mode": "auto"})
events2 = chat("查看电池电量")
types2 = [e["type"] for e in events2]
check("act 模式恢复工具执行", "tool_start" in types2 and "tool_update" in types2, str(types2))

# ---------- Recipe ----------
recipes = req("GET", "/api/recipes")["recipes"]
check("GET /api/recipes 返回预设配方", len(recipes) >= 3, str([r["id"] for r in recipes]))
check("配方字段完整",
      all("id" in r and "name" in r and "trigger_type" in r and "expr" in r for r in recipes),
      str(recipes[0]))

# 选一个默认停用的配方（weather）实例化，避免它真的触发
target = next((r for r in recipes if r["id"] == "weather"), None)
check("存在 weather 配方", target is not None)
before_applied = target["applied"]
job = req("POST", "/api/recipes/weather/apply")
check("apply 返回创建的 job", job.get("id") and job.get("trigger_type") == "cron", str(job))
# 幂等：再 apply 一次应返回同一个 job
job2 = req("POST", "/api/recipes/weather/apply")
check("apply 幂等（同 job_id）", job2.get("id") == job.get("id"), f"{job.get('id')} vs {job2.get('id')}")
# 列表里 applied 应变 true
recipes2 = req("GET", "/api/recipes")["recipes"]
w2 = next((r for r in recipes2 if r["id"] == "weather"), {})
check("apply 后列表标记 applied=true", w2.get("applied") is True, str(w2))

# 清理：删掉这个测试 job
req("DELETE", f"/api/jobs/{job['id']}")

# 未知配方 404
try:
    req("POST", "/api/recipes/no_such_recipe/apply")
    check("未知配方返回 404", False)
except Exception:
    check("未知配方返回 404", True)

print(f"\n结果: {len(passed)} 通过 / {len(failed)} 失败")
sys.exit(1 if failed else 0)
