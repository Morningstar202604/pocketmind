"""离线 Mock LLM：不消耗 API 额度，用于端到端自测与演示。

用法：python -m agentd.main --mock。
触发规则（对测试友好且可预期）：用户消息含关键词 → 调用对应工具 → 下一轮返回收尾文本。
  电池→get_battery  短信→read_sms  定位/位置→get_location  剪贴板→get_clipboard
  传感器→list_sensors  通知→send_notification  工具→run_shell
无关键词 → 直接回显一段 mock 文本。
"""
from __future__ import annotations

import json
from types import SimpleNamespace


class _Delta:
    def __init__(self, content: str | None = None, reasoning: str | None = None, tool_calls: list | None = None):
        self.content = content
        self.reasoning_content = reasoning
        self.tool_calls = tool_calls


class _Chunk:
    def __init__(self, delta: _Delta):
        self.choices = [SimpleNamespace(delta=delta)]


def _tool_call(index: int, call_id: str, name: str, args: dict) -> SimpleNamespace:
    return SimpleNamespace(
        index=index,
        id=call_id,
        function=SimpleNamespace(name=name, arguments=json.dumps(args, ensure_ascii=False)),
    )


class MockLLM:
    def __init__(self, settings: dict):
        self.settings = settings
        # (触发词, 工具名, 参数)：用于端到端演示与自测，顺序即优先级
        self._TOOLS = [
            ("电池", "get_battery", {}),
            ("短信", "read_sms", {"limit": 5}),
            ("定位", "get_location", {"provider": "network"}),
            ("位置", "get_location", {"provider": "network"}),
            ("剪贴板", "get_clipboard", {}),
            ("传感器", "list_sensors", {}),
            ("通知", "send_notification", {"title": "口袋智灵 测试通知", "message": "这是 mock 演示"}),
            ("打电话", "make_call", {"number": "10086"}),
            ("打个电话", "make_call", {"number": "10086"}),
            ("发短信", "send_sms", {"numbers": ["13800000000"], "text": "测试短信"}),
            ("发条短信", "send_sms", {"numbers": ["13800000000"], "text": "测试短信"}),
            ("语音", "tts_speak", {"text": "你好，我是口袋智灵"}),
            ("离线朗读", "tts_offline", {"text": "明天早上八点提醒我开会"}),
            ("拍照", "take_photo", {}),
            ("WiFi", "get_wifi_info", {}),
            ("wifi", "get_wifi_info", {}),
            ("工具", "run_shell", {"command": "echo 口袋智灵工具链路OK"}),
        ]

    async def chat(self, messages: list[dict], tools: list[dict] | None):
        last_user = next(
            (m["content"] for m in reversed(messages) if m["role"] == "user"), ""
        )
        has_tool_result = any(m["role"] == "tool" for m in messages)

        if not has_tool_result and tools:
            for kw, name, args in self._TOOLS:
                if kw in str(last_user):
                    yield _Chunk(_Delta(reasoning=f"我先调用 {name} 获取结果，再给你答复。"))
                    yield _Chunk(_Delta(tool_calls=[_tool_call(0, "call_mock_1", name, args)]))
                    return

        if has_tool_result:
            yield _Chunk(_Delta(content=(
                "工具已执行完毕（mock 演示）：结果已返回并回填。"
                "这条消息说明「流式输出 → 工具调用 → 审批 → 结果回填」链路是通的，"
                "配置真实 LLM 后我会真正干活。"
            )))
            return

        yield _Chunk(_Delta(content=f"（mock 模式）收到：{str(last_user)[:200]}。配置真实 LLM（见设置）后我会真正执行任务。"))

    # ---------- 会话自动命名 / 记忆摘要（mock 规则版） ----------

    def title(self, message: str) -> str:
        text = " ".join(str(message).split())
        return text[:14] + ("…" if len(text) > 14 else "")

    def summarize(self, messages: list[dict]) -> str:
        last_user = next((str(m["content"] or "") for m in reversed(messages) if m["role"] == "user"), "")
        last_asst = next((str(m["content"] or "") for m in reversed(messages) if m["role"] == "assistant"), "")
        return f"用户最近请求：{last_user[:60]}。助手答复：{last_asst[:80]}。"
