"""口袋 Agent · agentd —— 手机上的本地智能体服务（单进程）。

启动：
  python -m agentd.main                     # 本机访问，端口 8787
  python -m agentd.main --lan               # 局域网访问（会要求设置访问令牌）
  python -m agentd.main --mock              # 离线 mock 模式（不调用 LLM，用于自测）

安全要点（对比旧实现的修复）：
  - 默认只绑 127.0.0.1；--lan 时强制要求 Bearer token；
  - 工具审批由服务端强制，approve 模式下写/危险操作必须用户确认；
  - GET /api/settings 只返回打码后的 API Key。
"""
from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from sse_starlette.sse import EventSourceResponse, ServerSentEvent

from .agent import Agent
from .config import PRESETS, Settings, mask_key
from .mcp import MCPClient
from .memory import Store
from .notify import notify
from .tools import Tool, register


def _sse_event(ev: dict) -> ServerSentEvent:
    """把事件 dict 编码成与原手写 SSE 逐字节一致的帧：``data: <json>\\n\\n``。

    sse-starlette 若直接 yield dict 会把键当作 SSE 字段名（event/id/retry…），
    无法把整条事件 dict 放进 data 字段，因此这里统一显式构造 ServerSentEvent。
    sep 固定为 "\\n"，与原手写 ``f"data: {...}\\n\\n"`` 完全一致。
    """
    return ServerSentEvent(data=json.dumps(ev, ensure_ascii=False), sep="\n")

VERSION = "0.2.0"
APP_NAME = "口袋 Agent"

DIST = Path(__file__).parent / "web" / "dist"


# ---------- 审批中心 ----------
class ApprovalCenter:
    """每个待审批工具调用一个 asyncio.Event；超时默认拒绝（安全优先）。"""

    def __init__(self):
        self._gates: dict[tuple[str, str], dict] = {}
        self._always: dict[str, set] = {}

    async def ask(self, session_id: str, tool_call_id: str, tool_name: str) -> str:
        if tool_name in self._always.setdefault(session_id, set()):
            return "allow"
        timeout = 120
        try:
            timeout = int(settings_mgr.get().get("server", {}).get("approval_timeout", 120))
        except (TypeError, ValueError):
            pass
        gate = {"event": asyncio.Event(), "decision": None}
        self._gates[(session_id, tool_call_id)] = gate
        try:
            await asyncio.wait_for(gate["event"].wait(), timeout=timeout)
        except asyncio.TimeoutError:
            return "timeout"  # 超时默认拒绝（安全优先）
        finally:
            self._gates.pop((session_id, tool_call_id), None)
        d = gate["decision"]
        if d == "allow_always":
            self._always.setdefault(session_id, set()).add(tool_name)
            return "allow"
        if d == "allow_once":
            return "allow"
        return "deny"

    def decide(self, session_id: str, tool_call_id: str, decision: str) -> bool:
        gate = self._gates.get((session_id, tool_call_id))
        if not gate:
            return False
        gate["decision"] = decision
        gate["event"].set()
        return True

    def forget(self, session_id: str) -> None:
        """会话删除时清理：未决审批 + 「始终允许」记忆。"""
        self._gates = {k: v for k, v in self._gates.items() if k[0] != session_id}
        self._always.pop(session_id, None)


# ---------- 应用 ----------
settings_mgr = Settings()
store: Store | None = None
approval = ApprovalCenter()
runs: dict[str, dict] = {}  # session_id -> {task, queue}
MOCK = False


