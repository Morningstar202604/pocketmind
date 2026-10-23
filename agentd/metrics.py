"""运行指标：进程内计数器 + Prometheus 文本格式输出。

## 为什么不用 prometheus_client

多一个依赖，而这个项目已经因为依赖体积在 Termux 上吃过亏（OTLP 拖进 grpcio
要编译 BoringSSL）。需求只是「几个计数器和直方图」，标准库足够。

## 为什么需要它

审计报告 F-012 指出：现在只能回答"服务活着吗"（/api/health），
回答不了"什么慢、为什么慢、哪类工具在失败"。
一个本地 Agent 最常见的问题恰恰是：某个工具反复失败、某类请求越来越慢、
SSE 连接堆积。这些都需要**按端点/按工具维度**的计数。

## 设计

- 纯内存、无锁（asyncio 单线程，不存在多线程竞争）。
- 只记 4 类信号：请求数/耗时、工具调用数/耗时、LLM 调用、错误数。
- 提供 `render_prometheus()` 输出标准文本格式，可直接被 Prometheus 抓取，
  也可以人工 curl 查看。
- 不采集用户数据：路径里只有 session_id 这种不透明的 id，且**不做维度标签**，
  避免把用户 id 暴露给监控系统。
"""
from __future__ import annotations

import time
from collections import defaultdict

# 端点维度：{"GET /api/sessions": [count, total_seconds, max_seconds]}
_requests: dict[str, list[float]] = defaultdict(lambda: [0, 0.0, 0.0])
_status: dict[int, int] = defaultdict(int)
_tools: dict[str, list[float]] = defaultdict(lambda: [0, 0.0, 0.0, 0])  # count, total, max, failures
_llm_calls = 0
_llm_seconds = 0.0
_errors: dict[str, int] = defaultdict(int)
_started_at = time.time()


def normalize_path(path: str) -> str:
    """把 /api/sessions/abc123/messages 收敛成 /api/sessions/{sid}/messages。

    不做这一步，指标基数会随会话数爆炸（每个会话一个独立的时间序列）。
    HMAC/随机 id 也不该进监控系统。
    """
    parts = path.strip("/").split("/")
    out: list[str] = []
    for i, seg in enumerate(parts):
        # sessions/{sid}/... 与 jobs/{jid}、kb/{kid}、memory/{mid}
        if i > 0 and parts[i - 1] in ("sessions", "jobs", "kb", "memory", "recipes") and seg:
            if seg not in ("search", "pending", "profile", "reset", "compact", "todos"):
                out.append("{id}")
                continue
        # sessions/{sid}/messages/{mid}：消息主键同样不能进标签。
        # 漏掉这一段时基数会随消息数线性增长（一个活跃会话几千条），
        # 而且标签里带着可枚举的用户数据 id。
        if i > 0 and parts[i - 1] == "messages" and seg.isdigit():
            out.append("{id}")
            continue
        out.append(seg)
    return "/" + "/".join(out)


def record_request(method: str, path: str, status: int, seconds: float) -> None:
    key = f"{method} {normalize_path(path)}"
    slot = _requests[key]
    slot[0] += 1
    slot[1] += seconds
    if seconds > slot[2]:
        slot[2] = seconds
    _status[status] += 1


def record_tool(name: str, seconds: float, ok: bool) -> None:
    slot = _tools[name]
    slot[0] += 1
    slot[1] += seconds
    if seconds > slot[2]:
        slot[2] = seconds
    if not ok:
        slot[3] += 1


def record_llm(seconds: float) -> None:
    global _llm_calls, _llm_seconds
    _llm_calls += 1
    _llm_seconds += seconds


def record_error(kind: str) -> None:
    _errors[kind] += 1


