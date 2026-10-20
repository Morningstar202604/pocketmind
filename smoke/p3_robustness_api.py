#!/usr/bin/env python3
"""P3 健壮性 API 回归（需 mock 服务；地址取 AGENTD_BASE，默认 8799）：非法输入必须 400 而非 500。"""

import json
import os
import sys
import urllib.request

BASE = os.environ.get("AGENTD_BASE", "http://127.0.0.1:8799")
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


# 限流豁免：健康探测与静态资源不该被自己的限流打死（曾经 1s 轮询撞 30/min → 界面报掉线）
codes = []
for _ in range(40):
    try:
        r = urllib.request.Request(BASE + "/api/health")
        with urllib.request.urlopen(r, timeout=5) as resp:
            codes.append(resp.status)
    except urllib.error.HTTPError as e:
        codes.append(e.code)
check("健康检查连打 40 次不触发 429", set(codes) == {200}, f"codes={sorted(set(codes))}")

root_codes = []
for _ in range(10):
    try:
        with urllib.request.urlopen(BASE + "/", timeout=5) as resp:
            root_codes.append(resp.status)
    except urllib.error.HTTPError as e:
        root_codes.append(e.code)
check("静态首页连打 10 次不触发 429", set(root_codes) == {200}, f"codes={sorted(set(root_codes))}")

# 正常界面流量（会话列表刷新等）不该被限流：一轮界面操作轻松超过 30 次/分钟，
# 旧配额会让用户看到莫名的 429
ui_codes = []
for _ in range(45):
    try:
        with urllib.request.urlopen(BASE + "/api/sessions", timeout=5) as resp:
            ui_codes.append(resp.status)
    except urllib.error.HTTPError as e:
        ui_codes.append(e.code)
check("常规 API 连打 45 次不触发 429（配额适配界面操作）", set(ui_codes) == {200}, f"codes={sorted(set(ui_codes))}")

print()
print(f"结果: {len(passed)} 通过 / {len(failed)} 失败")
if failed:
    print("失败:", failed)
    sys.exit(1)