def create_app() -> FastAPI:
    global store
    app = FastAPI(title=APP_NAME, version=VERSION)
    store = Store(settings_mgr.path.parent / "agent.db")

    def emit_now(session_id: str):
        q = runs.get(session_id, {}).get("queue")
        if q is None:
            return
        async def _emit(ev: dict):
            q.put_nowait(ev)
        return _emit

    async def run_agent(session_id: str, message: str):
        notify("口袋 Agent", f"正在处理：{message[:40]}", persistent=True)
        try:
            # 新会话（尚无 user 消息）自动命名
            if store.first_user_message(session_id) is None:
                try:
                    title = await Agent(store, settings_mgr, mock=MOCK).title_for(message)
                    store.touch_session(session_id, title or "新会话")
                except Exception:  # noqa: BLE001 —— 命名失败不阻塞
                    pass
            agent = Agent(store, settings_mgr, mock=MOCK)

            async def ask(tid: str, name: str, summary: str, risk: str) -> str:
                emit = emit_now(session_id)
                if emit:
                    await emit({"type": "approval", "id": tid, "name": name, "summary": summary, "risk": risk})
                notify("口袋 Agent · 需要确认", f"{summary}（{'危险' if risk == 'danger' else '需要' if risk == 'write' else '只读'}操作），去应用里处理", persistent=True)
                return await approval.ask(session_id, tid, name)

            await agent.chat(session_id, message, emit=emit_now(session_id), ask_approval=ask)
            notify("口袋 Agent · 完成", f"已处理：{message[:30]}", persistent=False)
        except asyncio.CancelledError:
            q = runs.get(session_id, {}).get("queue")
            if q:
                q.put_nowait({"type": "error", "message": "已停止"})
            notify("口袋 Agent", "已停止")
        except Exception as e:  # noqa: BLE001 —— 服务端兜底，不能静默
            q = runs.get(session_id, {}).get("queue")
            if q:
                q.put_nowait({"type": "error", "message": str(e)})
            notify("口袋 Agent · 出错", str(e)[:80])
        finally:
            run = runs.get(session_id)
            pending = (run or {}).get("pending", [])
            runs.pop(session_id, None)
            # 排队续跑：同一会话期间发来的消息在此自动执行
            if pending:
                nxt = pending.pop(0)
                queue2: asyncio.Queue = asyncio.Queue()
                runs[session_id] = {"task": None, "queue": queue2, "pending": pending}
                task2 = asyncio.create_task(run_agent(session_id, nxt))
                runs[session_id]["task"] = task2

    def cancel_run(session_id: str) -> bool:
        run = runs.get(session_id)
        if run and run.get("task") and not run["task"].done():
            run["task"].cancel()
            return True
        return False

    # ---------- P1：定时/条件触发 ----------
    from .scheduler import TRIGGER_TYPES, JobStore, SchedulerService, parse_condition

    jobs_store = JobStore(settings_mgr.path.parent / "jobs.db")
    mcp_clients: list[MCPClient] = []

    async def scheduled_run(session_id: str, message: str):
        """定时任务触发：指定会话不存在时自动新建；执行结果写入该会话，可在前端回看。"""
        if not session_id or store.get_session(session_id) is None:
            session_id = store.create_session()
        runs.setdefault(session_id, {"task": None, "queue": asyncio.Queue(), "pending": []})
        await run_agent(session_id, message)

    scheduler_svc = SchedulerService(jobs_store, run_cb=scheduled_run, notify_cb=lambda t, c: notify(t, c, persistent=False))

    @app.on_event("startup")
    async def _start_scheduler():
        scheduler_svc.start()
        # P1：MCP 服务器可选接入（mock 模式不连，保持演示环境纯净）
        if not MOCK:
            mcp_configs = settings_mgr.get().get("server", {}).get("mcp", []) or []
            for cfg in mcp_configs:
                if not isinstance(cfg, dict) or not cfg.get("command"):
                    continue
                client = MCPClient(
                    str(cfg.get("name", "mcp")).strip() or "mcp",
                    str(cfg["command"]),
                    [str(a) for a in cfg.get("args", []) or []],
                )
                try:
                    tools = await client.connect()
                except Exception as e:  # noqa: BLE001 —— 连接失败不阻塞启动
                    await client.close()
                    continue
                for t in tools:
                    tname = str(t.get("name", ""))
                    if not tname:
                        continue
                    full = f"mcp__{client.name}__{tname}"

                    async def _handler(c=client, tn=tname, **args):
                        return await c.call_tool(tn, args)

                    register(
                        Tool(
                            name=full,
                            description=f"[MCP:{client.name}] {(t.get('description') or tname)[:160]}",
                            parameters=t.get("inputSchema") or {"type": "object", "properties": {}},
                            risk="write",  # 第三方工具默认需审批
                            handler=_handler,
                            summary=f"MCP {client.name} · {tname}",
                            timeout=60,
                            group="mcp",
                        )
                    )
                client.tools = [str(t.get("name", "")) for t in tools if t.get("name")]
                mcp_clients.append(client)

    @app.on_event("shutdown")
    async def _stop_scheduler():
        scheduler_svc.shutdown()
        for c in mcp_clients:
            try:
                await c.close()
            except Exception:  # noqa: BLE001
                pass

    # ---------- 鉴权（局域网开启后强制） ----------
    def check_token(request: Request) -> None:
        import hmac

        s = settings_mgr.get()
        token = str(s.get("server", {}).get("token", ""))
        if not token:
            return
        auth = request.headers.get("authorization", "")
        if not auth.startswith("Bearer "):
            raise HTTPException(status_code=401, detail="需要访问令牌")
        given = auth[len("Bearer "):].strip()
        if not hmac.compare_digest(given, token):
            raise HTTPException(status_code=401, detail="需要访问令牌")

    # ---------- API ----------
    @app.get("/api/health")
    async def health():
        llm = settings_mgr.llm()
        ready = MOCK or bool(llm.get("api_key") and llm.get("model"))
        return {
            "ok": True,
            "app": APP_NAME,
            "version": VERSION,
            "ready": ready,
            "mock": MOCK,
            "active_runs": len(runs),
        }

    @app.post("/api/chat", dependencies=[Depends(check_token)])
    async def chat(payload: dict, request: Request):
        if store is None:
            raise HTTPException(503, "服务未就绪")
        message = payload.get("message")
        if not isinstance(message, str) or not message.strip():
            raise HTTPException(400, "消息不能为空")
        if len(message) > 100000:
            raise HTTPException(400, "消息过长（最多 10 万字符）")

        session_id = payload.get("session_id")
        if not isinstance(session_id, str) or not session_id.strip():
            session_id = store.create_session()
        elif store.get_session(session_id) is None:
            raise HTTPException(404, "会话不存在")

        # 同一会话进行中：消息入队，当前轮结束后由 run_agent → agent.chat 统一落库执行
        # （不在此重复 add_message，否则 agent.chat:166 会再写一次导致重复）
        if session_id in runs:
            runs[session_id].setdefault("pending", []).append(message)
            sid = session_id

            async def queued_sse():
                # 事件字段与原手写 SSE 完全一致，仅改用 sse-starlette 编码
                yield _sse_event({"type": "session", "session_id": sid})
                yield _sse_event(
                    {"type": "queued", "message": "上一轮回复还在进行中，这条已排队，完成后自动执行"}
                )
                yield _sse_event({"type": "done", "stop_reason": "queued"})

            return EventSourceResponse(
                queued_sse(),
                sep="\n",
                headers={"Cache-Control": "no-cache"},
            )

        queue: asyncio.Queue = asyncio.Queue()
        # 先注册再启动任务，避免 run_agent 首帧 emit 时 runs 尚未就绪的竞态
        runs[session_id] = {"task": None, "queue": queue}
        task = asyncio.create_task(run_agent(session_id, message))
        runs[session_id]["task"] = task

        async def sse():
            # 首帧告知会话 ID（新建会话时前端需要）
            yield _sse_event({"type": "session", "session_id": session_id})
            try:
                while True:
                    ev = await queue.get()
                    # 心跳保活改由 EventSourceResponse(ping=15) 内置发送注释帧，
                    # 这里只转发业务事件；审批等待长达 120s 也不会断连。
                    yield _sse_event(ev)
                    if ev.get("type") in ("done", "error"):
                        break
            except asyncio.CancelledError:
                # 客户端断开（SSE 连接关闭）→ 取消正在跑的 agent 任务
                cancel_run(session_id)
                raise

        return EventSourceResponse(
            sse(),
            sep="\n",
            ping=15,  # 每 15s 自动发注释帧保活（替代原手写 _heartbeat）
            headers={
                "Cache-Control": "no-cache",
            },
        )

    @app.post("/api/stop", dependencies=[Depends(check_token)])
    async def stop(payload: dict):
        session_id = payload.get("session_id", "")
        return {"ok": cancel_run(session_id)}

    @app.post("/api/approval", dependencies=[Depends(check_token)])
    async def approval_endpoint(payload: dict):
        session_id = payload.get("session_id", "")
        tool_call_id = payload.get("tool_call_id", "")
        decision = payload.get("decision", "")
        if decision not in ("allow_once", "allow_always", "deny"):
            raise HTTPException(400, "decision 必须是 allow_once / allow_always / deny")
        ok = approval.decide(session_id, tool_call_id, decision)
        if not ok:
            raise HTTPException(404, "该审批已过期或不存在")
        return {"ok": True}

    @app.post("/api/undo", dependencies=[Depends(check_token)])
    async def undo_endpoint(payload: dict):
        """撤销一次已完成的文件类操作（写文件/删文件前已自动备份）。"""
        session_id = payload.get("session_id", "")
        tool_call_id = payload.get("tool_call_id", "")
        if not (session_id and tool_call_id):
            raise HTTPException(400, "需要 session_id 与 tool_call_id")
        from .checkpoints import CheckpointStore, undo

        cs = CheckpointStore(settings_mgr.path.parent / "checkpoints.db")
        try:
            return undo(cs, session_id, tool_call_id)
        finally:
            cs.close()

    @app.put("/api/sessions/{sid}", dependencies=[Depends(check_token)])
    async def rename_session(sid: str, payload: dict):
        title = str(payload.get("title", "")).strip()[:60]
        if not title:
            raise HTTPException(400, "标题不能为空")
        if store is None or store.get_session(sid) is None:
            raise HTTPException(404, "会话不存在")
        store.touch_session(sid, title)
        return {"ok": True, "title": title}

    @app.get("/api/sessions", dependencies=[Depends(check_token)])
    async def list_sessions():
        if store is None:
            return []
        return store.list_sessions()

    @app.post("/api/sessions", dependencies=[Depends(check_token)])
    async def create_session():
        if store is None:
            raise HTTPException(503)
        sid = store.create_session()
        return {"session_id": sid, "title": "新会话"}

    @app.delete("/api/sessions/{sid}", dependencies=[Depends(check_token)])
    async def delete_session(sid: str):
        if store is None:
            raise HTTPException(503)
        cancel_run(sid)
        ok = store.delete_session(sid)
        if ok:
            approval.forget(sid)  # 清理该会话的"始终允许"记忆与未决审批
            from .checkpoints import CheckpointStore

            cs = CheckpointStore(settings_mgr.path.parent / "checkpoints.db")
            cs.delete_session(sid)
            cs.close()
            # 级联清理该会话下的定时任务（内存调度 + 持久化库）
            scheduler_svc.delete_by_session(sid)
        return {"ok": ok}

    @app.get("/api/sessions/{sid}/messages", dependencies=[Depends(check_token)])
    async def session_messages(sid: str):
        if store is None:
            raise HTTPException(503)
        if store.get_session(sid) is None:
            raise HTTPException(404, "会话不存在")
        return store.get_messages(sid)

    # ---------- P1：定时任务 API ----------
    @app.get("/api/jobs", dependencies=[Depends(check_token)])
    async def jobs_list():
        return {"jobs": scheduler_svc.list()}

    @app.post("/api/jobs", dependencies=[Depends(check_token)])
    async def jobs_create(payload: dict):
        name = str(payload.get("name", "")).strip()[:60]
        tt = str(payload.get("trigger_type", "")).strip()
        expr = str(payload.get("expr", "")).strip()
        message = str(payload.get("message", "")).strip()
        if not (name and tt in TRIGGER_TYPES and expr and message):
            raise HTTPException(400, "缺少必要字段：name / trigger_type(cron|interval|date) / expr / message")
        if tt == "interval":
            try:
                int(float(expr))
            except ValueError:
                raise HTTPException(400, "interval 表达式需为秒数（如 3600）")
        if tt == "date":
            try:
                __import__("datetime").datetime.strptime(expr, "%Y-%m-%d %H:%M")
            except ValueError:
                raise HTTPException(400, "date 表达式格式：YYYY-MM-DD HH:MM")
        cond = str(payload.get("condition", "")).strip()
        if cond and parse_condition(cond) is None:
            raise HTTPException(400, "条件格式：battery < 20（仅支持电池电量阈值）")
        job = scheduler_svc.create(
            {
                "name": name,
                "trigger_type": tt,
                "expr": expr,
                "message": message,
                "session_id": str(payload.get("session_id", "")).strip(),
                "condition": cond,
                "enabled": bool(payload.get("enabled", True)),
            }
        )
        return job

    @app.put("/api/jobs/{jid}", dependencies=[Depends(check_token)])
    async def jobs_update(jid: str, payload: dict):
        enabled = payload.get("enabled")
        if not isinstance(enabled, bool):
            raise HTTPException(400, "只需传 enabled: true/false")
        job = scheduler_svc.update(jid, {"enabled": enabled})
        if job is None:
            raise HTTPException(404, "任务不存在")
        return job

    @app.delete("/api/jobs/{jid}", dependencies=[Depends(check_token)])
    async def jobs_delete(jid: str):
        return {"ok": scheduler_svc.delete(jid)}

    @app.get("/api/providers")
    async def providers():
        return {"providers": PRESETS}

    @app.get("/api/tools", dependencies=[Depends(check_token)])
    async def tools():
        from .tools import all_tools

        return {
            "tools": [
                {
                    "name": t.name,
                    "description": t.description,
                    "risk": t.risk,
                    "summary": t.summary,
                    "timeout": t.timeout,
                    "group": t.group,
                }
                for t in all_tools()
            ],
            "count": len(all_tools()),
        }

    @app.get("/api/mcp", dependencies=[Depends(check_token)])
    async def mcp_status():
        cfg = settings_mgr.get().get("server", {}).get("mcp", []) or []
        return {
            "configured": cfg,
            "connected": [
                {
                    "name": c.name,
                    "tools": c.tools if hasattr(c, "tools") and isinstance(c.tools, list) else [],
                }
                for c in mcp_clients
            ],
        }

    @app.get("/api/export", dependencies=[Depends(check_token)])
    async def export_all():
        """导出全部数据（会话 + 消息 + 脱敏配置）为 JSON，用于备份迁移。"""
        import datetime

        out: dict = {
            "app": "口袋 Agent",
            "version": VERSION,
            "exported_at": datetime.datetime.now().isoformat(timespec="seconds"),
            "settings": {
                "permission_mode": settings_mgr.get().get("permission_mode", "approve"),
                "llm": {k: v for k, v in settings_mgr.get().get("llm", {}).items() if k != "api_key"},
                "user_prefs": settings_mgr.get().get("user_prefs", ""),
            },
            "sessions": [],
        }
        if store is not None:
            for s in store.list_sessions(limit=500):
                msgs = [
                    {"role": m["role"], "content": m["content"], "meta": m.get("meta")}
                    for m in store.get_messages(s["id"], limit=1000)
                ]
                out["sessions"].append({"id": s["id"], "title": s["title"], "message_count": len(msgs), "messages": msgs})
        return out

    @app.get("/api/settings", dependencies=[Depends(check_token)])
    async def get_settings():
        s = settings_mgr.get()
        llm = dict(s.get("llm", {}))
        llm["api_key"] = mask_key(llm.get("api_key", ""))
        return {**s, "llm": llm}

    @app.put("/api/settings", dependencies=[Depends(check_token)])
    async def put_settings(payload: dict):
        llm = payload.get("llm")
        if isinstance(llm, dict):
            # api_key 打码占位符：前端回传的是 GET 时打码后的值（或空串），
            # 原样写回会污染真实 key，因此剔除——仅当用户输入了「新的、非占位」的 key 才写入。
            masked = mask_key(settings_mgr.llm().get("api_key", ""))
            key = llm.get("api_key")
            if isinstance(key, str) and (not key.strip() or key == masked):
                llm.pop("api_key", None)
        server = payload.get("server")
        if isinstance(server, dict) and isinstance(server.get("mcp"), list):
            # mcp 条目清洗：只保留带 command 的服务器（与原逻辑一致），结构校验交给 pydantic
            cleaned_mcp = []
            for c in server["mcp"]:
                if isinstance(c, dict) and str(c.get("command", "")).strip():
                    cleaned_mcp.append(
                        {
                            "name": c.get("name", "mcp"),
                            "command": c["command"],
                            "args": c.get("args", []),
                        }
                    )
            server["mcp"] = cleaned_mcp
        # permission_mode 非法值不写入（pydantic Literal 也会拒绝，这里显式兜底）
        mode = payload.get("permission_mode")
        if mode is not None and mode not in ("auto", "approve", "chat"):
            payload.pop("permission_mode", None)
        # 类型强制 / 范围收敛（temperature、max_tokens、approval_timeout、字符串截断等）
        # 全部由 pydantic 模型在 Settings.save() 内完成，不再手写 if/else clamp。
        settings_mgr.save(payload)
        s = settings_mgr.get()
        llm_out = dict(s.get("llm", {}))
        llm_out["api_key"] = mask_key(llm_out.get("api_key", ""))
        return {"ok": True, "settings": {**s, "llm": llm_out}}

    # ---------- 静态前端 ----------
    if DIST.exists():
        app.mount("/", StaticFiles(directory=str(DIST), html=True), name="static")
    else:

        @app.get("/", include_in_schema=False)
        async def root_placeholder():
            return HTMLResponse(
                "<html><body style='font-family:sans-serif;background:#0f1115;color:#e4e4e7;padding:40px'>"
                "<h2>口袋 Agent 服务已启动</h2>"
                "<p>前端尚未构建。在仓库根目录执行：</p>"
                "<pre>cd agentd/web && npm install && npm run build</pre>"
                "</body></html>"
            )

    return app


