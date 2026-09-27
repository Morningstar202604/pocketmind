"""Agent 循环：流式输出 → 工具调用 → 审批门 → 结果回填 → 继续。

与旧实现（goose 黑盒 + 服务端自动批准一切）的本质区别：
1. 循环是我们自己的，每一步都可见、可中断、可审批；
2. 审批门由服务端强制，危险工具必须用户确认，不再被自动放行；
3. 消息全部落 SQLite，服务重启后上下文从库里恢复，不会"看起来有历史、实际上失忆"。
"""
from __future__ import annotations

import asyncio
import json
import sys
from typing import Awaitable, Callable

from . import permissions
from .memory import Store
from .mock_llm import MockLLM
from .tools import call_tool, get_tool, schemas

MAX_ITERATIONS = 20      # 一轮最多工具调用次数，防死循环
MAX_CONTEXT = 80         # 上下文消息上限，超出做安全裁剪
MAX_TOOL_TEXT = 6000     # 回传给模型的工具结果上限

Emit = Callable[[dict], Awaitable[None]]
AskApproval = Callable[[str, str, str, str], Awaitable[str]]  # (tool_call_id, name, summary, risk) -> allow|deny

# 固定 system 前缀：内容恒定不变，位于请求最前，配合厂商「提示缓存」机制
# （相同前缀命中后 token 费约 1 折，DeepSeek/豆包/百炼/Kimi 均支持）。
# 注意：勿随意改动这里，改动会降低缓存命中率。
SYSTEM_FIXED = (
    "你是「口袋 Agent」，运行在用户 Android 手机的 Termux 环境里，可以调用手机能力"
    "（短信、电话、定位、传感器、WiFi、通知、剪贴板、文件、shell 等）。\n"
    "原则：\n"
    "1. 用用户的语言回答（默认简体中文），简洁直接，不写废话。\n"
    "2. 需要用户信息时先调用工具获取，不臆测；拿不到就如实说明。\n"
    "3. 工具返回后，用自然语言把结果讲清楚。\n"
    "4. 危险操作（删除、拨号、写文件、shell）会由用户审批，你只负责发起，不要绕过审批。"
)


def _fallback_title(message: str) -> str:
    """回退标题：取首条消息前 14 字（与 mock 规则一致）。"""
    text = " ".join(str(message).split())
    return text[:14] + ("…" if len(text) > 14 else "")


def _trim(messages: list[dict], maxn: int = MAX_CONTEXT) -> list[dict]:
    """裁剪早期消息，保证 tool 消息永远跟着它的 assistant tool_call 一起被裁掉；
    第一条 system（记忆摘要/角色设定）永远保留。"""
    if len(messages) <= maxn:
        return messages
    drop = len(messages) - maxn
    i = 1 if messages and messages[0]["role"] == "system" else 0  # 保护 system
    while i < len(messages) and drop > 0:
        m = messages[i]
        if m["role"] == "tool":
            i += 1
            drop -= 1
        elif m["role"] == "assistant" and m.get("tool_calls"):
            i += 1
            drop -= 1
            while i < len(messages) and messages[i]["role"] == "tool":
                i += 1
                drop -= 1
        else:
            i += 1
            drop -= 1
    return messages[:1] + messages[i:] if messages and messages[0]["role"] == "system" and i > 0 else messages[i:]


