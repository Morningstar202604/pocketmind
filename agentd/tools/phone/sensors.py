"""感知工具：定位 / 剪贴板 / 传感器。"""
from __future__ import annotations

import json

from . import _t
from ._termux import _bin, _fail, _ok, _run, _unavailable


async def get_location(provider: str = "network") -> dict:
    b = _bin("termux-location")
    if not b:
        return _unavailable("termux-location")
    if provider not in ("gps", "network", "passive"):
        provider = "network"
    try:
        code, out, err = await _run([b, "-p", provider, "-r", "once"], timeout=30)
    except TimeoutError:
        return _fail("获取定位超时（GPS 冷启动可能较慢，可稍后再试）")
    if code != 0:
        return _fail(f"定位失败：{err or f'exit {code}'}")
    try:
        data = json.loads(out) if out else {}
    except json.JSONDecodeError:
        return _fail("定位结果解析失败")
    return _ok(
        provider=data.get("provider"),
        latitude=data.get("latitude"),
        longitude=data.get("longitude"),
        accuracy_m=data.get("accuracy"),
        altitude=data.get("altitude"),
    )


async def clipboard_get() -> dict:
    b = _bin("termux-clipboard-get")
    if not b:
        return _unavailable("termux-clipboard-get")
    try:
        code, out, err = await _run([b])
        return _ok(content=out) if code == 0 else _fail("读取剪贴板失败")
    except TimeoutError:
        return _fail("读取剪贴板超时")


async def clipboard_set(text: str) -> dict:
    b = _bin("termux-clipboard-set")
    if not b:
        return _unavailable("termux-clipboard-set")
    try:
        await _run([b], input_text=str(text))
        return _ok()
    except TimeoutError:
        return _fail("写入剪贴板超时")


async def list_sensors() -> dict:
    b = _bin("termux-sensor")
    if not b:
        return _unavailable("termux-sensor")
    try:
        code, out, err = await _run([b, "-l"])
    except TimeoutError:
        return _fail("读取传感器列表超时")
    if code != 0:
        return _fail(f"termux-sensor 失败：{err or f'exit {code}'}")
    lines = [ln for ln in out.splitlines() if ln.strip()]
    sensors = [ln.split(":")[0].strip() for ln in lines if ":" in ln] or lines
    return _ok(count=len(sensors), sensors=sensors[:30])


async def read_sensor(name: str) -> dict:
    b = _bin("termux-sensor")
    if not b:
        return _unavailable("termux-sensor")
    try:
        code, out, err = await _run([b, "-s", str(name), "-n", "1"], timeout=15)
    except TimeoutError:
        return _fail("读取传感器超时")
    if code != 0:
        return _fail(f"termux-sensor 失败：{err or f'exit {code}'}")
    try:
        data = json.loads(out) if out else []
    except json.JSONDecodeError:
        return _fail("传感器数据解析失败")
    if isinstance(data, list) and data:
        item = data[0] if isinstance(data[0], dict) else {}
        return _ok(sensor=item.get("name", name), values=item.get("values"))
    return _fail(f"未找到传感器：{name}")


async def tts_speak(text: str, rate: float = 1.0) -> dict:
    b = _bin("termux-tts-speak")
    if not b:
        return _unavailable("termux-tts-speak")
    args = [b, "-r", str(max(0.0, min(2.0, rate)))]
    try:
        await _run(args, input_text=str(text))
        return _ok(spoken_chars=len(str(text)))
    except TimeoutError:
        return _fail("TTS 朗读超时")


async def speech_to_text(lang: str = "zh-CN") -> dict:
    b = _bin("termux-speech-to-text")
    if not b:
        return _unavailable("termux-speech-to-text")
    try:
        code, out, err = await _run([b, "-l", str(lang)], timeout=45)
    except TimeoutError:
        return _fail("语音识别超时（未检测到说话内容）")
    if code != 0:
        return _fail(f"语音识别失败：{err or f'exit {code}'}")
    return _ok(text=out)


# ---------- 注册表 ----------
_t("get_location", "获取手机当前位置（经纬度，涉及隐私，需确认）。",
   {"provider": {"type": "string", "description": "gps/network/passive，默认 network"}},
   "write", get_location, "获取当前位置")
_t("get_clipboard", "读取手机剪贴板当前内容。", {}, "safe", clipboard_get, "读取剪贴板")
_t("set_clipboard", "把指定文本写入手机剪贴板。",
   {"text": {"type": "string", "description": "要写入剪贴板的文本"}},
   "write", clipboard_set, "写入剪贴板", required=["text"])
_t("list_sensors", "列出手机上可用的传感器。", {}, "safe", list_sensors, "列出传感器")
_t("read_sensor", "读取指定传感器的当前读数（如 accelerometer/gyroscope/proximity）。",
   {"name": {"type": "string", "description": "传感器名称"}},
   "safe", read_sensor, "读取传感器读数", required=["name"])
_t("tts_speak", "用手机扬声器朗读一段文字（TTS 语音合成）。",
   {"text": {"type": "string", "description": "要朗读的文字"}, "rate": {"type": "number", "description": "语速 0-2，默认 1.0"}},
   "write", tts_speak, "语音朗读文字", required=["text"])
_t("speech_to_text", "打开麦克风识别一段语音并返回文字（会录 1-2 秒环境声音，需确认）。",
   {"lang": {"type": "string", "description": "语言代码，默认 zh-CN"}},
   "write", speech_to_text, "语音转文字", timeout=50)