def main():
    global MOCK
    parser = argparse.ArgumentParser(description="口袋 Agent agentd")
    parser.add_argument("--host", default=None, help="监听地址（默认 127.0.0.1，--lan 时 0.0.0.0）")
    parser.add_argument("--port", type=int, default=8787)
    parser.add_argument("--lan", action="store_true", help="允许局域网访问（会校验访问令牌）")
    parser.add_argument("--mock", action="store_true", help="离线 mock 模式")
    parser.add_argument("--home", default=None, help="数据目录（默认 ~/.agent/termux-agent）")
    args = parser.parse_args()

    MOCK = args.mock
    if args.home:
        import os

        os.environ["AGENT_HOME"] = args.home
    settings_mgr.load()

    host = args.host or ("0.0.0.0" if args.lan else "127.0.0.1")
    if args.lan:
        s = settings_mgr.get()
        token = str(s.get("server", {}).get("token", "")).strip()
        if not token:
            print("✗ 安全策略：--lan 必须配置访问令牌才能启动（否则局域网内任何设备都能调用本服务）。")
            print("  请先设置 server.token（启动本机模式后，在页面「设置 → 局域网访问令牌」填入），再重试。")
            raise SystemExit(1)

    import uvicorn

    print(f"[agentd] 口袋 Agent v{VERSION} | mock={MOCK} | 监听 {host}:{args.port}")
    if MOCK:
        print("[agentd] mock 模式：消息含 电池/短信/定位/剪贴板/传感器/通知/工具 等词会走工具链路，便于自测")
    uvicorn.run(create_app(), host=host, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
