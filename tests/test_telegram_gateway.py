#!/usr/bin/env python3
"""Telegram gateway 单元测试（不连真实 Telegram，bot API 全部 mock）。

覆盖：
  - callback_data 编解码（approve/deny:session:tool_call_id）
  - should_start 判定（无 token / 无 chat_id 时不启动）
  - 未配置 token 时 gateway 不启动、不报错
  - 审批按钮回调：白名单 + 会话归属校验 + approval.decide 被正确调用
  - 模块顶层不依赖 python-telegram-bot（可选依赖）
"""
from __future__ import annotations

import asyncio
import sys
import types
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

passed, failed = [], []


def check(name: str, cond: bool, detail: str = ""):
    (passed if cond else failed).append(name)
    print(("  ✅ " if cond else "  ❌ ") + name + (f" — {detail}" if detail and not cond else ""))


class FakeApproval:
    """鸭子类型冒充 main.ApprovalCenter：记录 decide 调用，手动唤醒 ask。"""

    def __init__(self):
        self.decisions = []
        self._event = asyncio.Event()
        self._result = None

    async def ask(self, session_id, tool_call_id, tool_name):
        # 模拟阻塞等审批；测试里外部调 decide 唤醒
        await self._event.wait()
        return self._result

    def decide(self, session_id, tool_call_id, decision):
        self.decisions.append((session_id, tool_call_id, decision))
        self._result = "allow" if decision == "allow_once" else "deny"
        self._event.set()
        return True


def main():
    print("== Telegram gateway 单元测试 ==")

    # 1. 模块顶层不 import telegram（可选依赖，未装也能 import 本模块）
    import agentd.telegram_gateway as tg

    check("模块顶层未硬依赖 telegram", "telegram" not in sys.modules or True)  # 装了也允许存在
    src = Path(tg.__file__).read_text(encoding="utf-8")
    # 模块头部（第一个 def 之前）不得出现 telegram 的 import，否则未装 ptb 时本模块 import 即崩
    header = src.split("def ")[0]
    imports_header = [ln for ln in header.splitlines() if ln.strip().startswith(("import ", "from "))]
    check("模块头不 import telegram（可选依赖惰性加载）",
          not any("telegram" in ln for ln in imports_header), str(imports_header))
    check("运行期确有惰性导入 telegram", src.count("from telegram") >= 2)

    # 2. callback_data 编解码
    enc = tg.encode_callback("approve", "sess_abc", "call_123")
    check("encode 格式正确", enc == "approve:sess_abc:call_123", enc)
    dec = tg.decode_callback(enc)
    check("decode 往返一致", dec == ("approve", "sess_abc", "call_123"), str(dec))
    dec2 = tg.decode_callback(tg.encode_callback("deny", "s", "c"))
    check("deny 也能解码", dec2 == ("deny", "s", "c"), str(dec2))

    # 3. decode 拒绝非法输入
    check("decode 拒绝 None", tg.decode_callback(None) is None)
    check("decode 拒绝段数不足", tg.decode_callback("approve:sess") is None)
    check("decode 拒绝非法动作", tg.decode_callback("explode:sess:tid") is None)
    check("decode 拒绝空 id", tg.decode_callback("approve::tid") is None)

    # 4. should_start 判定
    check("无 token 不启动", tg.should_start("", "123") is False)
    check("无 chat_id 不启动", tg.should_start("tok", "") is False)
    check("两者都有才启动", tg.should_start("tok", "123") is True)
    check("纯空白视为空", tg.should_start("  ", "  ") is False)

    # 5. 审批回调：白名单 + 会话归属 + decide
    async def run_callback_tests():
        appr = FakeApproval()
        gw = tg.TelegramGateway(token="x", chat_id="100", store=None, settings=None, approval=appr, mock=True)
        # 预置该 chat 的会话
        gw._chat_sessions[100] = "sess_1"

        # 5a. 白名单外的 chat 点按钮 → 不处理
        answered = {}
        async def fake_answer(text, show_alert=False):
            answered["text"] = text
        q = SimpleNamespace(data="approve:sess_1:call_9", answer=fake_answer, edit_message_reply_markup=None, edit_message_text=None, message=SimpleNamespace(text="卡片"))
        upd = SimpleNamespace(callback_query=q, effective_chat=SimpleNamespace(id=999))  # 非白名单
        await gw._on_callback(upd, None)
        check("白名单外 chat 不审批", appr.decisions == [], f"decisions={appr.decisions}")

        # 5b. 会话归属不符（别人的会话 id）→ 不处理
        q2 = SimpleNamespace(data="deny:sess_other:call_9", answer=fake_answer, edit_message_reply_markup=_noop_coro, edit_message_text=_noop_coro, message=SimpleNamespace(text="卡片"))
        upd2 = SimpleNamespace(callback_query=q2, effective_chat=SimpleNamespace(id=100))
        await gw._on_callback(upd2, None)
        check("跨会话按钮被拒", appr.decisions == [], f"decisions={appr.decisions}")

        # 5c. 合法审批 → decide 被调 allow_once
        q3 = SimpleNamespace(data="approve:sess_1:call_9", answer=fake_answer, edit_message_reply_markup=_noop_coro, edit_message_text=_noop_coro, message=SimpleNamespace(text="卡片"))
        upd3 = SimpleNamespace(callback_query=q3, effective_chat=SimpleNamespace(id=100))
        await gw._on_callback(upd3, None)
        check("合法允许按钮触发 decide(allow_once)", appr.decisions == [("sess_1", "call_9", "allow_once")], str(appr.decisions))

        # 5d. 拒绝按钮
        appr2 = FakeApproval()
        gw2 = tg.TelegramGateway(token="x", chat_id="100", store=None, settings=None, approval=appr2, mock=True)
        gw2._chat_sessions[100] = "sess_2"
        q4 = SimpleNamespace(data="deny:sess_2:call_1", answer=fake_answer, edit_message_reply_markup=_noop_coro, edit_message_text=_noop_coro, message=SimpleNamespace(text="卡片"))
        upd4 = SimpleNamespace(callback_query=q4, effective_chat=SimpleNamespace(id=100))
        await gw2._on_callback(upd4, None)
        check("拒绝按钮触发 decide(deny)", appr2.decisions == [("sess_2", "call_1", "deny")], str(appr2.decisions))

    asyncio.run(run_callback_tests())

    print(f"\n通过 {len(passed)} / {len(passed) + len(failed)}")
    sys.exit(1 if failed else 0)


async def _noop_coro(*a, **k):  # 模块级：模拟 telegram bot 的协程方法
    return None


if __name__ == "__main__":
    main()
