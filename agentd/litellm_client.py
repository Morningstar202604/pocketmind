"""LiteLLM 统一网关客户端：一份代码调用 100+ LLM（OpenAI 格式）。

替代原有的硬编码多厂商对接（config.py 的 PRESETS），核心价值：
  - 单一 OpenAI 兼容接口：无论后端是 OpenAI / Anthropic / Ollama / 豆包 / DeepSeek
  - 自动故障转移：primary 挂了切 secondary（配置 fallback_models）
  - 成本追踪：每次调用后累计 token 花费
  - 本地推理：通过 Ollama/vLLM 跑量化模型（断网可用）
  - 模型列表动态发现：不再硬编码 PRESETS

保留原有 OpenAI-SDK 直连模式作为 fast path（延迟敏感时跳过 LiteLLM 包装）；
通过 settings.server.use_liteLLM 开关切换。
"""
from __future__ import annotations

import logging
import os

import litellm
from litellm import acompletion
from litellm.utils import cost_per_token, token_counter

logger = logging.getLogger(__name__)

# 关闭 LiteLLM 过多的日志输出（生产环境安静）
litellm.set_verbose = False
litellm.suppress_debug_info = True

# 不尝试从 GitHub 拉 cost map（离线/手机环境没有公网）
litellm.drop_params = True  # 自动丢弃模型不认识的参数（跨厂商兼容）


