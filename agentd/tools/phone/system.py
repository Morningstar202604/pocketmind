"""系统工具：电池 / 通知 / 震动 / 手电筒 / 音量 / 亮度 / Toast。"""
from __future__ import annotations

import json

from . import _t
from ._termux import _bin, _fail, _ok, _run, _run_checked, _unavailable


async def battery() -> dict:
    b = _bin("termux-battery-status")
    if not b:
        return _unavailable("termux-battery-status")
    try:
        code, out, err = await _run([b])
        data = json.loads(out) if out else {}
        if isinstance(data, list):
            data = data[0] if data else {}
    except (TimeoutError, json.JSONDecodeError, IndexError, TypeError):
        return _fail("读取电量失败")
    return _ok(
        status=data.get("status"),
        percentage=data.get("percentage"),
        plugged=data.get("plugged"),
        temperature_c=data.get("temperature"),
        voltage=data.get("voltage"),
    )


async def notify(title: str, message: str = "") -> dict:
    b = _bin("termux-notification")
    if not b:
        return _unavailable("termux-notification")
    args = [b, "--title", str(title)]
    if message:
        args += ["--content", str(message)]
    failed = await _run_checked(args, "发送通知")
    if failed:
        return failed
    return _ok()


async def toast(message: str) -> dict:
    b = _bin("termux-toast")
    if not b:
        return _unavailable("termux-toast")
    failed = await _run_checked([b, "-s", str(message)], "显示 toast")
    if failed:
        return failed
    return _ok()


async def vibrate(duration_ms: int = 500) -> dict:
    b = _bin("termux-vibrate")
    if not b:
        return _unavailable("termux-vibrate")
    failed = await _run_checked([b, "-d", str(max(50, min(10000, duration_ms)))], "震动")
    if failed:
        return failed
    return _ok(vibrated_ms=duration_ms)


async def torch(on: bool) -> dict:
    b = _bin("termux-torch")
    if not b:
        return _unavailable("termux-torch")
    failed = await _run_checked([b, "on" if on else "off"], "手电筒")
    if failed:
        return failed
    return _ok(torch="on" if on else "off")


async def get_volume(stream: str = "music") -> dict:
    b = _bin("termux-volume")
    if not b:
        return _unavailable("termux-volume")
    try:
        code, out, err = await _run([b])
        data = json.loads(out) if out else []
    except (TimeoutError, json.JSONDecodeError):
        return _fail("读取音量失败")
    for item in data if isinstance(data, list) else []:
        if isinstance(item, dict) and item.get("stream") == stream:
            return _ok(stream=stream, volume=item.get("volume"), max_volume=item.get("max_volume"))
    return _ok(stream=stream, volume=None,
               note="未找到该流（可用流：music/ring/notification/alarm/call/system）")


async def set_volume(stream: str, level: int) -> dict:
    b = _bin("termux-volume")
    if not b:
        return _unavailable("termux-volume")
    # 必须校验退出码：裸 _run() 只返回 (code, out, err)，不看 code 就会把
    # 「权限被拒 / 参数非法」报成「已完成」。与 send_sms / set_wifi 同一套纪律。
    try:
        failed = await _run_checked([b, str(stream), str(max(0, min(100, level)))], "设置音量")
    except TimeoutError:
        return _fail("设置音量超时")
    if failed:
        return failed
    return _ok(stream=stream, level=level)


async def set_brightness(level: int) -> dict:
    b = _bin("termux-brightness")
    if not b:
        return _unavailable("termux-brightness")
    try:
        code, out, err = await _run([b, str(max(0, min(255, level)))])
    except TimeoutError:
        return _fail("设置亮度超时")
    if code != 0:
        return _fail(f"设置亮度失败：{err or f'exit {code}'}")
    return _ok(brightness=level)


# ---------- 注册表 ----------
_t("get_battery", "获取手机电池状态：电量百分比、是否充电、温度等。", {}, "safe", battery, "查看电池电量")
_t("send_notification", "在手机通知栏发一条系统通知（标题 + 可选正文）。",
   {"title": {"type": "string", "description": "通知标题"}, "message": {"type": "string", "description": "通知正文（可选）"}},
   "write", notify, "发送系统通知", required=["title"])
_t("show_toast", "在屏幕上弹出一条短暂的悬浮提示。",
   {"message": {"type": "string", "description": "提示文字"}},
   "write", toast, "弹出悬浮提示", required=["message"])
_t("vibrate", "让手机震动指定时长（毫秒，默认 500）。",
   {"duration_ms": {"type": "integer", "description": "震动时长（毫秒）"}},
   "write", vibrate, "震动手机")
_t("set_torch", "打开或关闭手电筒。",
   {"on": {"type": "boolean", "description": "true=开，false=关"}},
   "write", torch, "开关手电筒", required=["on"])
_t("get_volume", "查询某个音频流音量（music/ring/notification/alarm/call/system）。",
   {"stream": {"type": "string", "description": "音频流名称，默认 music"}},
   "safe", get_volume, "查询音量")
_t("set_volume", "设置某个音频流的音量（0-100）。",
   {"stream": {"type": "string", "description": "音频流名称，如 music"}, "level": {"type": "integer", "description": "音量 0-100"}},
   "write", set_volume, "设置音量", required=["stream", "level"])
_t("set_brightness", "设置屏幕亮度（0-255）。",
   {"level": {"type": "integer", "description": "亮度 0-255"}},
   "write", set_brightness, "设置屏幕亮度", required=["level"])
