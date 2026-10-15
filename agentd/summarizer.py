"""对话摘要器：直连 OpenAI 兼容端点，把一批历史消息压成简短摘要。

存在的理由（修复一个静默失效）：记忆摘要链路此前只会调用
``llm_client.summarize(...)``，而这条路径在「未开启 LiteLLM」时落到
``AsyncOpenAI`` 客户端——它没有 summarize 方法，于是
``auto_summarize_if_needed`` 每次都抛 AttributeError 被上层 try/except 吞掉、
``archive_messages`` 退化成一句占位。默认配置（use_litellm=false）下
等于「摘要功能从未生效」。本模块把「直连端点做摘要」补成一等实现。

LiteLLM 开启时仍走 LiteLLMClient.summarize（带成本追踪），本类作为其
默认回退；两者接口一致（summarize(messages, max_chars=...)）。
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

_SYS_PROMPT = (
    "你是对话压缩器。把用户给出的历史对话压缩成一段中文摘要，用于后续对话的记忆。"
    "要求：保留关键事实（人名、时间、地点、数字、结论、未完成的待办），"
    "丢弃寒暄与重复内容，不要评论、不要加标题、不要用列表以外的格式。"
)


def _transcript(messages: list[dict], budget: int = 12000) -> str:
    """把 OpenAI 格式消息拍平成文本（只取尾部 budget 字符，越新越重要）。"""
    lines = []
    for m in messages:
        role = {"user": "用户", "assistant": "助手", "tool": "工具", "system": "系统"}.get(
            m.get("role", ""), str(m.get("role", ""))
        )
        content = str(m.get("content") or "").strip()
        if not content:
            continue
        lines.append(f"{role}：{content}")
    text = "\n".join(lines)
    return text[-budget:]


class LLMSummarizer:
    """直连 OpenAI 兼容端点的摘要器（懒加载客户端，可安全重复构造）。"""

    def __init__(self, settings):
        self._settings = settings
        self._client = None

    def _get_client(self):
        if self._client is not None:
            return self._client
        from openai import AsyncOpenAI

        llm = self._settings.get().get("llm", {})
        self._client = AsyncOpenAI(
            api_key=llm.get("api_key") or "empty",
            base_url=llm.get("base_url") or None,
            timeout=60,
            max_retries=1,
        )
        return self._client

    async def summarize(self, messages: list[dict], max_chars: int = 400) -> str | None:
        """返回摘要文本；LLM 未配置 / 调用失败时返回 None（调用方自行降级）。"""
        llm = self._settings.get().get("llm", {})
        if not (llm.get("api_key") and llm.get("model") and llm.get("base_url")):
            return None
        transcript = _transcript(messages)
        if not transcript.strip():
            return None
        try:
            client = self._get_client()
            resp = await client.chat.completions.create(
                model=llm.get("model") or "unknown",
                messages=[
                    {"role": "system", "content": f"{_SYS_PROMPT}\n摘要不超过 {int(max_chars)} 个汉字。"},
                    {"role": "user", "content": transcript},
                ],
                max_tokens=min(1024, max(128, int(max_chars) * 2)),
                temperature=0.2,
            )
            text = (resp.choices[0].message.content or "").strip()
            return text[: int(max_chars) * 2] if text else None
        # aqg: top-level boundary —— 摘要失败不阻塞主流程（调用方有占位摘要兜底）
        except Exception as e:  # noqa: BLE001
            logger.warning("直连摘要失败: %s", e)
            return None

    async def aclose(self) -> None:
        if self._client is not None:
            try:
                await self._client.close()
            # aqg: top-level boundary —— 关池失败仅丢弃
            except Exception:  # noqa: BLE001
                pass
            self._client = None
