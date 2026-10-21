"""离线 Mock LLM：不消耗 API 额度，用于端到端自测与演示。

用法：python -m agentd.main --mock。
触发规则：用户消息含前缀/关键词 → 调用对应工具（流式 reasoning → tool_call）→ 下一轮返回收尾文本。
  支持正则 prefix 匹配（"查一下电池电量" 匹配 "电池"前缀），减少漏触发。
流式输出：文本按字/词逐步 yield（模拟真实 LLM 打字效果），思考步骤先输出 reasoning 再 tool_call。
"""
from __future__ import annotations

import json
import re
from types import SimpleNamespace


class _Delta:
    __slots__ = ("content", "reasoning_content", "tool_calls")

    def __init__(self, content: str | None = None, reasoning: str | None = None, tool_calls: list | None = None):
        self.content = content
        self.reasoning_content = reasoning
        self.tool_calls = tool_calls


class _Chunk:
    __slots__ = ("choices",)

    def __init__(self, delta: _Delta):
        self.choices = [SimpleNamespace(delta=delta)]


def _tool_call(index: int, call_id: str, name: str, args: dict) -> SimpleNamespace:
    return SimpleNamespace(
        index=index,
        id=call_id,
        function=SimpleNamespace(name=name, arguments=json.dumps(args, ensure_ascii=False)),
    )


def _stream_text(text: str):
    """把一段文本按「词」粒度切片，逐个 yield 以提供流式打字效果。

    简单分词：中文逐字、英文按空格、标点独立。
    """
    # 按 tokenize：中文字符独立、英文单词整体、标点独立
    tokens = re.findall(r"[\u4e00-\u9fff]|[a-zA-Z0-9_]+|[^\s\u4e00-\u9fff\w]|\s+", text)
    yield from tokens


class MockLLM:
    def __init__(self, settings: dict):
        self.settings = settings
        # (匹配模式, 工具名, 参数)：支持 str（含.Contains）或 compiled regex
        # 顺序即优先级；第一个命中的工具被调用
        self._TOOLS: list[tuple, str, dict] = [
            ("电池", "get_battery", {}),
            ("短信", "read_sms", {"limit": 5}),
            (re.compile(r"定位|位置|在哪"), "get_location", {"provider": "network"}),
            ("剪贴板", "get_clipboard", {}),
            ("传感器", "list_sensors", {}),
            (re.compile(r"通知|提醒|notify"), "send_notification", {"title": "口袋智灵", "message": "这是一条演示通知"}),
            (re.compile(r"打电话|打个电话|呼叫"), "make_call", {"number": "10086"}),
            (re.compile(r"发短信|发条短信"), "send_sms", {"numbers": ["13800000000"], "text": "测试短信"}),
            (re.compile(r"语音|朗读|读出来"), "tts_speak", {"text": "你好，我是口袋智灵，这是语音演示"}),
            ("离线朗读", "tts_offline", {"text": "明天早上八点提醒我开会"}),
            (re.compile(r"拍照|拍张|camera"), "take_photo", {}),
            (re.compile(r"wifi|WiFi|网络"), "get_wifi_info", {}),
            (re.compile(r"工具|执行|运行"), "run_shell", {"command": "echo 口袋智灵工具链路OK"}),
            # 子代理：让 mock 也能演示「派活 → 独立上下文 → 只回结论」
            (
                re.compile(r"子代理|派个任务|交给子代理|subagent"),
                "subagent",
                {"task": "请统计当前目录下的文件数量，并用一句话回答。"},
            ),
            # 任务清单：mock 也能演示 todo 面板与进度条（手机适配与演示都需要它）
            (
                re.compile(r"任务清单|待办|todo"),
                "todo_write",
                {
                    "todos": [
                        {"content": "查一次电量并汇报", "status": "completed"},
                        {"content": "把保修卡存进知识库", "status": "in_progress"},
                        {"content": "设一个低电量提醒", "status": "pending"},
                    ]
                },
            ),
        ]

    def _match_tool(self, text: str):
        """返回 (name, args) 或 None。支持 str（contains）和 regex（search）。"""
        for pattern, name, args in self._TOOLS:
            if isinstance(pattern, re.Pattern):
                if pattern.search(text):
                    return name, args
            elif pattern in text:
                return name, args
        return None

    async def chat(self, messages: list[dict], tools: list[dict] | None):
        last_user = next(
            (str(m.get("content") or "") for m in reversed(messages) if m["role"] == "user"), ""
        )
        has_tool_result = any(m["role"] == "tool" for m in messages)

        if not has_tool_result and tools:
            matched = self._match_tool(last_user)
            if matched:
                name, args = matched
                # 先输出思考过程
                for tok in _stream_text(f"需要调用 {name} 来回答这个问题。"):
                    yield _Chunk(_Delta(reasoning=tok))
                # 再发出 tool call
                yield _Chunk(_Delta(tool_calls=[_tool_call(0, "call_mock_1", name, args)]))
                return

        if has_tool_result:
            reply = (
                "工具已执行完毕（mock 演示）：结果已返回并回填。"
                "这条消息说明「流式输出 → 工具调用 → 审批 → 结果回填」链路是通的，"
                "配置真实 LLM 后我会真正干活。"
            )
            for tok in _stream_text(reply):
                yield _Chunk(_Delta(content=tok))
            return

        # 默认回显（流式）
        reply = f"（mock 模式）收到：{last_user[:200]}。配置真实 LLM（见设置）后我会真正执行任务。"
        for tok in _stream_text(reply):
            yield _Chunk(_Delta(content=tok))

    # ---------- 会话自动命名 / 记忆摘要（mock 规则版） ----------

    def title(self, message: str) -> str:
        text = " ".join(str(message).split())
        return text[:14] + ("…" if len(text) > 14 else "")

    def summarize(self, messages: list[dict]) -> str:
        last_user = next((str(m["content"] or "") for m in reversed(messages) if m["role"] == "user"), "")
        last_asst = next((str(m["content"] or "") for m in reversed(messages) if m["role"] == "assistant"), "")
        return f"用户最近请求：{last_user[:60]}。助手答复：{last_asst[:80]}。"