def snapshot() -> dict:
    """给 /api/stats 用的结构化快照。"""
    total = sum(v[0] for v in _requests.values())
    return {
        "uptime_seconds": round(time.time() - _started_at, 1),
        "requests": {
            "total": int(total),
            "by_endpoint": {
                k: {"count": int(v[0]), "avg_ms": round(v[1] / v[0] * 1000, 1) if v[0] else 0,
                    "max_ms": round(v[2] * 1000, 1)}
                for k, v in _requests.items()
            },
            "by_status": {str(k): v for k, v in sorted(_status.items())},
        },
        "tools": {
            name: {
                "calls": int(v[0]),
                "avg_ms": round(v[1] / v[0] * 1000, 1) if v[0] else 0,
                "max_ms": round(v[2] * 1000, 1),
                "failures": int(v[3]),
            }
            for name, v in _tools.items()
        },
        "llm": {"calls": _llm_calls, "avg_ms": round(_llm_seconds / _llm_calls * 1000, 1) if _llm_calls else 0},
        "errors": dict(_errors),
    }


def _esc(s: str) -> str:
    return s.replace("\\", "\\\\").replace('"', '\\"')


def render_prometheus() -> str:
    """Prometheus 文本格式（version 0.0.4）。

    只输出计数与耗时；不含用户数据（路径已归一化，工具名是固定的注册表键）。
    """
    lines: list[str] = []
    lines.append("# HELP pocketmind_uptime_seconds Process uptime in seconds")
    lines.append("# TYPE pocketmind_uptime_seconds gauge")
    lines.append(f"pocketmind_uptime_seconds {round(time.time() - _started_at, 1)}")

    lines.append("# HELP pocketmind_http_requests_total HTTP requests by endpoint and method")
    lines.append("# TYPE pocketmind_http_requests_total counter")
    for key, v in _requests.items():
        method, _, path = key.partition(" ")
        lines.append(f'pocketmind_http_requests_total{{method="{_esc(method)}",path="{_esc(path)}"}} {int(v[0])}')

    lines.append("# HELP pocketmind_http_request_duration_seconds_max Slowest request per endpoint")
    lines.append("# TYPE pocketmind_http_request_duration_seconds_max gauge")
    for key, v in _requests.items():
        method, _, path = key.partition(" ")
        lines.append(
            f'pocketmind_http_request_duration_seconds_max{{method="{_esc(method)}",path="{_esc(path)}"}} {v[2]:.6f}'
        )

    lines.append("# HELP pocketmind_http_responses_total HTTP responses by status code")
    lines.append("# TYPE pocketmind_http_responses_total counter")
    for code, n in sorted(_status.items()):
        lines.append(f'pocketmind_http_responses_total{{status="{code}"}} {n}')

    lines.append("# HELP pocketmind_tool_calls_total Tool invocations by tool name")
    lines.append("# TYPE pocketmind_tool_calls_total counter")
    for name, v in _tools.items():
        lines.append(f'pocketmind_tool_calls_total{{tool="{_esc(name)}"}} {int(v[0])}')
        lines.append(f'pocketmind_tool_failures_total{{tool="{_esc(name)}"}} {int(v[3])}')
        lines.append(f'pocketmind_tool_duration_seconds_max{{tool="{_esc(name)}"}} {v[2]:.6f}')

    lines.append("# HELP pocketmind_llm_calls_total LLM completion calls")
    lines.append("# TYPE pocketmind_llm_calls_total counter")
    lines.append(f"pocketmind_llm_calls_total {_llm_calls}")
    lines.append("pocketmind_llm_duration_seconds_total " + f"{_llm_seconds:.6f}")

    lines.append("# HELP pocketmind_errors_total Internal errors by kind")
    lines.append("# TYPE pocketmind_errors_total counter")
    for kind, n in _errors.items():
        lines.append(f'pocketmind_errors_total{{kind="{_esc(kind)}"}} {n}')

    return "\n".join(lines) + "\n"


def reset() -> None:
    """测试用：清空所有计数。"""
    _requests.clear()
    _status.clear()
    _tools.clear()
    _errors.clear()
    global _llm_calls, _llm_seconds, _started_at
    _llm_calls = 0
    _llm_seconds = 0.0
    _started_at = time.time()
