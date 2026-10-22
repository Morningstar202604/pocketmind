#!/usr/bin/env python3
"""P14 路由层测试：用 FastAPI TestClient 在**进程内**覆盖全部 43 个端点。

为什么用 TestClient 而不是起真服务：
- 之前 main.py 的行覆盖率只有 19%，因为路由全在 create_app() 闭包里，
  起独立进程测（7 个 API 套件）时 coverage 采不到另一个进程的数据。
- TestClient 在同一个进程内调用 ASGI app，覆盖率能直接采集，
  也不需要挑端口、不需要等启动、不会和本机其他服务抢 8799。
- 这不是替代已有的 API 套件（那些测的是真实 HTTP 语义），而是补上
  "路由函数本身有没有被走到、错误分支对不对" 这一层。

覆盖重点不是 happy path，而是**每个端点的错误分支**：
缺参数 → 400/422、资源不存在 → 404、非法值 → 400。
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

# AGENT_HOME 必须在 import agentd.main 之前设定（settings_mgr 是模块级单例）
_HOME = tempfile.mkdtemp(prefix="p14-")
os.environ["AGENT_HOME"] = _HOME
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

passed, failed = [], []


def check(name: str, cond: bool, detail: str = ""):
    (passed if cond else failed).append(name)
    print(("  ✅ " if cond else "  ❌ ") + name + (f" — {detail}" if detail and not cond else ""))


def main() -> int:
    print("== P14 路由层（进程内） ==")
    import agentd.main as M

    M.MOCK = True
    from fastapi.testclient import TestClient

    app = M.create_app()
    with TestClient(app) as c:
        # ---------- 基础 ----------
        r = c.get("/api/health")
        check("GET /api/health 200", r.status_code == 200, str(r.status_code))
        body = r.json()
        check("health 含 components.db", body.get("components", {}).get("db") == "ok", str(body.get("components")))
        check("health 含 ready 字段", "ready" in body)

        r = c.get("/")
        check("GET / 返回静态首页", r.status_code == 200 and "<" in r.text, str(r.status_code))

        # ---------- 会话 CRUD ----------
        r = c.post("/api/sessions")
        check("POST /api/sessions 201/200", r.status_code in (200, 201), str(r.status_code))
        sid = r.json()["session_id"]
        check("新建会话返回 session_id", bool(sid))

        r = c.get("/api/sessions")
        check("GET /api/sessions 200", r.status_code == 200)
        check("会话列表含刚建的会话", any(s["id"] == sid for s in r.json()))

        r = c.put(f"/api/sessions/{sid}", json={"title": "改名测试"})
        check("PUT /api/sessions/{sid} 改名", r.status_code == 200, str(r.status_code))

        r = c.get(f"/api/sessions/{sid}/messages")
        check("GET messages 200", r.status_code == 200)

        r = c.get("/api/sessions/nonexistent/messages")
        check("不存在会话的 messages 返回 404", r.status_code == 404, str(r.status_code))

        r = c.get(f"/api/sessions/{sid}/todos")
        check("GET todos 200", r.status_code == 200)
        r = c.delete(f"/api/sessions/{sid}/todos")
        check("DELETE todos 200", r.status_code == 200)

        r = c.post(f"/api/sessions/{sid}/compact", json={"keep": 5})
        check("POST compact 200", r.status_code == 200, str(r.status_code))

        # ---------- 消息截断 / 搜索 ----------
        r = c.delete(f"/api/sessions/{sid}/messages/999999")
        check("截断不存在的消息不报 500", r.status_code in (200, 404), str(r.status_code))

        r = c.get("/api/sessions/search", params={"q": "测试"})
        check("GET sessions/search 200", r.status_code == 200, str(r.status_code))
        check("搜索空结果返回 items 列表", isinstance(r.json().get("items"), list))

        r = c.get("/api/sessions/search")
        check("搜索无 q 参数返回空列表", r.json().get("items") == [])

        # ---------- 聊天参数校验 ----------
        r = c.post("/api/chat", json={})
        check("chat 缺 message → 400", r.status_code == 400, str(r.status_code))
        r = c.post("/api/chat", json={"message": "   "})
        check("chat 空白 message → 400", r.status_code == 400, str(r.status_code))
        r = c.post("/api/chat", json={"message": "x" * 100001})
        check("chat 超长 message → 400", r.status_code == 400, str(r.status_code))
        r = c.post("/api/chat", json={"message": "hi", "session_id": "no-such"})
        check("chat 不存在会话 → 404", r.status_code == 404, str(r.status_code))

        # ---------- 审批 ----------
        r = c.post("/api/approval", json={"session_id": "x", "tool_call_id": "y", "decision": "bogus"})
        check("approval 非法 decision → 400", r.status_code == 400, str(r.status_code))
        r = c.post("/api/approval", json={"session_id": "x", "tool_call_id": "y", "decision": "allow_once"})
        check("approval 不存在的 gate → 404", r.status_code == 404, str(r.status_code))
        r = c.get("/api/approvals/pending")
        check("GET approvals/pending 200", r.status_code == 200)
        r = c.get("/api/approvals")
        check("GET approvals 规则列表 200", r.status_code == 200)
        r = c.delete("/api/approvals")
        check("DELETE approvals 清空 200", r.status_code == 200)

        # ---------- 定时任务 ----------
        r = c.get("/api/jobs")
        check("GET jobs 200", r.status_code == 200)
        r = c.post("/api/jobs", json={"name": "t", "trigger_type": "cron", "expr": "bad", "message": "m"})
        check("jobs 非法 cron → 400", r.status_code == 400, str(r.status_code))
        r = c.post("/api/jobs", json={"name": "t", "trigger_type": "interval", "expr": "3600", "message": "m"})
        check("jobs 合法 interval 创建成功", r.status_code == 200, str(r.status_code))
        jid = r.json()["id"]
        r = c.put(f"/api/jobs/{jid}", json={"enabled": False})
        check("jobs 停用", r.status_code == 200 and r.json().get("enabled") is False)
        r = c.delete(f"/api/jobs/{jid}")
        check("jobs 删除", r.status_code == 200)
        # 幂等删除：不存在的 job 返回 {"ok": false} 而不是 404。
        # 这是有意的设计——客户端重试删除不应报错；关键是**不能**返回 ok=true
        # 谎称删掉了。
        r = c.delete("/api/jobs/no-such-id")
        check("删不存在的 job 返回 200 且 ok=false（幂等）",
              r.status_code == 200 and r.json().get("ok") is False,
              f"{r.status_code} {r.text[:60]}")

        # ---------- 配方 ----------
        r = c.get("/api/recipes")
        check("GET recipes 200", r.status_code == 200 and "recipes" in r.json())
        r = c.post("/api/recipes/no-such/apply")
        check("未知配方 apply → 404", r.status_code == 404, str(r.status_code))

        # ---------- 设置 ----------
        r = c.get("/api/settings")
        check("GET settings 200", r.status_code == 200)
        check("settings 中 api_key 被打码或为空",
              r.json().get("llm", {}).get("api_key", "") in ("", None) or "*" in str(r.json()["llm"]["api_key"]))
        r = c.put("/api/settings", json={"permission_mode": "bogus"})
        check("settings 非法 permission_mode 被收敛而非 500", r.status_code in (200, 400), str(r.status_code))
        r = c.put("/api/settings", json={"permission_mode": "approve"})
        check("settings 合法写入 200", r.status_code == 200, str(r.status_code))

        # ---------- 模型 / 厂商 ----------
        r = c.get("/api/providers")
        check("GET providers 200", r.status_code == 200)
        r = c.post("/api/llm/test", json={})
        check("llm/test 未配置时返回可读错误（非 500）", r.status_code in (200, 400), str(r.status_code))

        # ---------- 记忆 / 知识库 / 技能 ----------
        r = c.get("/api/memory/profile")
        check("GET memory/profile 200", r.status_code == 200)
        r = c.put("/api/memory/profile", json={"user": "测试用户", "facts": "喜欢咖啡"})
        check("PUT memory/profile 200", r.status_code == 200, str(r.status_code))
        r = c.get("/api/memory")
        check("GET memory 200", r.status_code == 200)
        r = c.get("/api/memory/search", params={"q": "咖啡"})
        check("GET memory/search 200", r.status_code == 200)
        # Mem0 在 mock / 未安装时是 503 "Mem0 未启用"——正确行为：
        # 该能力依赖可选组件，缺组件时明确说"没启用"，而不是假装成功清空。
        r = c.post("/api/memory/reset")
        check("POST memory/reset 在 Mem0 未启用时 503 且说明原因",
              r.status_code == 503 and "Mem0" in r.text, f"{r.status_code} {r.text[:60]}")
        r = c.delete("/api/memory/999999")
        check("DELETE memory 在 Mem0 未启用时 503（不是 500）",
              r.status_code == 503, str(r.status_code))

        r = c.get("/api/kb")
        check("GET kb 200", r.status_code == 200)
        r = c.delete("/api/kb/999999")
        check("DELETE 不存在的 kb 不 500", r.status_code in (200, 404), str(r.status_code))

        r = c.get("/api/skills")
        check("GET skills 200", r.status_code == 200)

        # ---------- 工具 / MCP / 统计 / 诊断 ----------
        r = c.get("/api/tools")
        check("GET tools 200 且含 count", r.status_code == 200 and "count" in r.json())
        check("工具数量 >= 38", r.json().get("count", 0) >= 38, str(r.json().get("count")))
        r = c.get("/api/mcp")
        check("GET mcp 200", r.status_code == 200)
        r = c.get("/api/stats")
        check("GET stats 200", r.status_code == 200)
        r = c.get("/api/diagnostics")
        check("GET diagnostics 200", r.status_code == 200)
        check("diagnostics 含 capabilities", "capabilities" in r.json())

        # ---------- 导出 ----------
        r = c.get("/api/export")
        check("GET export 200", r.status_code == 200)
        payload = r.json()
        for key in ("app", "version", "settings", "sessions"):
            check(f"export 含 {key}", key in payload, str(list(payload)[:8]))
        check("export 不含明文 api_key", "sk-" not in r.text or "***" in r.text)

        # ---------- 停止 ----------
        r = c.post("/api/stop", json={"session_id": sid})
        check("POST stop 200", r.status_code == 200)

        # ---------- 删除会话 ----------
        r = c.delete(f"/api/sessions/{sid}")
        check("DELETE session 200", r.status_code == 200)
        r = c.get(f"/api/sessions/{sid}/messages")
        check("删除后访问 → 404", r.status_code == 404, str(r.status_code))

    print(f"\n结果: {len(passed)} 通过 / {len(failed)} 失败")
    if failed:
        print("失败项：" + "；".join(failed))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
