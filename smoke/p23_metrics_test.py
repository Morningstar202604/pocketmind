#!/usr/bin/env python3
"""P23 运行指标：计数器正确性 + Prometheus 文本格式 + 不泄露用户数据。

审计 F-012：之前只能回答"服务活着吗"，回答不了"什么慢、哪个工具在失败"。
本测试覆盖：
1. 计数器语义（请求数/状态码/工具成功失败/LLM）
2. 端点路径归一化（否则指标基数随会话数爆炸）
3. Prometheus 文本格式可被解析（label 转义、类型声明）
4. **不包含会话 id**（避免把用户数据灌进监控系统）
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

passed, failed = [], []


def check(name: str, cond: bool, detail: str = ""):
    (passed if cond else failed).append(name)
    print(("  ✅ " if cond else "  ❌ ") + name + (f" — {detail}" if detail and not cond else ""))


def main() -> int:
    print("== P23 运行指标 ==")
    from agentd import metrics as m

    m.reset()

    # ---------- 路径归一化 ----------
    check("会话 id 被归一化",
          m.normalize_path("/api/sessions/abc123def/messages") == "/api/sessions/{id}/messages",
          m.normalize_path("/api/sessions/abc123def/messages"))
    check("jobs id 被归一化",
          m.normalize_path("/api/jobs/job_xyz") == "/api/jobs/{id}",
          m.normalize_path("/api/jobs/job_xyz"))
    check("search 不被当成 id",
          m.normalize_path("/api/sessions/search") == "/api/sessions/search",
          m.normalize_path("/api/sessions/search"))
    check("profile 不被当成 id",
          m.normalize_path("/api/memory/profile") == "/api/memory/profile",
          m.normalize_path("/api/memory/profile"))
    check("静态路径保持不变", m.normalize_path("/api/health") == "/api/health")

    # ---------- 计数 ----------
    m.record_request("GET", "/api/sessions/aaa/messages", 200, 0.05)
    m.record_request("GET", "/api/sessions/bbb/messages", 200, 0.15)
    m.record_request("POST", "/api/chat", 500, 1.2)
    snap = m.snapshot()
    by_ep = snap["requests"]["by_endpoint"]
    key = "GET /api/sessions/{id}/messages"
    check("两个不同会话归并到同一时间序列", by_ep[key]["count"] == 2, str(by_ep.keys()))
    check("平均耗时正确", abs(by_ep[key]["avg_ms"] - 100.0) < 1, str(by_ep[key]))
    check("最大耗时取最大值", abs(by_ep[key]["max_ms"] - 150.0) < 1, str(by_ep[key]))
    check("请求总数正确", snap["requests"]["total"] == 3, str(snap["requests"]["total"]))
    check("状态码分布正确",
          snap["requests"]["by_status"].get("200") == 2 and snap["requests"]["by_status"].get("500") == 1,
          str(snap["requests"]["by_status"]))

    # ---------- 工具 ----------
    m.record_tool("read_file", 0.01, True)
    m.record_tool("read_file", 0.03, False)
    m.record_tool("run_shell", 2.5, True)
    snap = m.snapshot()
    rf = snap["tools"]["read_file"]
    check("工具调用次数", rf["calls"] == 2, str(rf))
    check("工具失败次数", rf["failures"] == 1, str(rf))
    check("工具最大耗时", abs(rf["max_ms"] - 30.0) < 1, str(rf))

    # ---------- LLM ----------
    m.record_llm(0.5)
    m.record_llm(1.5)
    snap = m.snapshot()
    check("LLM 调用次数", snap["llm"]["calls"] == 2, str(snap["llm"]))
    check("LLM 平均耗时", abs(snap["llm"]["avg_ms"] - 1000.0) < 1, str(snap["llm"]))

    # ---------- Prometheus 格式 ----------
    text = m.render_prometheus()
    check("含 TYPE 声明", "# TYPE pocketmind_http_requests_total counter" in text)
    check("含 uptime gauge", "pocketmind_uptime_seconds" in text)
    check("含端点 label", 'path="/api/sessions/{id}/messages"' in text, text[:200])
    check("含状态码 label", 'status="500"' in text)
    check("含工具 label", 'tool="read_file"' in text)
    check("每行以换行结尾", text.endswith("\n"))
    # 格式校验：非注释行必须是 name{labels} value 或 name value
    bad = []
    for line in text.splitlines():
        if not line or line.startswith("#"):
            continue
        import re
        # label 的**值**里可以含花括号（我们的路径归一化就产出 /sessions/{id}/messages），
        # 所以不能简单地用 [^}]* —— 按"名字 + 可选的 {...} + 空白 + 数字"来判，
        # 花括号配对交给一个宽松模式即可（这里只校验整体形状，不实现完整 Prometheus 解析）。
        if not re.match(r'^[a-zA-Z_:][a-zA-Z0-9_:]*(\{.*\})?\s+[-+0-9.eE]+$', line):
            bad.append(line)
    check("所有样本行格式合法", not bad, str(bad[:3]))

    # ---------- 不含用户数据 ----------
    check("指标文本不含原始会话 id", "abc123def" not in text and "job_xyz" not in text, "泄露了 id")

    # ---------- 转义 ----------
    m.reset()
    m.record_request("GET", '/api/weird"quote', 200, 0.01)
    text = m.render_prometheus()
    check("label 中的双引号被转义", '\\"' in text, text[:200])

    print(f"\n结果: {len(passed)} 通过 / {len(failed)} 失败")
    if failed:
        print("失败项：" + "；".join(failed))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
