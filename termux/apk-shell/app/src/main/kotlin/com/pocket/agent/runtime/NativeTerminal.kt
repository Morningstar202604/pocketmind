package com.pocket.agent.runtime

import org.json.JSONObject
import java.io.File
import java.nio.charset.StandardCharsets
import java.util.UUID
import java.util.concurrent.ConcurrentHashMap

/**
 * PTY 会话池：断线 30 秒内保留 shell，重连接管同一进程。
 *
 * 协议与 Python `workspace_terms.py` 一致：
 *  - 服务端先发 `{"type":"ready","session_id":..,"reconnected":bool}`；
 *  - 其余输出是原始文本帧，xterm.js 直接写；
 *  - 客户端发 `{"type":"input","data":..}` / `{"type":"resize","cols":..,"rows":..}`。
 *
 * NativePty 不可用时（JVM 单测 / 不支持的 ABI）返回 error 控制帧，
 * 工作区 UI 会退回命令台。
 */
object NativeTerminalHub {
    private val sessions = ConcurrentHashMap<String, TerminalSession>()

    fun serve(
        ws: NativeWebSocket,
        cwd: File,
        home: File,
        tmp: File,
        requested: String?,
        mode: String = "shell",
        argv: List<String> = emptyList(),
        extraEnv: List<String> = emptyList(),
    ) {
        val existing = requested?.let { sessions[it] }
        val session = if (existing != null && !existing.dead && existing.cwd == cwd && existing.mode == mode) {
            existing
        } else {
            val pty = NativePty.open(cwd, home, tmp, 80, 24, argv, extraEnv)
            if (pty == null) {
                ws.sendText(JSONObject().put("type", "error")
                    .put("message", "当前 ABI 没有 PTY 支持，请改用命令台").toString())
                ws.close(1011)
                return
            }
            TerminalSession(UUID.randomUUID().toString().replace("-", "").take(10), cwd, mode, pty)
                .also { sessions[it.id] = it }
        }

        session.attach(ws, existing === session)
        try {
            while (ws.open) {
                val frame = ws.readFrame() ?: break
                when (frame.opcode) {
                    0x1, 0x2 -> handleMessage(session, frame.payload)
                    0x8 -> break
                    0x9 -> ws.sendPong(frame.payload)
                    else -> Unit
                }
            }
        } finally {
            session.detach()
            ws.close()
        }
    }

    private fun handleMessage(session: TerminalSession, payload: ByteArray) {
        val text = String(payload, StandardCharsets.UTF_8)
        val msg = runCatching { JSONObject(text) }.getOrNull() ?: return
        when (msg.optString("type")) {
            "input" -> msg.optString("data").takeIf { it.isNotEmpty() }?.let { session.write(it) }
            "resize" -> session.resize(msg.optInt("cols", 80), msg.optInt("rows", 24))
        }
    }

    fun shutdown() {
        sessions.values.forEach { it.shutdown() }
        sessions.clear()
    }

    private class TerminalSession(
        val id: String,
        val cwd: File,
        val mode: String,
        private val pty: NativePty.Session,
    ) {
        @Volatile var dead = false
            private set
        @Volatile private var ws: NativeWebSocket? = null
        @Volatile private var lastSeen = System.currentTimeMillis()
        private var cleanup: Thread? = null
        private val buffer = StringBuilder()
        private val bufferLock = Any()
        private val pump = Thread({ pump() }, "native-pty-$id").apply { isDaemon = true }

        init {
            pump.start()
        }

        fun attach(ws: NativeWebSocket, reconnected: Boolean) {
            this.ws = ws
            lastSeen = System.currentTimeMillis()
            cleanup?.interrupt()
            cleanup = null
            ws.sendText(JSONObject()
                .put("type", "ready").put("session_id", id).put("reconnected", reconnected).toString())
            val replay = synchronized(bufferLock) { buffer.toString() }
            if (replay.isNotEmpty()) ws.sendText(replay)
        }

        fun detach() {
            ws = null
            lastSeen = System.currentTimeMillis()
            scheduleCleanup()
        }

        fun write(data: String) {
            runCatching {
                pty.output.write(data.toByteArray(StandardCharsets.UTF_8))
                pty.output.flush()
            }
        }

        fun resize(cols: Int, rows: Int) {
            pty.resize(cols.coerceIn(20, 500), rows.coerceIn(5, 200))
        }

        private fun pump() {
            val buf = ByteArray(4096)
            try {
                while (!dead) {
                    val n = pty.input.read(buf)
                    if (n < 0) break
                    if (n == 0) continue
                    val text = String(buf, 0, n, StandardCharsets.UTF_8)
                    synchronized(bufferLock) {
                        buffer.append(text)
                        if (buffer.length > MAX_BUFFER) buffer.delete(0, buffer.length - MAX_BUFFER)
                    }
                    lastSeen = System.currentTimeMillis()
                    if (!(ws?.sendText(text) ?: true)) {
                        ws = null
                        scheduleCleanup()
                    }
                }
            } catch (_: Exception) {
            } finally {
                dead = true
                ws?.close()
                ws = null
                sessions.remove(id)
                runCatching { pty.close() }
            }
        }

        private fun scheduleCleanup() {
            if (cleanup?.isAlive == true) return
            cleanup = Thread {
                try {
                    Thread.sleep(GRACE_MILLIS)
                } catch (_: InterruptedException) {
                    return@Thread
                }
                if (ws == null && !dead) {
                    sessions.remove(id)
                    runCatching { pty.close() }
                    dead = true
                }
            }.apply { isDaemon = true; start() }
        }

        fun shutdown() {
            dead = true
            ws?.close()
            ws = null
            runCatching { pty.close() }
        }
    }

    private const val GRACE_MILLIS = 30_000L
    private const val MAX_BUFFER = 20_000
}
