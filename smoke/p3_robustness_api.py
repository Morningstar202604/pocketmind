#!/usr/bin/env python3
"""P3 健壮性 API 回归（需 mock 服务在 8799）：非法输入必须 400 而非 500。"""

import json
import sys
import urllib.request

BASE = "http://127.0.0.1:8799"
passed, failed = [], []


def check(name, cond, detail=""):
    (passed if cond else failed).append(name)
    print(("  OK " if cond else "  XX ") + name + (f" — {detail}" if detail and not cond else ""))


def req(method, path, body=None, timeout=20):
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(
        BASE + path, data=data, method=method, headers={"Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(r, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()


print("== 健壮性：非法输入 400 而非 500 ==")

# BUG-1：非法 cron
st, _ = req(
    "POST",
    "/api/jobs",
    {"name": "x", "trigger_type": "cron", "expr": "not a cron !!!", "message": "m"},
)
check("非法 cron 返回 400（曾 500）", st == 400, f"got {st}")

# BUG-3：合法 date 表达式
st, body = req(
    "POST",
    "/api/jobs",
    {"name": "date测", "trigger_type": "date", "expr": "2030-01-01 08:00", "message": "m"},
)
check(
    "合法 date 表达式创建成功（曾 500）", st == 200 and "id" in body, f"got {st} {str(body)[:120]}"
)
if st == 200:
    req("DELETE", f"/api/jobs/{body['id']}")

# 空消息 / 超长
st, _ = req("POST", "/api/chat", {"message": "   "})
check("空消息 400", st == 400, f"got {st}")
st, _ = req("POST", "/api/chat", {"message": "x" * 100001})
check("超长消息 400", st == 400, f"got {st}")

# 非法 decision
st, _ = req("POST", "/api/approval", {"session_id": "x", "tool_call_id": "y", "decision": "bogus"})
check("非法 decision 400", st == 400, f"got {st}")

# 非法 interval 极值
st, _ = req(
    "POST", "/api/jobs", {"name": "x", "trigger_type": "interval", "expr": "abc", "message": "m"}
)
check("interval 非数字 400", st == 400, f"got {st}")

print()
print(f"结果: {len(passed)} 通过 / {len(failed)} 失败")
if failed:
    print("失败:", failed)
    sys.exit(1)
