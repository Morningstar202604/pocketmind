"""Agent Harness 基础件：单轮预算、重复调用保护、上下文硬裁剪。

为什么把这些放在独立模块：``agent.py`` 负责「下一步做什么」，这里负责
「这一步是否还允许继续」。把运行时护栏与业务决策分开，才能单独测试：

- ``TurnGuard``：单轮墙钟上限 + 连续重复工具调用检测。只有 max_tool_chain_depth
  不够——模型可以每轮换一个工具名继续绕，或在同一调用上反复重试；手机端必须
  有墙钟，否则一次对话可以在后台跑一小时。
- ``fit_context``：发送给模型之前的最后一道预算闸。历史摘要/记忆/工具结果都
  可能把上下文顶爆；裁剪时必须保持 assistant(tool_calls) 与 tool 响应成对，
  否则 OpenAI 兼容端点直接 400。
- ``tool_fingerprint``：把工具名 + 规范化参数压成稳定签名，用于重复检测，
  避免 JSON 键顺序不同导致误判。
"""
from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass, field

CHARS_PER_TOKEN = 4


def canonical_json(value) -> str:
    """稳定 JSON：键排序、紧凑分隔符，用于签名而不是展示。"""
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    except (TypeError, ValueError):
        return repr(value)


def tool_fingerprint(name: str, args: dict) -> str:
    """工具名 + 规范化参数 -> 短哈希。"""
    raw = f"{name}\x00{canonical_json(args)}"
    return hashlib.sha1(raw.encode("utf-8", errors="replace")).hexdigest()[:16]


def estimate_tokens(messages: list[dict]) -> int:
    """粗估消息 token 数：正文 + tool_calls 参数 + 每条消息固定开销。

    旧实现只算 content，assistant 的 tool_calls 参数完全不计——一个刚写完
    20KB 文件内容的 ``write_file`` 调用可能被估算成 0 token，预算判断失真。
    """
    total = 0
    for m in messages:
        total += 4
        total += len(str(m.get("content") or ""))
        tcs = m.get("tool_calls") or []
        if tcs:
            total += len(canonical_json(tcs))
    return total // CHARS_PER_TOKEN


@dataclass
class TurnGuard:
    """一次用户请求内的运行时护栏。

    max_seconds <= 0 表示不限制墙钟（不建议，保留给测试/离线场景）。
    repeat_tool_limit <= 0 表示关闭重复检测。
    """

    max_seconds: float = 900
    repeat_tool_limit: int = 3
    started_at: float = field(default_factory=time.monotonic)
    last_fingerprint: str = ""
    repeat_count: int = 0
    tool_calls: int = 0

    def timed_out(self, now: float | None = None) -> bool:
        if self.max_seconds <= 0:
            return False
        return (now if now is not None else time.monotonic()) - self.started_at >= self.max_seconds

    def note_tool(self, name: str, args: dict) -> str | None:
        """登记一次工具调用；返回非空字符串表示应阻止本次执行。

        这里允许最多 ``repeat_tool_limit`` 次连续相同调用（含第一次），第
        ``limit + 1`` 次才拦。这样正常的「读一次、再读一次确认」不会误伤，
        真正的死循环会在几轮内被截断。
        """
        self.tool_calls += 1
        fp = tool_fingerprint(name, args)
        if fp == self.last_fingerprint:
            self.repeat_count += 1
        else:
            self.last_fingerprint = fp
            self.repeat_count = 1
        if self.repeat_tool_limit > 0 and self.repeat_count > self.repeat_tool_limit:
            return (
                f"检测到连续 {self.repeat_count} 次完全相同的工具调用（{name}），"
                "已阻止本次执行。请改变参数/工具，或直接给出结论。"
            )
        return None


def _blocked(messages: list[dict]) -> list[list[dict]]:
    """把消息切成不可拆的块：assistant(tool_calls) 必须和后续 tool 响应同块。"""
    blocks: list[list[dict]] = []
    i = 0
    while i < len(messages):
        m = messages[i]
        block = [m]
        i += 1
        if m.get("role") == "assistant" and m.get("tool_calls"):
            while i < len(messages) and messages[i].get("role") == "tool":
                block.append(messages[i])
                i += 1
        blocks.append(block)
    return blocks


def fit_context(messages: list[dict], budget_tokens: int) -> tuple[list[dict] | None, int]:
    """把消息裁到预算内，返回 ``(messages | None, estimated_tokens)``。

    None 表示「连固定 system + 最后一条用户消息都放不下」——调用方应直接报
    错停止，而不是把超预算请求发给端点（那是 400 或按量计费事故）。

    保留顺序：
    1. 第一条 system（固定角色提示，必须保留）；
    2. 最后一条 user（当前问题，必须保留）；
    3. 其余从旧到新丢弃，但按块丢，绝不拆散 tool 对。
    """
    if budget_tokens <= 0 or not messages:
        return messages, estimate_tokens(messages)
    if estimate_tokens(messages) <= budget_tokens:
        return messages, estimate_tokens(messages)

    first_system = messages[0] if messages[0].get("role") == "system" else None
    head_optional: list[dict] = []
    body_start = 0
    if first_system:
        body_start = 1
        while body_start < len(messages) and messages[body_start].get("role") == "system":
            head_optional.append(messages[body_start])
            body_start += 1
    body = messages[body_start:]
    blocks = _blocked(body)
    last_user_block = -1
    for idx, block in enumerate(blocks):
        if any(m.get("role") == "user" for m in block):
            last_user_block = idx
    if last_user_block < 0:
        last_user_block = len(blocks) - 1

    def build(start: int, opts: list[dict]) -> list[dict]:
        head = ([first_system] if first_system else []) + opts
        return head + [m for block in blocks[start:] for m in block]

    # 先丢旧块，保留最后一条用户消息及其后的收尾内容
    start = 0
    while start < last_user_block and estimate_tokens(build(start, head_optional)) > budget_tokens:
        start += 1
    candidate = build(start, head_optional)
    # 如果仍然超，逐个丢掉可选的 system 记忆/技能块（固定 system 永不丢）
    opts = list(head_optional)
    while opts and estimate_tokens(build(start, opts)) > budget_tokens:
        opts.pop(0)
    candidate = build(start, opts)
    if estimate_tokens(candidate) > budget_tokens:
        # 固定 system + 当前问题本身已经超预算，不能靠裁剪解决
        return None, estimate_tokens(candidate)
    return candidate, estimate_tokens(candidate)
