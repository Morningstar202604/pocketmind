"""通知栏持久进度（P1）：调用 termux-notification，运行期进度可见可回溯。

零依赖：直接调二进制，失败静默（没有 termux-api 的环境自动降级为无通知）。
--id 固定，同一任务更新同一通知、完成后清除，避免刷屏。
"""
from __future__ import annotations

import asyncio
import os
import shutil
import subprocess

_NOTIFY_ID = "pocket-agent"


def _tap_url() -> str:
    """通知点击后要打开的本机 Web UI 地址。默认 127.0.0.1:8787，可用环境变量覆盖。"""
    return os.environ.get("AGENT_WEB_URL", "http://127.0.0.1:8787/")


def notify(title: str, content: str, persistent: bool = False) -> bool:
    """发/更新一条通知栏消息。persistent=True 时点击通知打开 Agent 页面。"""
    exe = shutil.which("termux-notification")
    if not exe:
        return False
    cmd = [exe, "--id", _NOTIFY_ID, "--title", title[:40], "--content", content[:200]]
    if persistent:
        # termux-notification 的 --action 是「点击通知时执行的 shell 命令」，不是
        # Android intent action 字符串；--action-data 根本不是有效选项（会导致整条
        # 通知被 termux-notification 拒绝）。正确做法：点击时用 termux-open-url 打开本机页面。
        # --ongoing 把通知钉住（不可滑动清除），配合 termux-wake-lock 维持后台存活。
        cmd += ["--ongoing", "--action", f"termux-open-url {_tap_url()}"]
    try:
        subprocess.run(cmd, capture_output=True, timeout=4)
        return True
    except Exception:  # noqa: BLE001 —— 通知失败不影响主流程
        return False


async def notify_async(title: str, content: str, persistent: bool = False) -> bool:
    """notify() 的异步版：subprocess.run 是阻塞系统调用，不能直接在事件循环里跑，
    否则最长会卡住 loop 4s。丢到线程池执行，调用方 await 即可。"""
    return await asyncio.to_thread(notify, title, content, persistent)


def clear() -> bool:
    """移除当前通知。"""
    exe = shutil.which("termux-notification-remove")
    if not exe:
        return False
    try:
        subprocess.run([exe, _NOTIFY_ID], capture_output=True, timeout=4)
        return True
    except Exception:  # noqa: BLE001
        return False
