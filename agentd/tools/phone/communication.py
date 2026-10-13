"""通信工具：短信、电话。"""
from __future__ import annotations

import json

from . import _t
from ._termux import _bin, _fail, _ok, _run, _unavailable


async def send_sms(numbers: list[str], text: str) -> dict:
    b = _bin("termux-sms-send")
    if not b:
        return _unavailable("termux-sms-send")
    if not numbers:
        return _fail("缺少收件人号码")
    recipients = ",".join(str(n) for n in numbers)
    try:
        await _run([b, "-n", recipients], input_text=str(text))
        return _ok(sent_to=numbers, chars=len(str(text)))
    except TimeoutError:
        return _fail("发送短信超时")


async def read_sms(limit: int = 20) -> dict:
    b = _bin("termux-sms-list")
    if not b:
        return _unavailable("termux-sms-list")
    try:
        code, out, err = await _run([b, "-l", str(max(1, min(100, limit))), "-t", "inbox"])
    except TimeoutError:
        return _fail("读取短信超时")
    if code != 0:
        return _fail(f"termux-sms-list 失败：{err or f'exit {code}'}")
    try:
        data = json.loads(out) if out else []
    except json.JSONDecodeError:
        return _fail("解析短信列表失败")
    items = []
    for m in (data if isinstance(data, list) else [])[:limit]:
        if isinstance(m, dict):
            items.append({
                "from": m.get("number") or m.get("address"),
                "time": m.get("received_time") or m.get("received_date") or m.get("date") or m.get("time"),
                "text": (m.get("body") or "")[:200],
            })
    return _ok(count=len(items), messages=items)


async def make_call(number: str) -> dict:
    b = _bin("termux-telephony-call")
    if not b:
        return _unavailable("termux-telephony-call")
    try:
        await _run([b, str(number)], timeout=10)
        return _ok(calling=number)
    except TimeoutError:
        return _fail("拨号超时")


# ---------- 注册表 ----------
_t("send_sms", "发送短信给一个或多个号码（内容为纯文本）。",
   {"numbers": {"type": "array", "items": {"type": "string"}, "description": "收件人号码列表，如 [\"10086\"]"},
    "text": {"type": "string", "description": "短信内容"}},
   "write", send_sms, "发送短信", required=["numbers", "text"])
_t("read_sms", "读取最近收到的短信（涉及隐私，需确认）。",
   {"limit": {"type": "integer", "description": "读取条数，默认 20"}},
   "write", read_sms, "读取最近短信")
_t("make_call", "拨打电话给指定号码（会产生真实通话，需确认）。",
   {"number": {"type": "string", "description": "电话号码"}},
   "danger", make_call, "拨打电话", required=["number"])