class LiteLLMClient:
    """异步 LiteLLM 客户端，对外暴露与 openai.AsyncOpenAI 近似的流式接口。

    用法：
        client = LiteLLMClient(config)
        async for chunk in client.chat(messages, tools):
            delta = chunk.choices[0].delta
    """

    def __init__(self, cfg: dict):
        """
        cfg 结构：
        {
          "litellm": {
            "model": "openai/gpt-4o",          #LiteLLM 模型名（provider/model）
            "fallbacks": ["anthropic/claude-3-5-sonnet"],  #可选故障转移
            "api_base": None,                  #自定义端点
            "api_key": None,
            "temperature": 0.3,
            "max_tokens": 4096,
            "timeout": 60,
            "cost_tracking": true
          }
        }
        """
        llm_cfg = cfg.get("llm", {})
        self._litellm_cfg = cfg.get("litellm", {})
        self._primary_model = self._litellm_cfg.get("model") or llm_cfg.get("model", "gpt-4o")
        self._provider = llm_cfg.get("provider", "openai")

        # 统一 model 名称映射
        # 如果只给了短名（如 "doubao"）但开了 LiteLLM，补全 prefix
        if self._provider != "custom" and "/" not in self._primary_model:
            self._primary_model = f"{self._provider}/{self._primary_model}"

        self._fallbacks = self._litellm_cfg.get("fallbacks", [])
        self._api_base = self._litellm_cfg.get("api_base") or llm_cfg.get("base_url") or None
        self._api_key = self._litellm_cfg.get("api_key") or llm_cfg.get("api_key") or os.environ.get("LITELLM_API_KEY")
        self._temperature = float(self._litellm_cfg.get("temperature", llm_cfg.get("temperature", 0.3)))
        self._max_tokens = int(self._litellm_cfg.get("max_tokens", llm_cfg.get("max_tokens", 4096)))
        self._timeout = int(self._litellm_cfg.get("timeout", 60))
        self._cost_tracking = self._litellm_cfg.get("cost_tracking", True)

        # 累计花费追踪
        self.total_cost_usd = 0.0
        self.total_tokens = 0
        self.call_count = 0
        self._actual_completion_tokens = 0  # 跟踪实际完成 tokens

    async def chat(self, messages: list[dict], tools: list[dict] | None = None):
        """流式调用。yield litellm 响应 chunk（与 OpenAI 格式相同）。

        自动重试：primary 失败 → fallback 列表顺序尝试。
        """
        # 构建完整 model 列表（primary + fallbacks）
        models = [self._primary_model] + self._fallbacks
        last_err = None

        for model in models:
            try:
                params = {
                    "model": model,
                    "messages": messages,
                    "temperature": self._temperature,
                    "max_tokens": self._max_tokens,
                    "timeout": self._timeout,
                    "stream": True,
                    "drop_params": True,
                }
                if tools:
                    # LiteLLM 的 tools 格式 = OpenAI function calling 格式
                    params["tools"] = [
                        {"type": "function", "function": t} if "function" not in t else t
                        for t in tools
                    ]
                    # tool_choice 让模型自己决定
                    if len(tools) > 1:
                        params["tool_choice"] = "auto"

                if self._api_base:
                    params["api_base"] = self._api_base
                if self._api_key:
                    params["api_key"] = self._api_key

                response = await acompletion(**params)
                self.call_count += 1

                # 流式 yield 每个 chunk，同时累计真实 token 使用量。
                # 注意：LiteLLM 可能多个 chunk 都带 usage，用 usage_seen 防重复累加。
                usage_seen = False
                self._actual_completion_tokens = 0  # 每轮调用前清零（防止跨调用累积）
                async for chunk in response:
                    # 跟踪 usage（LiteLLM 在流式 chunk 中包含 usage 字段）
                    usage = getattr(chunk, "usage", None)
                    if usage and not usage_seen:
                        prompt_t = getattr(usage, "prompt_tokens", 0) or 0
                        completion_t = getattr(usage, "completion_tokens", 0) or 0
                        if prompt_t:
                            self.total_tokens += prompt_t  # prompt tokens（只加一次）
                        if completion_t:
                            self._actual_completion_tokens = completion_t
                        usage_seen = True
                    yield chunk

                # 调用成功，记录花费（可选）
                if self._cost_tracking:
                    self._track_cost(model, messages)

                return  # 跳出 for（不再尝试 fallback）

            except Exception as e:
                last_err = e
                logger.warning("LiteLLM 调用失败 model=%s: %s，尝试下一个", model, str(e)[:100])
                continue

        # 全部失败
        raise last_err or RuntimeError(f"LiteLLM 所有模型调用失败: {models}")

    def _track_cost(self, model: str, messages: list):
        """估算本次调用 token 花费。优先使用实际 token；否则回退 max_tokens 上界。"""
        try:
            prompt_tokens = token_counter(model=model, messages=messages)
            # 优先用实际完成 tokens；否则用 max_tokens * 0.6 估算（业界均值）
            completion_tokens = self._actual_completion_tokens or int(self._max_tokens * 0.6)
            cost = cost_per_token(
                model=model,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
            )
            if cost and cost > 0:
                self.total_cost_usd += cost
                if not self._actual_completion_tokens:
                    self.total_tokens += prompt_tokens + completion_tokens
        except Exception:
            pass  # 花费追踪失败不影响主流程

    def get_stats(self) -> dict:
        """返回调用统计（用于前端展示成本信息）。"""
        return {
            "call_count": self.call_count,
            "total_tokens": self.total_tokens,
            "total_cost_usd": round(self.total_cost_usd, 6),
            "primary_model": self._primary_model,
            "fallback_count": len(self._fallbacks),
        }

    async def summarize(self, messages: list[dict], max_chars: int = 400) -> str | None:
        """基于 LLM 把一批旧消息压缩为摘要文本（供 auto_summarize_if_needed 调用）。

        失败时返回 None（调用方应跳过，不影响主流程）。
        """
        if not messages or self._primary_model is None:
            return None
        try:
            text = "\n".join(f"{m['role']}: {m.get('content','')}" for m in messages)
            system = (
                "用简体中文输出简洁的事实摘要（第三人称、第三人称），保留关键动作/决定/结果。"
                f" 限 {max_chars} 字。格式：要点、不超过5条。"
            )
            summary_msgs = [
                {"role": "system", "content": system},
                {"role": "user", "content": text[:4000]},
            ]
            full = ""
            async for chunk in self.chat(summary_msgs):
                try:
                    delta = chunk.choices[0].delta.content
                    if delta:
                        full += delta
                except Exception:
                    pass
            return full.strip() or None
        except Exception:
            return None
