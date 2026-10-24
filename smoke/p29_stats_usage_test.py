#!/usr/bin/env python3
"""P29 运行统计与 Token 用量：/api/stats 必须是真数据，不是空壳。

真实缺陷（本轮审计发现）：
- `metrics.snapshot()` 写好了但**没有任何调用方**，`/api/stats` 只回显配置项，
  于是「运行统计」面板永远没有请求数 / 工具失败率 / LLM 调用数；
- 流式调用从不请求 `stream_options.include_usage`，也不解析 chunk.usage，
  所以 token 消耗完全不可见——用户无法判断一次任务花了多少钱。

本套件锁定三件事：
1. metrics 能累计 prompt/completion tokens，并出现在 snapshot 与 Prometheus 输出；
2. mock 对话的 done 事件带 usage（前端据此在消息上显示 token）；
3. /api/stats 返回 metrics 快照（请求数、工具调用、LLM token）。
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

_HOME = tempfile.mkdtemp(prefix="p29-")
os.environ["AGENT_HOME"] = _HOME
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

passed, failed = [], []


def check(name: str, cond: bool, detail: str = ""):
    (passed if cond else failed).append(name)
    print(("  ✅ " if cond else "  ❌ ") + name + (f" — {detail}" if detail and not cond else ""))


def main() -> int:
    print("== P29 运行统计与 Token 用量 ==")
    from agentd import metrics

    metrics.reset()
    metrics.record_llm(0.5, prompt_tokens=120, completion_tokens=30)
    metrics.record_llm(0.25, prompt_tokens=80, completion_tokens=20)
    snap = metrics.snapshot()
    llm = snap["llm"]
    check("snapshot 记录 LLM 调用次数", llm["calls"] == 2, str(llm))
    check("snapshot 记录 prompt tokens", llm["prompt_tokens"] == 200, str(llm))
    check("snapshot 记录 completion tokens", llm["completion_tokens"] == 50, str(llm))
    check("snapshot 记录 total tokens", llm["total_tokens"] == 250, str(llm))
    prom = metrics.render_prometheus()
    check("Prometheus 暴露 prompt token 计数", "pocketmind_llm_prompt_tokens_total 200" in prom)
    check("Prometheus 暴露 completion token 计数", "pocketmind_llm_completion_tokens_total 50" in prom)

    # 兼容旧调用：只传耗时不报错（p23 仍按旧签名调用）
    metrics.record_llm(0.1)
    check("record_llm 向后兼容单参调用", metrics.snapshot()["llm"]["calls"] == 3)

    # 端点层：/api/stats 必须包含 metrics 快照
    import agentd.main as M

    M.MOCK = True
    from fastapi.testclient import TestClient

    app = M.create_app()
    with TestClient(app) as c:
        r = c.get("/api/stats")
        check("GET /api/stats 200", r.status_code == 200, str(r.status_code))
        body = r.json()
        check("stats 含 metrics 快照", isinstance(body.get("metrics"), dict), str(list(body)))
        check("stats.metrics 含 requests", "requests" in body.get("metrics", {}))
        check("stats.metrics 含 llm", "llm" in body.get("metrics", {}))
        check("stats 不再只有配置回显", "use_litellm" in body and "metrics" in body)

        # mock 一轮对话：done 事件必须带 usage
        sid = c.post("/api/sessions").json()["session_id"]
        with c.stream("POST", "/api/chat", json={"session_id": sid, "message": "你好"}) as resp:
            payloads = []
            for line in resp.iter_lines():
                if line.startswith("data: "):
                    import json as _json

                    payloads.append(_json.loads(line[6:]))
        done = next((p for p in payloads if p.get("type") == "done"), {})
        check("mock 对话 done 带 usage", isinstance(done.get("usage"), dict), str(done))
        usage = done.get("usage") or {}
        check(
            "usage 含 prompt/completion/total",
            {"prompt_tokens", "completion_tokens", "total_tokens"} <= set(usage),
            str(usage),
        )
        check("usage 数值非负且 total 一致",
              usage.get("total_tokens") == (usage.get("prompt_tokens", 0) + usage.get("completion_tokens", 0)),
              str(usage))

    print(f"\n结果: {len(passed)} 通过 / {len(failed)} 失败")
    if failed:
        print("失败项：" + "；".join(failed))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
