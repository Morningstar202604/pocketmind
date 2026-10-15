#!/usr/bin/env python3
"""P6 首次使用回归（未配置模型时的行为）：自起一个「真实模式」服务，全程不需要 LLM。

为什么单独一个套件：这个场景（用户刚装好、还没填 API Key 就发消息）是**必然发生**的首次路径，
而它此前会挂十几分钟（AsyncOpenAI 拿默认 base_url 打 api.openai.com 并重试）。
断言三件事：
  1) 未配置时 /api/health 明确给出 ready=false 与缺失项（前端据此显示「未配置」与指引）；
  2) 发消息在 5 秒内拿到带指引的 error 事件（不是长时间挂起）；
  3) 连续两条消息都得到处理——**不能**因为提前 return 跳过 runs 清理而把会话永久锁成「排队」；
     且用户消息与失败说明都要落库（重开会话能看到完整一问一答）。

自带服务生命周期，无需外部依赖；CI 可直接运行。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _free_port() -> int:
    """向系统要一个空闲端口：固定端口容易被上一个残留实例占用，
    那时测试会静默连到**别人的服务**上（断言全错但不报「端口被占」）。"""
    import socket

    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


PORT = int(os.environ.get("P6_PORT") or _free_port())
BASE = f"http://127.0.0.1:{PORT}"
passed, failed = [], []


def check(name: str, cond: bool, detail: str = ""):
    (passed if cond else failed).append(name)
    print(("  ✅ " if cond else "  ❌ ") + name + (f" — {detail}" if detail and not cond else ""))


def req(method: str, path: str, body=None, timeout: int = 10):
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(BASE + path, data=data, method=method,
                               headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(r, timeout=timeout) as resp:
        return resp.status, json.loads(resp.read().decode())


def chat_stream(sid: str, message: str, timeout: int = 12) -> list[str]:
    """POST /api/chat 并收集 SSE 事件类型（不解析正文，只关心顺序与类型）。"""
    data = json.dumps({"session_id": sid, "message": message}).encode()
    r = urllib.request.Request(BASE + "/api/chat", data=data, method="POST",
                               headers={"Content-Type": "application/json"})
    types: list[str] = []
    with urllib.request.urlopen(r, timeout=timeout) as resp:
        for raw in resp:
            line = raw.decode("utf-8", errors="replace").strip()
            if not line.startswith("data:"):
                continue
            try:
                types.append(json.loads(line[5:].strip())["type"])
            # aqg: top-level boundary —— SSE 坏帧忽略（协议层已带容错，此处仅统计）
            except Exception:  # noqa: BLE001
                continue
            if types[-1] in ("done", "error") and len(types) > 1:
                if "error" in types:
                    break
    return types


def main() -> None:
    print("== P6 首次使用（未配置模型）回归 ==")
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
        env = dict(os.environ)
        env["AGENT_HOME"] = td
        env.setdefault("PATH", "")
        proc = subprocess.Popen(
            [sys.executable, "-m", "agentd.main", "--port", str(PORT)],
            cwd=str(ROOT), env=env,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        )
        try:
            ready = False
            exited = None
            for _ in range(30):
                if proc.poll() is not None:  # 自身进程提前退出（端口被占 / 启动报错）
                    exited = proc.stdout.read().decode("utf-8", "replace")[-400:] if proc.stdout else ""
                    break
                try:
                    st, health = req("GET", "/api/health", timeout=3)
                    if st == 200:
                        ready = True
                        break
                # aqg: top-level boundary —— 服务启动中，健康检查轮询失败属正常
                except Exception:  # noqa: BLE001
                    time.sleep(1)
            check("真实模式服务可启动", ready, f"30 秒内未就绪；进程输出：{exited or '(无)'}")

            st, health = req("GET", "/api/health")
            check("health 明确未就绪且列出缺失项",
                  health.get("ready") is False and "Base URL" in (health.get("llm_missing") or []),
                  f"ready={health.get('ready')} missing={health.get('llm_missing')}")
            check("health 的 llm 组件带缺失明细",
                  str(health.get("components", {}).get("llm", "")).startswith("not_configured"),
                  str(health.get("components", {}).get("llm")))

            st, sess = req("POST", "/api/sessions", {})
            sid = sess["session_id"]

            t0 = time.time()
            types1 = chat_stream(sid, "帮我查电量")
            dt1 = time.time() - t0
            check("第一条消息 5 秒内返回（不再长时间挂起）", dt1 < 5, f"{dt1:.1f}s")
            check("返回 error 事件且无 queued",
                  "error" in types1 and "queued" not in types1, f"types={types1}")

            t0 = time.time()
            types2 = chat_stream(sid, "再来一条")
            dt2 = time.time() - t0
            check("第二条消息同样被处理（会话未被永久锁成排队）",
                  "error" in types2 and "queued" not in types2 and dt2 < 5,
                  f"types={types2} dt={dt2:.1f}s")

            st, msgs = req("GET", f"/api/sessions/{sid}/messages")
            roles = [m["role"] for m in msgs]
            check("历史落库为完整一问一答（user + assistant 说明）",
                  roles.count("user") == 2 and roles.count("assistant") == 2, f"roles={roles}")
            check("失败说明含可执行指引",
                  all("设置" in (m["content"] or "") for m in msgs if m["role"] == "assistant"),
                  str([m["content"][:40] for m in msgs if m["role"] == "assistant"]))
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()

    print(f"\n结果: {len(passed)} 通过 / {len(failed)} 失败")
    if failed:
        print("失败项：" + "；".join(failed))
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