class Agent:
    def __init__(self, store: Store, settings: dict, mock: bool = False):
        self.store = store
        self.settings = settings
        self.mock = mock

    def _llm(self):
        if self.mock:
            return MockLLM(self.settings)
        from openai import AsyncOpenAI

        llm = self.settings.get().get("llm", {})
        return AsyncOpenAI(
            api_key=llm.get("api_key") or "empty",
            base_url=llm.get("base_url") or None,
            timeout=180,
            max_retries=2,
        )

    async def _stream(self, messages: list[dict], tools: list[dict] | None):
        """统一流式接口：产出 (content, reasoning, tool_calls_delta)。"""
        if self.mock:
            async for chunk in self._llm().chat(messages, tools):
                d = chunk.choices[0].delta
                yield d.content, d.reasoning_content, d.tool_calls
            return
        llm = self.settings.get().get("llm", {})
        client = self._llm()
        kwargs = {
            "model": llm.get("model") or "unknown",
            "messages": messages,
            "stream": True,
        }
        if tools:
            kwargs["tools"] = tools
        temp = llm.get("temperature")
        if isinstance(temp, (int, float)) and 0 <= temp <= 2:
            kwargs["temperature"] = temp
        mt = llm.get("max_tokens")
        if isinstance(mt, int) and mt > 0:
            kwargs["max_tokens"] = min(mt, 65536)
        stream = await client.chat.completions.create(**kwargs)
        async for chunk in stream:
            if not chunk.choices:
                continue
            delta = chunk.choices[0].delta
            if delta is None:
                continue
            reasoning = getattr(delta, "reasoning_content", None) or getattr(delta, "reasoning", None)
            yield delta.content, reasoning, delta.tool_calls

    def _is_reasoning_model(self) -> bool:
        """思考模型启发式识别（DeepSeek-R1/reasoner、通义 thinking、Kimi k3 等）。
        思考模型常不支持 function calling，需要给模型和用户明确提示，避免假装执行工具。"""
        llm = self.settings.get().get("llm", {})
        m = str(llm.get("model", "")).lower()
        return any(k in m for k in ("reasoner", "thinking", "r1", "-think", "k3", "k2.6", "o1", "o3"))

    async def chat(
        self,
        session_id: str,
        message: str,
        emit: Emit,
        ask_approval: AskApproval | None = None,
    ) -> None:
        mode = self.settings.permission_mode()
        tools = [] if mode == "chat" else schemas()

        # 组装上下文（顺序固定，前缀稳定以利提示缓存）：
        #   固定角色 → 用户长期偏好 → 相关历史记忆 → 记忆摘要 → 历史 → 本条消息
        messages: list[dict] = [{"role": "system", "content": SYSTEM_FIXED}]
        prefs = str(self.settings.get().get("user_prefs", "")).strip()
        if prefs:
            messages.append({"role": "system", "content": f"用户长期偏好：{prefs}"})
        if tools and self._is_reasoning_model():
            messages.append(
                {
                    "role": "system",
                    "content": (
                        "注意：你当前是思考模型。如果本环境无法通过 function calling 调用工具，"
                        "请直接说明「当前模型不支持工具调用，建议换用支持工具的非思考模型」，不要假装执行。"
                    ),
                }
            )
        # P1：跨会话相关记忆（用户问的与新任务相关的旧事）
        related = await self.store.search_memories(message, limit=3, exclude_session=session_id)
        if related:
            rel_text = "\n".join(f"· {m['content'][:160]}" for m in related)
            messages.append({"role": "system", "content": f"与此相关的历史记忆：\n{rel_text}"})
        summary = await self.store.get_summary(session_id)
        if summary:
            messages.append({"role": "system", "content": f"此前对话摘要：{summary}"})
        # 恢复历史消息：从 meta 反序列化 tool_calls / tool_call_id，
        # 否则重启后孤立的 role:"tool" 消息会让 OpenAI 兼容 API 返回 400。
        for m in await self.store.get_messages(session_id):
            msg: dict = {"role": m["role"], "content": m["content"] or ""}
            meta = m.get("meta") or {}
            if m["role"] == "assistant" and meta.get("tool_calls"):
                msg["tool_calls"] = [
                    {
                        "id": c["id"],
                        "type": "function",
                        "function": {"name": c["name"], "arguments": c.get("args", "{}")},
                    }
                    for c in meta["tool_calls"]
                ]
            elif m["role"] == "tool" and meta.get("tool_call_id"):
                msg["tool_call_id"] = meta["tool_call_id"]
            messages.append(msg)
        messages.append({"role": "user", "content": message})
        await self.store.add_message(session_id, "user", message)

        for _ in range(MAX_ITERATIONS):
            messages = _trim(messages)
            content_acc, reasoning_acc = "", ""
            tool_calls: dict[int, dict] = {}
            try:
                async for content, reasoning, tc_delta in self._stream(messages, tools):
                    if reasoning:
                        reasoning_acc += reasoning
                        await emit({"type": "thinking", "text": reasoning})
                    if content:
                        content_acc += content
                        await emit({"type": "text", "text": content})
                    if tc_delta:
                        for tc in tc_delta:
                            acc = tool_calls.setdefault(tc.index, {"id": "", "name": "", "args": ""})
                            if tc.id:
                                acc["id"] = tc.id
                            if tc.function:
                                if tc.function.name:
                                    acc["name"] = tc.function.name
                                if tc.function.arguments:
                                    acc["args"] += tc.function.arguments
            except asyncio.CancelledError:
                raise

            if not tool_calls:
                text = content_acc.strip()
                await self.store.add_message(session_id, "assistant", text)
                # 一轮结束后滚动更新记忆摘要（后续对话的开头注入）
                try:
                    await self.store.set_summary(session_id, await self._summarize(session_id, messages))
                except Exception as e:  # noqa: BLE001 —— 摘要失败不影响主流程，但需留痕
                    print(f"[agentd] 记忆摘要更新失败: {e!r}", file=sys.stderr)
                await emit({"type": "done", "stop_reason": "end_turn"})
                return

            # 模型要求调用工具：先落一条带 tool_calls 的 assistant 消息
            assistant_msg: dict = {"role": "assistant", "content": content_acc or None}
            calls_list = [
                {
                    "id": tc["id"] or f"call_{i}",
                    "type": "function",
                    "function": {"name": tc["name"], "arguments": tc["args"] or "{}"},
                }
                for i, tc in sorted(tool_calls.items())
            ]
            if calls_list:
                assistant_msg["tool_calls"] = calls_list
            messages.append(assistant_msg)
            await self.store.add_message(
                session_id, "assistant", content_acc,
                {"tool_calls": [{"id": c["id"], "name": c["function"]["name"], "args": c["function"]["arguments"][:500]} for c in calls_list]},
            )

            for call in calls_list:
                tid, name = call["id"], call["function"]["name"]
                try:
                    args = json.loads(call["function"]["arguments"] or "{}")
                except json.JSONDecodeError:
                    args = {}
                if not isinstance(args, dict):
                    args = {}

                tool = get_tool(name)
                await emit({"type": "tool_start", "id": tid, "name": name, "input": args})

                decision = "allow"
                if ask_approval is not None:
                    if tool is None:
                        # 模型幻觉出的未知工具：approve 模式下同样要求用户确认，不静默执行
                        if mode != "auto":
                            decision = await ask_approval(tid, name, "未知工具，请确认是否放行（通常应拒绝）", "danger")
                    elif permissions.should_ask(tool.risk, mode):
                        summary = tool.summary or name
                        decision = await ask_approval(tid, name, summary, tool.risk)

                if decision != "allow":
                    if decision == "timeout":
                        result = {"denied": "审批超时，已自动拒绝"}
                    else:
                        result = {"denied": "用户拒绝了该操作"}
                    await emit({"type": "tool_update", "id": tid, "name": name, "status": "denied", "result": result})
                else:
                    result = await call_tool(name, args)
                    # 危险文件操作自动备份：剥离 _ckpt、落库、前端显示可撤销
                    ck = result.pop("_ckpt", None)
                    undoable = False
                    if ck and ck.get("backup"):
                        try:
                            from .checkpoints import CheckpointStore

                            cs = CheckpointStore(self.store.path.parent / "checkpoints.db")
                            await cs.start()
                            await cs.record(session_id, tid, name, ck["path"], ck["backup"], ck.get("kind", "restore"))
                            await cs.close()
                            undoable = True
                        except Exception:  # noqa: BLE001
                            pass
                    status = "completed" if "error" not in result else "failed"
                    await emit({"type": "tool_update", "id": tid, "name": name, "status": status, "result": result, "undoable": undoable})

                text = json.dumps(result, ensure_ascii=False)
                messages.append({"role": "tool", "tool_call_id": tid, "content": text[:MAX_TOOL_TEXT]})
                await self.store.add_message(
                    session_id, "tool", text[:MAX_TOOL_TEXT],
                    {"tool_call_id": tid, "name": name, "status": result.get("denied") and "denied" or ("error" in result and "failed" or "completed")},
                )

        await emit({"type": "error", "message": "工具调用次数过多，已自动停止"})

    # ---------- 会话自动命名 ----------
    async def title_for(self, message: str) -> str:
        """给新会话生成标题：真实 LLM 一次小请求（≤14 字），失败回退截断首条消息。"""
        if self.mock:
            return self._llm().title(message)
        llm = self.settings.get().get("llm", {})
        if not (llm.get("api_key") and llm.get("model") and llm.get("base_url")):
            return _fallback_title(message)
        try:
            client = self._llm()
            resp = await client.chat.completions.create(
                model=llm.get("model") or "unknown",
                messages=[
                    {"role": "system", "content": "给对话起一个不超过 14 个字的简洁标题，只输出标题本身，不要引号。"},
                    {"role": "user", "content": message[:2000]},
                ],
                max_tokens=20,
                temperature=0.2,
            )
            title = (resp.choices[0].message.content or "").strip().strip('"「」').splitlines()[0][:20]
            if title:
                return title
        except Exception:  # noqa: BLE001 —— 标题失败不阻塞聊天
            pass
        return _fallback_title(message)

    # ---------- 记忆摘要 ----------
    async def _summarize(self, session_id: str, messages: list[dict]) -> str:
        """把"旧摘要 + 本轮对话"压缩成一段新摘要，滚动更新。"""
        old = (await self.store.get_summary(session_id)) or ""
        recent = messages[-8:]
        if self.mock:
            return self._llm().summarize(recent)
        llm = self.settings.get().get("llm", {})
        if not (llm.get("api_key") and llm.get("model") and llm.get("base_url")):
            return old or "（暂无）"
        try:
            client = self._llm()
            text = "\n".join(
                f"{m['role']}: {(m.get('content') or '')[:400]}" for m in recent if m["role"] in ("user", "assistant")
            )[:3000]
            resp = await client.chat.completions.create(
                model=llm.get("model") or "unknown",
                messages=[
                    {"role": "system", "content": "用 100 字以内总结这段对话要点（用户需求、已执行的动作、结果、待办）。只输出摘要。"},
                    {"role": "user", "content": f"旧摘要：{old}\n\n新对话：\n{text}"},
                ],
                max_tokens=160,
                temperature=0.2,
            )
            s = (resp.choices[0].message.content or "").strip()
            return s[:400] if s else old or "（空摘要）"
        except Exception:  # noqa: BLE001 —— 摘要失败不阻塞主流程，保留旧摘要
            return old or "（空摘要）"
