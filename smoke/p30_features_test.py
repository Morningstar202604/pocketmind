#!/usr/bin/env python3
"""P30 会话置顶 / 定时任务立即执行 / 备份导入恢复。

这三项都是「用户以为有、实际没有」的功能缺口（本轮补齐）：
1. 会话列表只按最近活跃排序 —— 重要会话被新对话压到看不见的地方。现在有 pinned 列。
2. 定时任务只能等触发点 —— 用户想验证「这条任务到底跑不跑得通」只能干等。
   现在有 POST /api/jobs/{id}/run（后台起跑，HTTP 立即返回）。
3. 导出有、导入没有 —— 「完整备份」的承诺只完成了一半，换机恢复只能手抄。
   现在有 POST /api/import（合并语义，不覆盖本机数据）。

另外验证导入的事务安全：坏文件返回 400 且数据库不留半截会话。
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

_HOME = tempfile.mkdtemp(prefix="p30-")
os.environ["AGENT_HOME"] = _HOME
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

passed, failed = [], []


def check(name: str, cond: bool, detail: str = ""):
    (passed if cond else failed).append(name)
    print(("  ✅ " if cond else "  ❌ ") + name + (f" — {detail}" if detail and not cond else ""))


def main() -> int:
    print("== P30 置顶 / 立即执行 / 导入恢复 ==")
    import agentd.main as M

    M.MOCK = True
    from fastapi.testclient import TestClient

    app = M.create_app()
    with TestClient(app) as c:
        # ---------- 1. 会话置顶 ----------
        sid_a = c.post("/api/sessions").json()["session_id"]
        sid_b = c.post("/api/sessions").json()["session_id"]
        c.put(f"/api/sessions/{sid_a}", json={"title": "重要会话"})
        c.put(f"/api/sessions/{sid_b}", json={"title": "普通会话"})

        r = c.put(f"/api/sessions/{sid_a}", json={"pinned": True})
        check("PUT pinned=true 返回 200", r.status_code == 200, str(r.status_code))
        check("响应用 pinned=true", r.json().get("pinned") is True, str(r.json()))

        lst = c.get("/api/sessions").json()
        check("置顶会话排在第一位", lst[0]["id"] == sid_a, str([x["id"] for x in lst]))
        check("列表带 pinned 字段", "pinned" in lst[0], str(lst[0]))

        # 只提交 pinned 不能清空标题
        c.put(f"/api/sessions/{sid_a}", json={"pinned": False})
        after = {s["id"]: s for s in c.get("/api/sessions").json()}[sid_a]
        check("只改 pinned 不会清空标题", after["title"] == "重要会话", str(after))

        r = c.put(f"/api/sessions/{sid_a}", json={})
        check("空 body 被拒（400）", r.status_code == 400, str(r.status_code))
        r = c.put("/api/sessions/nonexistent", json={"pinned": True})
        check("不存在的会话 404", r.status_code == 404, str(r.status_code))

        # ---------- 2. 定时任务立即执行 ----------
        r = c.post("/api/jobs", json={
            "name": "手动触发测试", "trigger_type": "interval", "expr": "3600",
            "message": "你好", "enabled": True,
        })
        check("创建定时任务 200", r.status_code == 200, str(r.status_code))
        jid = r.json()["id"]
        r = c.post(f"/api/jobs/{jid}/run")
        check("立即执行返回 200", r.status_code == 200, str(r.status_code))
        check("立即执行响应含 job_id", r.json().get("job_id") == jid, str(r.json()))
        r = c.post("/api/jobs/nonexistent/run")
        check("不存在的任务 404", r.status_code == 404, str(r.status_code))

        # ---------- 3. 导出 → 导入 ----------
        c.post("/api/chat", json={"session_id": sid_a, "message": "备份前的消息"})
        backup = c.get("/api/export").json()
        check("导出含 sessions", isinstance(backup.get("sessions"), list))
        check("导出含 pinned 字段", any("pinned" in s for s in backup["sessions"]), str(backup["sessions"][:1]))

        # 新实例（模拟换机）：用全新数据目录启动另一个 app
        # 直接删会话再导入回来，等价于「恢复被删数据」
        c.delete(f"/api/sessions/{sid_a}")
        check("删除后会话不存在", c.get(f"/api/sessions/{sid_a}/messages").status_code == 404)
        r = c.post("/api/import", json=backup)
        check("导入备份 200", r.status_code == 200, str(r.status_code))
        stats = r.json().get("imported", {})
        check("导入报告恢复出会话", stats.get("sessions", 0) >= 1, str(stats))
        check("导入报告恢复出消息", stats.get("messages", 0) >= 1, str(stats))
        msgs = c.get(f"/api/sessions/{sid_a}/messages")
        check("恢复后消息可读", msgs.status_code == 200 and len(msgs.json()) >= 1, str(msgs.status_code))

        # 幂等：再导一次不重复写
        r2 = c.post("/api/import", json=backup)
        check("重复导入幂等（跳过已存在会话）",
              r2.json().get("imported", {}).get("sessions_skipped", 0) >= 1,
              str(r2.json()))

        # 坏文件：400 且不写坏库
        r = c.post("/api/import", json={"foo": "bar"})
        check("坏备份文件 400", r.status_code == 400, str(r.status_code))
        r = c.post("/api/import", json={"sessions": "not-a-list"})
        check("sessions 不是数组时 400", r.status_code == 400, str(r.status_code))
        still = c.get(f"/api/sessions/{sid_a}/messages")
        check("坏导入不影响已有数据", still.status_code == 200, str(still.status_code))

    print(f"\n结果: {len(passed)} 通过 / {len(failed)} 失败")
    if failed:
        print("失败项：" + "；".join(failed))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
