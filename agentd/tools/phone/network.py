"""网络工具：WiFi 信息 / 扫描 / 开关。"""
from __future__ import annotations

import json

from . import _t
from ._termux import _bin, _fail, _ok, _run, _run_checked, _unavailable


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
        link_speed_mbps=data.get("link_speed_mbps") or data.get("link_speed"),
    )


async def scan_wifi() -> dict:
    b = _bin("termux-wifi-scaninfo")
    if not b:
        return _unavailable("termux-wifi-scaninfo")
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
    failed = await _run_checked([b, "true" if enabled else "false"], "切换 WiFi")
    if failed:
        return failed
    return _ok(wifi_enabled=enabled)


# ---------- 注册表 ----------
_t("get_wifi_info", "查询当前 WiFi 连接信息（SSID、IP、信号强度）。", {}, "safe", wifi_info, "查询 WiFi 信息")
# timeout=40：内部扫描用 30s，工具级留余量（同上）
_t("scan_wifi", "扫描附近 WiFi 网络（最多返回 15 个）。", {}, "safe", scan_wifi, "扫描附近 WiFi", timeout=40)
_t("set_wifi", "打开或关闭 WiFi。",
   {"enabled": {"type": "boolean", "description": "true=开，false=关"}},
   "write", set_wifi, "开关 WiFi", required=["enabled"])
