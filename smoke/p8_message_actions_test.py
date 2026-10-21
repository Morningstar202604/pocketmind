#!/usr/bin/env python3
"""P8 消息级操作回归：从某条消息起截断历史（编辑重发 / 重新生成共用）。

为什么必须按 id 截断而不是删单条：对话历史必须保持 user → assistant → tool
配对完整。删掉中间一条会留下孤立 tool 响应或没有提问的回答，下一轮组装上下文
时模型看到的就是错乱对话。

需要 mock 服务在 :8799（或 AGENTD_BASE）。
"""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

BASE = os.environ.get("AGENTD_BASE", "http://127.0.0.1:8799")
passed, failed = [], []


def check(name: str, cond: bool, detail: str = ""):
    (passed if cond else failed).append(name)
    print(("  ✅ " if cond else "  ❌ ") + name + (f" — {detail}" if detail and not cond else ""))


def req(method: str, path: str, body: dict | None = None):
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(BASE + path, data=data, method=method,
                               headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(r, timeout=30) as resp:
            return resp.status, json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode() or "{}")


def chat(message: str, sid: str) -> None:
    body = json.dumps({"session_id": sid, "message": message}).encode()
    r = urllib.request.Request(BASE + "/api/chat", data=body, method="POST",
                               headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(r, timeout=90) as resp:
        for _ in resp:
            pass


def main() -> int:
    print("== P8 消息级操作 ==")
    _, s = req("POST", "/api/sessions", {})
    sid = s["session_id"]

    chat("第一条", sid)
    chat("第二条", sid)
    _, msgs = req("GET", f"/api/sessions/{sid}/messages")
    check("两轮对话共 4 条消息", len(msgs) == 4, str(len(msgs)))

    users = [m for m in msgs if m["role"] == "user"]
    check("消息带 id 字段（前端截断依赖它）", all("id" in m for m in msgs))
    second_user = users[1]

    # 从第二条用户消息起截断 → 应当只剩第一轮
    code, r = req("DELETE", f"/api/sessions/{sid}/messages/{second_user['id']}")
    check("截断返回 200", code == 200, str(code))
    check("报告删除 2 条", r.get("removed") == 2, str(r.get("removed")))

    _, msgs2 = req("GET", f"/api/sessions/{sid}/messages")
    check("剩 2 条（第一轮完整保留）", len(msgs2) == 2, str(len(msgs2)))
    check("剩下的仍是 user→assistant 配对",
          [m["role"] for m in msgs2] == ["user", "assistant"], str([m["role"] for m in msgs2]))
    check("首条内容未被误删", msgs2[0]["content"] == "第一条", msgs2[0]["content"])

    # 截断后还能继续对话（上下文没有错乱到不可用）
    chat("第三条", sid)
    _, msgs3 = req("GET", f"/api/sessions/{sid}/messages")
    check("截断后可继续对话（4 条）", len(msgs3) == 4, str(len(msgs3)))

    # 越界 id：不存在的会话必须 404，而不是静默成功
    code, _ = req("DELETE", "/api/sessions/nope/messages/1")
    check("不存在的会话返回 404", code == 404, str(code))

    # ---- 消息正文搜索：标题里没有的内容也必须能搜到 ----
    _, s2 = req("POST", "/api/sessions", {})
    sid2 = s2["session_id"]
    chat("记住这个暗号：蓝色海豚七号", sid2)
    _, hit = req("GET", "/api/sessions/search?q=" + urllib.parse.quote("蓝色海豚"))
    check("搜索命中正文（不是标题）", hit.get("count", 0) >= 1, str(hit.get("count")))
    check("命中项带摘要片段", bool((hit.get("items") or [{}])[0].get("snippet")))
    check("命中项带所属会话 id", (hit.get("items") or [{}])[0].get("session_id") == sid2)
    _, miss = req("GET", "/api/sessions/search?q=" + urllib.parse.quote("绝不存在的词xyz"))
    check("无匹配时返回 0 而不是报错", miss.get("count") == 0, str(miss.get("count")))
    _, empty = req("GET", "/api/sessions/search?q=")
    check("空查询返回空列表", empty.get("items") == [], str(empty))
    req("DELETE", f"/api/sessions/{sid2}")

    req("DELETE", f"/api/sessions/{sid}")
    print(f"\n结果: {len(passed)} 通过 / {len(failed)} 失败")
    if failed:
        print("失败项：" + "；".join(failed))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
