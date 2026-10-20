#!/usr/bin/env python3
"""P5 前端契约回归（需 mock 服务；地址取 AGENTD_BASE，默认 8799）：数据可携性 + 运行参数回环 + 工具分组。

对应 .aqg/code-construction/poketmind-frontend-brand.md 的 R1/R2 与工具分组可见性：
  R1 导出必须覆盖全部本机数据（档案记忆 / 任务清单 / 知识库）
  R2 agent / context / memory 三个配置块能经 PUT /api/settings 回环并被收敛
  R3 新增工具必须带可展示的分组（前端设置面板按 group 分组渲染）

运行：先起 mock 服务，再 `python smoke/p5_frontend_contract_test.py`
"""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request

BASE = os.environ.get("AGENTD_BASE", "http://127.0.0.1:8799")
passed, failed = [], []


def check(name, cond, detail=""):
    (passed if cond else failed).append(name)
    print(("  ✅ " if cond else "  ❌ ") + name + (f" — {detail}" if detail and not cond else ""))


def req(method, path, body=None, timeout=30):
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(
        BASE + path, data=data, method=method, headers={"Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(r, timeout=timeout) as resp:
            raw = resp.read().decode()
            try:
                return resp.status, json.loads(raw)
            except json.JSONDecodeError:
                return resp.status, raw
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()


print("== P5 前端契约 ==")

# ---------- R1：导出覆盖全部本机数据 ----------
st, sess = req("POST", "/api/sessions", {})
sid = sess.get("session_id") if isinstance(sess, dict) else ""
check("准备会话", st == 200 and bool(sid), f"st={st} sid={sid}")

# 写入档案（知识库没有 HTTP 写入端点——设计如此：写入只走 kb_add 工具，避免绕过审批）
req("PUT", "/api/memory/profile", {"user": "P5 测试用户", "facts": "- P5 事实"})
req("POST", "/api/chat", {"session_id": sid, "message": "P5 nothing"})  # 触发一次落库即可
st, exp = req("GET", "/api/export")
check("导出 200 且为 dict", st == 200 and isinstance(exp, dict), f"st={st} type={type(exp).__name__}")
check("导出含档案记忆（memory_profile.user/facts）",
      isinstance(exp.get("memory_profile"), dict)
      and "user" in exp["memory_profile"] and "facts" in exp["memory_profile"],
      f"memory_profile={exp.get('memory_profile')}")
check("导出档案内容与写入一致",
      (exp.get("memory_profile") or {}).get("user", "").find("P5 测试用户") >= 0,
      f"user={(exp.get('memory_profile') or {}).get('user')!r}")
check("导出含知识库列表（knowledge）", isinstance(exp.get("knowledge"), list),
      f"knowledge type={type(exp.get('knowledge')).__name__}")
s0 = (exp.get("sessions") or [{}])[0] if exp.get("sessions") else {}
check("导出会话含任务清单字段（todos）", "todos" in s0, f"keys={sorted(s0.keys())[:12]}")
check("导出仍不含明文 api_key", "api_key" not in json.dumps(exp.get("settings", {})),
      "导出泄漏了 api_key")

# ---------- R1b：手动压缩端点（前端「立即压缩」按钮的契约） ----------
for i in range(12):
    req("POST", "/api/chat", {"session_id": sid, "message": f"P5 compact 第{i}条"})
st, cp = req("POST", f"/api/sessions/{sid}/compact", {"keep": 4})
check("压缩端点 200 且返回归档条数",
      st == 200 and isinstance(cp, dict) and cp.get("ok") is True and cp.get("archived_messages", 0) > 0,
      f"st={st} body={str(cp)[:120]}")
check("压缩保留条数符合请求", cp.get("kept") == 4, f"kept={cp.get('kept')}")
st, cp2 = req("POST", f"/api/sessions/{sid}/compact", {"keep": 200})
check("无需压缩时返回 ok=false + reason",
      st == 200 and cp2.get("ok") is False and bool(cp2.get("reason")), f"body={str(cp2)[:120]}")

# ---------- R2：运行参数回环 + 收敛 ----------
st, _ = req("PUT", "/api/settings", {
    "agent": {"max_tool_chain_depth": 7, "max_tool_retries": 1},
    "context": {"budget_tokens": 50000, "compress_threshold": 0.7, "auto_compress": True},
    "memory": {"search_limit": 9, "summarize_every_n_turns": 6, "retention_days": 30},
})
check("PUT 运行参数 200", st == 200, f"st={st}")
st, s = req("GET", "/api/settings")
check("GET 回显 agent 参数", s.get("agent", {}).get("max_tool_chain_depth") == 7
      and s.get("agent", {}).get("max_tool_retries") == 1, f"agent={s.get('agent')}")
check("GET 回显 context 参数", s.get("context", {}).get("budget_tokens") == 50000
      and abs(float(s.get("context", {}).get("compress_threshold", 0)) - 0.7) < 1e-6,
      f"context={s.get('context')}")
check("GET 回显 memory 参数", s.get("memory", {}).get("search_limit") == 9
      and s.get("memory", {}).get("retention_days") == 30, f"memory={s.get('memory')}")

# 越界值必须被收敛（而不是原样写回）
req("PUT", "/api/settings", {"agent": {"max_tool_chain_depth": 999}, "context": {"compress_threshold": 5}})
st, s2 = req("GET", "/api/settings")
check("越界 chain_depth 收敛到 50", s2.get("agent", {}).get("max_tool_chain_depth") == 50,
      f"got {s2.get('agent', {}).get('max_tool_chain_depth')}")
check("越界 compress_threshold 收敛到 0.95",
      abs(float(s2.get("context", {}).get("compress_threshold", 0)) - 0.95) < 1e-6,
      f"got {s2.get('context', {}).get('compress_threshold')}")

# 恢复默认，避免污染后续套件
req("PUT", "/api/settings", {
    "agent": {"max_tool_chain_depth": 20, "max_tool_retries": 2},
    "context": {"budget_tokens": 0, "compress_threshold": 0.8},
    "memory": {"search_limit": 5, "summarize_every_n_turns": 10, "retention_days": 90},
})

# ---------- R3：新增工具有可展示分组 ----------
st, tools = req("GET", "/api/tools")
groups = {t.get("group") for t in tools.get("tools", [])}
need = {"agent", "knowledge", "memory", "skills"}
check("新增能力分组齐备（前端按组渲染）", need <= groups, f"missing={sorted(need - groups)}")
check("分组内工具不为空",
      all(any(t.get("group") == g for t in tools.get("tools", [])) for g in need),
      f"groups={sorted(groups)}")

print(f"\n结果: {len(passed)} 通过 / {len(failed)} 失败")
if failed:
    print("失败项：" + "；".join(failed))
sys.exit(1 if failed else 0)
