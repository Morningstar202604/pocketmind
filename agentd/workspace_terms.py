"""PTY 终端会话池：断线 30 秒内保留 shell，重连接管同一进程。

为什么需要会话池：
- 用户切后台、锁屏、网络抖动都会让 WebSocket 断开；旧实现一断就 SIGKILL，
  `npm run dev`、`vim` 里没保存的 buffer 全没了；
- 重连不能只是「再开一个 shell」——那等于把工作现场扔掉。

协议：
- 服务端先发一条 JSON 控制帧 `{"type":"ready","session_id":"...","reconnected":bool}`；
- 其余输出仍按原始文本发送，xterm.js 直接 write；
- 客户端输入 `{"type":"input","data":"..."}`、`{"type":"resize","cols":..,"rows":..}`。

边界：只保留一个滚动缓冲（20KB），不做完整终端录像；进程退出后会话即回收。
"""
from __future__ import annotations

import asyncio
import json
import os
import shutil
import subprocess
import time
import uuid
from dataclasses import dataclass, field

from fastapi import WebSocket, WebSocketDisconnect

GRACE_SECONDS = 30
MAX_BUFFER = 20_000


def _set_winsize(fd: int, cols: int, rows: int) -> None:
    import fcntl
    import struct
    import termios

    cols = max(20, min(int(cols or 80), 500))
    rows = max(5, min(int(rows or 24), 200))
    fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))


def _kill_term(proc: subprocess.Popen) -> None:
    import signal

    if proc.poll() is not None:
        return
    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
    except (ProcessLookupError, OSError):
        try:
            proc.kill()
        except ProcessLookupError:
            pass


def _spawn_shell(cwd: str) -> tuple[int, subprocess.Popen]:
    """同步启动 PTY shell；放到线程里调用，避免阻塞事件循环。"""
    import pty

    shell = shutil.which("bash") or "/bin/sh"
    env = os.environ.copy()
    env["TERM"] = "xterm-256color"
    env["PWD"] = cwd
    master, slave = pty.openpty()
    try:
        _set_winsize(master, 80, 24)
        proc = subprocess.Popen(
            [shell, "-i"],
            stdin=slave,
            stdout=slave,
            stderr=slave,
            cwd=cwd,
            env=env,
            start_new_session=True,
            close_fds=True,
        )
    except OSError:
        os.close(master)
        os.close(slave)
        raise
    os.close(slave)
    return master, proc


@dataclass
class _Session:
    id: str
    cwd: str
    master: int
    proc: subprocess.Popen
    created_at: float = field(default_factory=time.time)
    last_seen: float = field(default_factory=time.time)
    buffer: str = ""
    subscribers: set[WebSocket] = field(default_factory=set)
    pump_task: asyncio.Task | None = None
    cleanup_task: asyncio.Task | None = None
    dead: bool = False


class TerminalHub:
    def __init__(self) -> None:
        self._sessions: dict[str, _Session] = {}

    async def _create(self, cwd: str) -> _Session:
        master, proc = await asyncio.to_thread(_spawn_shell, cwd)
        sess = _Session(id=uuid.uuid4().hex[:10], cwd=cwd, master=master, proc=proc)
        self._sessions[sess.id] = sess
        sess.pump_task = asyncio.create_task(self._pump(sess))
        return sess

    async def _pump(self, sess: _Session) -> None:
        loop = asyncio.get_running_loop()
        try:
            while True:
                data = await loop.run_in_executor(None, os.read, sess.master, 4096)
                if not data:
                    break
                text = data.decode("utf-8", errors="replace")
                sess.buffer = (sess.buffer + text)[-MAX_BUFFER:]
                for ws in list(sess.subscribers):
                    try:
                        await ws.send_text(text)
                    except (WebSocketDisconnect, RuntimeError, OSError):
                        sess.subscribers.discard(ws)
        except (OSError, RuntimeError):
            pass
        finally:
            sess.dead = True
            for ws in list(sess.subscribers):
                try:
                    await ws.close(code=1000, reason="shell 已退出")
                except (RuntimeError, OSError):
                    pass
            sess.subscribers.clear()
            self._sessions.pop(sess.id, None)
            try:
                os.close(sess.master)
            except OSError:
                pass
            try:
                sess.proc.wait(timeout=1)
            except subprocess.TimeoutExpired:
                _kill_term(sess.proc)

    async def _cleanup_after(self, sess: _Session) -> None:
        try:
            await asyncio.sleep(GRACE_SECONDS)
        except asyncio.CancelledError:
            return
        if sess.subscribers:
            return
        self._sessions.pop(sess.id, None)
        _kill_term(sess.proc)
        try:
            os.close(sess.master)
        except OSError:
            pass
        if sess.pump_task:
            sess.pump_task.cancel()

    def _schedule_cleanup(self, sess: _Session) -> None:
        if sess.cleanup_task and not sess.cleanup_task.done():
            sess.cleanup_task.cancel()
        sess.cleanup_task = asyncio.create_task(self._cleanup_after(sess))

    async def serve(self, websocket: WebSocket, cwd: str, requested_id: str | None = None) -> None:
        if os.name != "posix":
            await websocket.close(code=1008, reason="当前运行时没有 POSIX PTY；请用 Termux/Linux")
            return
        await websocket.accept()
        sess: _Session | None = None
        reconnected = False
        if requested_id:
            candidate = self._sessions.get(requested_id)
            if candidate and not candidate.dead and candidate.cwd == cwd:
                sess = candidate
                reconnected = True
        if sess is None:
            try:
                sess = await self._create(cwd)
            except OSError as exc:
                await websocket.send_text(json.dumps({"type": "error", "message": f"启动 shell 失败：{exc}"}, ensure_ascii=False))
                await websocket.close(code=1011)
                return
        if sess.cleanup_task and not sess.cleanup_task.done():
            sess.cleanup_task.cancel()
            sess.cleanup_task = None
        sess.subscribers.add(websocket)
        sess.last_seen = time.time()
        await websocket.send_text(json.dumps({
            "type": "ready", "session_id": sess.id, "reconnected": reconnected,
        }, ensure_ascii=False))
        if sess.buffer:
            await websocket.send_text(sess.buffer)

        try:
            while True:
                raw = await websocket.receive_text()
                try:
                    msg = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                kind = msg.get("type")
                if kind == "input":
                    data = msg.get("data")
                    if isinstance(data, str) and data:
                        try:
                            os.write(sess.master, data.encode("utf-8", errors="replace"))
                        except OSError:
                            break
                elif kind == "resize":
                    try:
                        _set_winsize(sess.master, int(msg.get("cols", 80)), int(msg.get("rows", 24)))
                    except (OSError, ValueError, TypeError):
                        pass
        except WebSocketDisconnect:
            pass
        finally:
            sess.subscribers.discard(websocket)
            sess.last_seen = time.time()
            if not sess.subscribers and not sess.dead:
                self._schedule_cleanup(sess)

    async def shutdown(self) -> None:
        for sess in list(self._sessions.values()):
            _kill_term(sess.proc)
            try:
                os.close(sess.master)
            except OSError:
                pass
            if sess.pump_task:
                sess.pump_task.cancel()
        self._sessions.clear()


_hub: TerminalHub | None = None


def get_hub() -> TerminalHub:
    global _hub
    if _hub is None:
        _hub = TerminalHub()
    return _hub
