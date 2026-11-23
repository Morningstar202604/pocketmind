package com.pocket.agent.runtime

import java.io.BufferedInputStream
import java.io.IOException
import java.io.OutputStream
import java.net.Socket
import java.nio.charset.StandardCharsets
import java.security.MessageDigest
import java.util.Base64

/**
 * 最小 WebSocket 服务端：只做 RFC 6455 的握手、文本帧收发、ping/pong 与 close。
 *
 * 原生引擎的 HTTP 解析器已经把请求头读完，握手成功后直接复用同一个 socket
 * 的输入/输出流。没有引入 Netty/OkHttp：终端只需要文本帧，给 APK 增重不值得。
 */
class NativeWebSocket(
    private val socket: Socket,
    private val input: BufferedInputStream,
    private val out: OutputStream,
) {
    @Volatile var open = true
        private set

    @Synchronized
    fun sendText(text: String): Boolean {
        if (!open) return false
        return try {
            writeFrame(0x1, text.toByteArray(StandardCharsets.UTF_8))
            true
        } catch (_: IOException) {
            open = false
            false
        }
    }

    @Synchronized
    fun sendPong(payload: ByteArray): Boolean {
        if (!open) return false
        return try {
            writeFrame(0xA, payload)
            true
        } catch (_: IOException) {
            open = false
            false
        }
    }

    /** 读一帧；返回 opcode + payload。连接断开或帧非法时返回 null。 */
    fun readFrame(): Frame? {
        return try {
            val b0 = readByte() ?: return null
            val b1 = readByte() ?: return null
            val fin = (b0 and 0x80) != 0
            val opcode = b0 and 0x0f
            val masked = (b1 and 0x80) != 0
            var len = (b1 and 0x7f).toLong()
            if (len == 126L) {
                len = ((readByte() ?: return null) shl 8 or (readByte() ?: return null)).toLong()
            } else if (len == 127L) {
                len = 0
                repeat(8) { len = (len shl 8) or (readByte() ?: return null).toLong() }
            }
            if (len < 0 || len > MAX_FRAME_BYTES) return null
            val mask = if (masked) {
                ByteArray(4) { (readByte() ?: return null).toByte() }
            } else null
            val payload = ByteArray(len.toInt())
            var read = 0
            while (read < payload.size) {
                val n = input.read(payload, read, payload.size - read)
                if (n < 0) return null
                read += n
            }
            if (mask != null) {
                for (i in payload.indices) payload[i] = (payload[i].toInt() xor mask[i % 4].toInt()).toByte()
            }
            Frame(fin, opcode, payload)
        } catch (_: IOException) {
            null
        }
    }

    @Synchronized
    fun close(code: Int = 1000) {
        if (!open) return
        open = false
        try {
            val payload = ByteArray(2)
            payload[0] = ((code shr 8) and 0xff).toByte()
            payload[1] = (code and 0xff).toByte()
            writeFrame(0x8, payload)
        } catch (_: IOException) {
        }
        try { socket.close() } catch (_: IOException) { }
    }

    private fun writeFrame(opcode: Int, payload: ByteArray) {
        val header = ArrayList<Byte>(10)
        header.add((0x80 or opcode).toByte())
        val len = payload.size
        when {
            len < 126 -> header.add(len.toByte())
            len < 65536 -> {
                header.add(126.toByte())
                header.add((len shr 8).toByte())
                header.add(len.toByte())
            }
            else -> {
                header.add(127.toByte())
                for (shift in 56 downTo 0 step 8) header.add(((len.toLong() shr shift) and 0xff).toByte())
            }
        }
        synchronized(this) {
            out.write(header.toByteArray())
            if (len > 0) out.write(payload)
            out.flush()
        }
    }

    private fun readByte(): Int? {
        val b = input.read()
        return if (b < 0) null else b
    }

    data class Frame(val fin: Boolean, val opcode: Int, val payload: ByteArray)

    companion object {
        const val GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
        private const val MAX_FRAME_BYTES = 2L * 1024 * 1024

        fun acceptKey(key: String): String {
            val sha1 = MessageDigest.getInstance("SHA-1")
            val digest = sha1.digest((key + GUID).toByteArray(StandardCharsets.US_ASCII))
            return Base64.getEncoder().encodeToString(digest)
        }
    }
}
