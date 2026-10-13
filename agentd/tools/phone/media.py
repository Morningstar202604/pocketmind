"""媒体工具：拍照 / 分享 / 下载 / 打开。"""
from __future__ import annotations

import time
from pathlib import Path

from . import _t
from ._termux import _bin, _fail, _ok, _run, _safe_http_url, _unavailable


async def take_photo(output: str = "") -> dict:
    b = _bin("termux-camera-photo")
    if not b:
        return _unavailable("termux-camera-photo")
    path = output or str(Path.home() / "storage" / "pictures" / f"agent-{int(time.time())}.jpg")
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
    try:
        await _run([b, "-p", str(path), safe], timeout=60)
        return _ok(url=safe, saved_to=str(path))
    except TimeoutError:
        return _fail("下载超时（网络慢或文件大）")


async def open_target(target: str) -> dict:
    b = _bin("termux-open")
    if not b:
        return _unavailable("termux-open")
    t = str(target).strip()
    if "://" in t or ":" in t.split("/", 1)[0]:
        if _safe_http_url(t) is None:
            return _fail("仅支持 http/https 链接或本地路径（拒绝 file:/javascript:/data: 等）")
    try:
        await _run([b, t], timeout=15)
        return _ok(opened=t)
    except TimeoutError:
        return _fail("打开超时")


# ---------- 注册表 ----------
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
