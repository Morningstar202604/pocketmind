"""手机能力工具：封装 termux-api 二进制（纯 Python 解析，不依赖 jq）。

M2 版本：覆盖 termux-api 主要能力，共 25 个工具：
  系统：  电量 / 通知 / 震动 / 手电筒 / 音量 / 亮度 / 吐司
  通信：  发短信 / 读短信 / 打电话
  隐私：  定位 / 剪贴板（读写）
  传感：  传感器列表 / 传感器读数
  语音：  TTS 朗读 / 语音转文字
  网络：  WiFi 信息 / WiFi 扫描 / WiFi 开关
  媒体：  拍照 / 分享
  文件：  下载 / 打开文件或链接

所有工具在 termux-api 未安装时返回结构化错误，模型可直接转告用户
"请先 pkg install termux-api"。二进制不在 PATH 时不崩溃、不假报成功。
"""
from __future__ import annotations

import asyncio
import json
import shutil
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from . import Tool, register


def _kill_proc(proc: asyncio.subprocess.Process) -> None:
    """尽力杀掉子进程并回收；已退出 / 查无此进程则忽略。"""
    if proc.returncode is not None:
        return
    try:
        proc.kill()
    except ProcessLookupError:
        pass


async def _run(args: list[str], input_text: str | None = None, timeout: int = 20) -> tuple[int, str, str]:
    proc = await asyncio.create_subprocess_exec(
        *args,
        stdin=asyncio.subprocess.PIPE if input_text is not None else None,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        out, err = await asyncio.wait_for(
            proc.communicate(input_text.encode() if input_text is not None else None),
            timeout=timeout,
        )
    except TimeoutError:
        # 超时必须杀掉子进程，否则变成僵尸进程继续在后台跑
        _kill_proc(proc)
        out, err = await proc.communicate()
        raise
    except asyncio.CancelledError:
        # 外部取消（用户停止 / SSE 断开 / 删会话）也必须回收子进程，
        # 否则卡在 communicate 里的长命令（如 60s 下载）会变成孤儿进程残留。
        _kill_proc(proc)
        raise
    return proc.returncode or 0, out.decode("utf-8", errors="replace").strip(), err.decode("utf-8", errors="replace").strip()


def _bin(name: str) -> str | None:
    return shutil.which(name)


def _unavailable(tool_name: str) -> dict:
    return {
        "ok": False,
        "error": f"termux-api 未安装（{tool_name}），请先执行：pkg install termux-api",
    }


def _ok(**kw: Any) -> dict:
    return {"ok": True, **kw}


def _fail(msg: str) -> dict:
    return {"ok": False, "error": msg}


def _safe_http_url(url: str) -> str | None:
    """校验 URL 仅允许 http/https scheme，拒绝 file:// / javascript: / data: 等。

    下载/打开工具的参数来自 LLM（可能被外部内容注入），限制 scheme 可防止误把
    file:///etc/passwd 当 URL 下载、或用 javascript:  scheme 触发异常行为。
    返回 None 表示非法。
    """
    if not isinstance(url, str) or not url.strip():
        return None
    u = urlparse(url.strip())
    if u.scheme not in ("http", "https") or not u.netloc:
        return None
    return url.strip()


# ==================== 系统 ====================

async def battery() -> dict:
    b = _bin("termux-battery-status")
    if not b:
        return _unavailable("termux-battery-status")
    try:
        code, out, err = await _run([b])
        data = json.loads(out) if out else {}
        # termux-battery-status 真实输出是单个 JSON 对象；个别封装/旧版可能返回
        # 单元素数组，两种形态都兼容（数组则取第一项）。
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
    try:
        await _run(args)
        return _ok()
    except TimeoutError:
        return _fail("发送通知超时")


async def toast(message: str) -> dict:
    b = _bin("termux-toast")
    if not b:
        return _unavailable("termux-toast")
    try:
        await _run([b, "-s", str(message)])
        return _ok()
    except TimeoutError:
        return _fail("toast 超时")


async def vibrate(duration_ms: int = 500) -> dict:
    b = _bin("termux-vibrate")
    if not b:
        return _unavailable("termux-vibrate")
    try:
        await _run([b, "-d", str(max(50, min(10000, duration_ms)))])
        return _ok(vibrated_ms=duration_ms)
    except TimeoutError:
        return _fail("震动超时")


async def torch(on: bool) -> dict:
    b = _bin("termux-torch")
    if not b:
        return _unavailable("termux-torch")
    try:
        await _run([b, "on" if on else "off"])
        return _ok(torch="on" if on else "off")
    except TimeoutError:
        return _fail("手电筒控制超时")


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
    return _ok(stream=stream, volume=None, note="未找到该流（可用流：music/ring/notification/alarm/call/system）")


async def set_volume(stream: str, level: int) -> dict:
    b = _bin("termux-volume")
    if not b:
        return _unavailable("termux-volume")
    # termux-volume 用法：`termux-volume <stream> <level>`（位置参数，无 -s/-v 选项）。
    # 音量范围因设备/流而异（音乐流通常 0-15），这里按设备实际 clamp。
    try:
        await _run([b, str(stream), str(max(0, min(100, level)))])
        return _ok(stream=stream, level=level)
    except TimeoutError:
        return _fail("设置音量超时")


async def set_brightness(level: int) -> dict:
    b = _bin("termux-brightness")
    if not b:
        return _unavailable("termux-brightness")
    try:
        await _run([b, str(max(0, min(255, level)))])
        return _ok(brightness=level)
    except TimeoutError:
        return _fail("设置亮度超时")


# ==================== 通信 ====================

async def send_sms(numbers: list[str], text: str) -> dict:
    b = _bin("termux-sms-send")
    if not b:
        return _unavailable("termux-sms-send")
    if not numbers:
        return _fail("缺少收件人号码")
    # termux-sms-send 用法：`-n number[,number2,...] [text]`，多收件人用逗号合并到
    # 同一个 -n 后面（重复 -n 会被 getopts 覆盖，只发最后一个）。文本无位置参数时从 stdin 读。
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
    # termux-sms-list 用法：`-l <limit> -t inbox|sent|draft|outbox|all`（无 termux-sms-inbox 命令）。
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


# ==================== 隐私 / 位置 ====================

async def get_location(provider: str = "network") -> dict:
    b = _bin("termux-location")
    if not b:
        return _unavailable("termux-location")
    if provider not in ("gps", "network", "passive"):
        provider = "network"
    # termux-location 用法：`-p gps|network|passive -r once|last|updates`；
    # -r 取值是 once/last/updates，不是数字（-r 1 会被拒绝）。这里只要一次性定位。
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


# ==================== 剪贴板 ====================

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


# ==================== 传感器 ====================

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


# ==================== 语音 ====================

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


# ==================== 网络 ====================

async def wifi_info() -> dict:
    b = _bin("termux-wifi-connectioninfo")
    if not b:
        return _unavailable("termux-wifi-connectioninfo")
    try:
        code, out, err = await _run([b])
    except TimeoutError:
        return _fail("读取 WiFi 信息超时")
    if code != 0:
        return _fail(f"termux-wifi-connectioninfo 失败：{err or f'exit {code}'}")
    try:
        data = json.loads(out) if out else {}
    except json.JSONDecodeError:
        return _fail("WiFi 信息解析失败")
    return _ok(
        connected=data.get("supplicant_state") == "COMPLETED" if data.get("supplicant_state") else None,
        ssid=data.get("ssid"),
        ip=data.get("ip"),
        bssid=data.get("bssid"),
        rssi=data.get("rssi"),
        link_speed_mbps=data.get("link_speed_mbps"),
    )


async def scan_wifi() -> dict:
    b = _bin("termux-wifi-scaninfo")
    if not b:
        return _unavailable("termux-wifi-scaninfo")
    # termux-wifi-scaninfo 无选项，直接返回最近一次扫描结果（JSON 数组）。
    try:
        code, out, err = await _run([b], timeout=30)
    except TimeoutError:
        return _fail("WiFi 扫描超时")
    if code != 0:
        return _fail(f"termux-wifi-scaninfo 失败：{err or f'exit {code}'}")
    try:
        data = json.loads(out) if out else []
    except json.JSONDecodeError:
        return _fail("WiFi 扫描结果解析失败")
    nets = []
    for n in (data if isinstance(data, list) else [])[:15]:
        if isinstance(n, dict):
            nets.append({
                "ssid": n.get("ssid"),
                "bssid": n.get("bssid"),
                "level_dbm": n.get("level"),
                "frequency_mhz": n.get("frequency"),
            })
    return _ok(count=len(nets), networks=nets)


async def set_wifi(enabled: bool) -> dict:
    b = _bin("termux-wifi-enable")
    if not b:
        return _unavailable("termux-wifi-enable")
    try:
        await _run([b, "true" if enabled else "false"])
        return _ok(wifi_enabled=enabled)
    except TimeoutError:
        return _fail("WiFi 开关超时")


# ==================== 媒体 / 文件 ====================

async def take_photo(output: str = "") -> dict:
    b = _bin("termux-camera-photo")
    if not b:
        return _unavailable("termux-camera-photo")
    path = output or str(Path.home() / "storage" / "pictures" / f"agent-{int(time.time())}.jpg")
    # termux-camera-photo 用法：`-c <camera-id> <output-file>`，输出文件是位置参数（无 -o 选项）。
    try:
        code, out, err = await _run([b, "-c", "0", str(path)], timeout=30)
    except TimeoutError:
        return _fail("拍照超时")
    if code != 0:
        return _fail(f"拍照失败：{err or f'exit {code}'}（可能需要先 termux-setup-storage）")
    return _ok(saved_to=str(path))


async def share_text(text: str, title: str = "") -> dict:
    b = _bin("termux-share")
    if not b:
        return _unavailable("termux-share")
    # termux-share 用法：`-a send|edit|view -t <title>`（-a 取值是 send/edit/view，
    # 不是完整 intent 字符串；从 stdin 读文本时默认 content-type 为 text/plain）。
    args = [b, "-a", "send"]
    if title:
        args += ["-t", str(title)]
    try:
        await _run(args, input_text=str(text))
        return _ok()
    except TimeoutError:
        return _fail("调起分享超时")


async def download_file(url: str, output: str = "") -> dict:
    b = _bin("termux-download")
    if not b:
        return _unavailable("termux-download")
    safe = _safe_http_url(url)
    if safe is None:
        return _fail("URL 非法：仅支持 http/https 链接（拒绝 file:/javascript:/data: 等）")
    path = output or str(Path.home() / "storage" / "downloads")
    # termux-download 用法：`-p <path> [-t title] [-d desc] <url>`（路径选项是 -p，不是 -o）。
    try:
        await _run([b, "-p", str(path), safe], timeout=60)
        return _ok(url=safe, saved_to=str(path))
    except TimeoutError:
        return _fail("下载超时（网络慢或文件大）")


async def open_target(target: str) -> dict:
    b = _bin("termux-open")
    if not b:
        return _unavailable("termux-open")
    # target 既可能是本地文件路径，也可能是 URL：URL 才做 scheme 校验；
    # 看起来像 URL（含 :// 或 scheme:）但不是 http(s) 的一律拒绝。
    t = str(target).strip()
    if "://" in t or ":" in t.split("/", 1)[0]:
        if _safe_http_url(t) is None:
            return _fail("仅支持 http/https 链接或本地路径（拒绝 file:/javascript:/data: 等）")
    try:
        await _run([b, t], timeout=15)
        return _ok(opened=t)
    except TimeoutError:
        return _fail("打开超时")


# ==================== 注册表 ====================

def _t(name: str, description: str, properties: dict, risk: str, handler, summary: str, required: list | None = None, timeout: int = 20) -> None:
    register(Tool(
        name=name,
        description=description,
        parameters={"type": "object", "properties": properties, **({"required": required} if required else {})},
        risk=risk,
        handler=handler,
        summary=summary,
        timeout=timeout,
        group="phone",
    ))


# 系统
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

# 通信
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

# 隐私 / 位置
_t("get_location", "获取手机当前位置（经纬度，涉及隐私，需确认）。",
   {"provider": {"type": "string", "description": "gps/network/passive，默认 network"}},
   "write", get_location, "获取当前位置")
_t("get_clipboard", "读取手机剪贴板当前内容。", {}, "safe", clipboard_get, "读取剪贴板")
_t("set_clipboard", "把指定文本写入手机剪贴板。",
   {"text": {"type": "string", "description": "要写入剪贴板的文本"}},
   "write", clipboard_set, "写入剪贴板", required=["text"])

# 传感器
_t("list_sensors", "列出手机上可用的传感器。", {}, "safe", list_sensors, "列出传感器")
_t("read_sensor", "读取指定传感器的当前读数（如 accelerometer/gyroscope/proximity）。",
   {"name": {"type": "string", "description": "传感器名称"}},
   "safe", read_sensor, "读取传感器读数", required=["name"])

# 语音
_t("tts_speak", "用手机扬声器朗读一段文字（TTS 语音合成）。",
   {"text": {"type": "string", "description": "要朗读的文字"}, "rate": {"type": "number", "description": "语速 0-2，默认 1.0"}},
   "write", tts_speak, "语音朗读文字", required=["text"])
_t("speech_to_text", "打开麦克风识别一段语音并返回文字（会录 1-2 秒环境声音，需确认）。",
   {"lang": {"type": "string", "description": "语言代码，默认 zh-CN"}},
   "write", speech_to_text, "语音转文字", timeout=50)

# 网络
_t("get_wifi_info", "查询当前 WiFi 连接信息（SSID、IP、信号强度）。", {}, "safe", wifi_info, "查询 WiFi 信息")
_t("scan_wifi", "扫描附近 WiFi 网络（最多返回 15 个）。", {}, "safe", scan_wifi, "扫描附近 WiFi")
_t("set_wifi", "打开或关闭 WiFi。",
   {"enabled": {"type": "boolean", "description": "true=开，false=关"}},
   "write", set_wifi, "开关 WiFi", required=["enabled"])

# 媒体 / 文件
_t("take_photo", "用后置摄像头拍一张照片并保存（输出路径可选，默认存到 pictures 目录）。",
   {"output": {"type": "string", "description": "保存路径（可选）"}},
   "write", take_photo, "拍摄一张照片", timeout=35)
_t("share_text", "调起系统分享面板分享一段文字。",
   {"text": {"type": "string", "description": "要分享的文字"}, "title": {"type": "string", "description": "分享标题（可选）"}},
   "write", share_text, "分享文字", required=["text"])
_t("download_file", "下载一个文件到手机（默认存到下载目录）。",
   {"url": {"type": "string", "description": "文件 URL"}, "output": {"type": "string", "description": "保存路径（可选）"}},
   "write", download_file, "下载文件", required=["url"], timeout=65)
_t("open_target", "用系统默认应用打开一个文件或链接。",
   {"target": {"type": "string", "description": "文件路径或 URL"}},
   "write", open_target, "打开文件或链接", required=["target"])
