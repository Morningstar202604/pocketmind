#!/usr/bin/env python3
"""P31 真实 OpenAI 兼容链路（不用 mock LLM，起一个本地兼容端点）。

为什么需要它：`--mock` 走的是 MockLLM，完全不经过 openai SDK、不经过 HTTP、
不经过 SSE 解析——也就是说「用户配置真实模型」这条最重要的路径，
在门禁里从来没有被端到端覆盖过。真实事故（本轮审计）：
- 流式请求从不带 `stream_options.include_usage`，token 永远统计不到；
- 端点不支持该参数时会把整个请求打挂（必须降级重试）。

本套件起一个本地 HTTP 服务，按 OpenAI 协议返回分片 SSE（含 usage 尾帧），
让 Agent 用**真实 AsyncOpenAI 客户端**跑完整一轮：
  1. 文本流式输出 → 落库 → done 带真实 usage；
  2. 工具调用分片（id 在第一片、arguments 逐片拼接）→ 执行 → 回填；
  3. 端点拒绝 stream_options（400）时自动降级，仍能完成对话。
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

_HOME = tempfile.mkdtemp(prefix="p31-")
os.environ["AGENT_HOME"] = _HOME
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

passed, failed = [], []


def check(name: str, cond: bool, detail: str = ""):
    (passed if cond else failed).append(name)
    print(("  ✅ " if cond else "  ❌ ") + name + (f" — {detail}" if detail and not cond else ""))


class State:
    requests: list[dict] = []
    reject_stream_options = False
    scenario = "text"  # text | tool


def _sse(obj: dict) -> bytes:
    return f"data: {json.dumps(obj, ensure_ascii=False)}\n\n".encode()


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):  # noqa: D401 —— 静音
        return

    def do_POST(self):  # noqa: N802
        length = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(length) or b"{}")
        State.requests.append(body)
        if State.reject_stream_options and body.get("stream_options"):
            payload = json.dumps({"error": {"message": "stream_options is not supported"}}).encode()
            self.send_response(400)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
            return

        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Transfer-Encoding", "chunked")
        self.end_headers()

        def send(chunk: dict):
            data = _sse(chunk)
            self.wfile.write(f"{len(data):X}\r\n".encode() + data + b"\r\n")
            self.wfile.flush()

        model = body.get("model", "local-test")
        base = {"id": "chatcmpl-test", "object": "chat.completion.chunk", "created": 1, "model": model}
        has_tool_result = any(m.get("role") == "tool" for m in body.get("messages", []))

        if State.scenario == "tool" and not has_tool_result:
            send({**base, "choices": [{"index": 0, "delta": {"role": "assistant", "content": None,
                  "tool_calls": [{"index": 0, "id": "call_1", "type": "function",
                                  "function": {"name": "list_dir", "arguments": ""}}]}, "finish_reason": None}]})
            for piece in ('{"path":', ' "."}'):
                send({**base, "choices": [{"index": 0, "delta": {"tool_calls": [
                    {"index": 0, "function": {"arguments": piece}}]}, "finish_reason": None}]})
            send({**base, "choices": [{"index": 0, "delta": {}, "finish_reason": "tool_calls"}]})
        else:
            for word in ("真实链路", "已连通", "。"):
                send({**base, "choices": [{"index": 0, "delta": {"content": word}, "finish_reason": None}]})
            send({**base, "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]})

        # usage 尾帧（OpenAI 协议：choices 为空数组）
        send({**base, "choices": [], "usage": {"prompt_tokens": 321, "completion_tokens": 45,
                                               "total_tokens": 366}})
        self.wfile.write(b"0\r\n\r\n")
        self.wfile.flush()


def start_server() -> tuple[ThreadingHTTPServer, int]:
    srv = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, port


def make_agent(port: int):
    from agentd.config import Settings

    cfg = Settings()
    cfg.data["llm"] = {
        "provider": "custom",
        "base_url": f"http://127.0.0.1:{port}/v1",
        "api_key": "test-key-not-real",
        "model": "local-test",
        "temperature": 0.1,
        "max_tokens": 256,
    }
    cfg.data["permission_mode"] = "auto"
    return cfg


def run_case(name: str, expect_tool: bool = False) -> None:
    import asyncio

    from agentd.agent import Agent
    from agentd.memory import Store

    print(f"== 场景：{name} ==")
    State.requests = []  # 每个场景独立统计请求，否则上一场景的请求会污染断言
    srv, port = start_server()
    try:
        async def main():
            cfg = make_agent(port)
            store = Store(Path(_HOME) / f"case-{int(time.time()*1000)}.db")
            await store.start()
            sid = await store.create_session()
            agent = Agent(store, cfg, mock=False)
            events: list[dict] = []

            async def emit(ev):
                events.append(ev)

            await agent.chat(sid, "你好，做个测试", emit=emit, ask_approval=None)
            msgs = await store.get_messages(sid)
            await agent.aclose()
            await store.close()
            return events, msgs

        events, msgs = asyncio.run(main())
        texts = "".join(e.get("text", "") for e in events if e.get("type") == "text")
        done = next((e for e in events if e.get("type") == "done"), {})
        if name == "端点拒绝 stream_options 时降级":
            check("降级后仍完成对话", "真实链路" in texts, texts[:80])
            check("降级重试丢弃了 stream_options",
                  all(not r.get("stream_options") for r in State.requests[1:]),
                  str([bool(r.get("stream_options")) for r in State.requests]))
            return
        check("文本流式输出到达前端", "真实链路已连通。" in texts, texts[:80])
        usage = done.get("usage") or {}
        # 工具场景有两次 LLM 调用（发 tool_call + 收尾），usage 按轮累计 → 321/45 的两倍。
        # 这不是 bug：用户看到的是「这一轮总共花了多少」，所以要累加。
        want_prompt = 642 if expect_tool else 321
        want_completion = 90 if expect_tool else 45
        check(
            f"done 带真实累计 usage（{want_prompt}/{want_completion}）",
            usage.get("prompt_tokens") == want_prompt
            and usage.get("completion_tokens") == want_completion
            and usage.get("total_tokens") == want_prompt + want_completion,
            str(usage),
        )
        check("usage 不是 estimated", not usage.get("estimated"), str(usage))
        check("请求带 include_usage", any(r.get("stream_options", {}).get("include_usage") for r in State.requests))
        check("助手回复已落库", any(m["role"] == "assistant" and "真实链路" in m["content"] for m in msgs),
              str([(m["role"], m["content"][:20]) for m in msgs]))
        if expect_tool:
            check("工具调用被执行", any(m["role"] == "tool" for m in msgs),
                  str([(m["role"], m["content"][:30]) for m in msgs]))
            check("工具调用参数分片被拼接", any(
                e.get("type") == "tool_start" and e.get("name") == "list_dir" for e in events), str(events[:3]))
    finally:
        srv.shutdown()


def main() -> int:
    print("== P31 真实 OpenAI 兼容链路 ==")
    State.scenario = "text"
    run_case("文本流式 + usage")
    State.scenario = "tool"
    run_case("工具调用分片", expect_tool=True)
    State.scenario = "text"
    State.reject_stream_options = True
    try:
        run_case("端点拒绝 stream_options 时降级")
    finally:
        State.reject_stream_options = False

    print(f"\n结果: {len(passed)} 通过 / {len(failed)} 失败")
    if failed:
        print("失败项：" + "；".join(failed))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
