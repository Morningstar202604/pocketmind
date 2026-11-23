package com.pocket.agent.runtime

import android.os.ParcelFileDescriptor
import java.io.File
import java.io.FileInputStream
import java.io.FileOutputStream

/**
 * 原生 PTY 的 Kotlin 封装。JNI 只负责 forkpty 级别的系统调用；
 * 读写由 ParcelFileDescriptor 暴露的 fd 完成，避免 JNI 里做阻塞 IO。
 *
 * 如果 ABI 没有对应的 libpty.so（例如 JVM 单测 / 不支持的 CPU），
 * [available] 为 false，工作区会退回命令台而不是假装终端可用。
 */
object NativePty {
    @Volatile
    var available: Boolean = false
        private set

    init {
        available = runCatching { System.loadLibrary("pty") }.isSuccess
    }

    private external fun start(
        cwd: String,
        argv: Array<String>,
        extraEnv: Array<String>,
        home: String,
        tmp: String,
        cols: Int,
        rows: Int,
    ): IntArray?

    private external fun resize(fd: Int, cols: Int, rows: Int)
    private external fun close(fd: Int, pid: Int)

    class Session internal constructor(
        val fd: Int,
        val pid: Int,
        private val master: ParcelFileDescriptor,
        val input: FileInputStream,
        val output: FileOutputStream,
    ) {
        fun resize(cols: Int, rows: Int) {
            if (available) runCatching { NativePty.resize(fd, cols, rows) }
        }

        fun close() {
            runCatching { input.close() }
            runCatching { output.close() }
            runCatching { master.close() }
            if (available) runCatching { NativePty.close(fd, pid) }
        }
    }

    fun open(
        cwd: File,
        home: File,
        tmp: File,
        cols: Int,
        rows: Int,
        argv: List<String> = emptyList(),
        extraEnv: List<String> = emptyList(),
    ): Session? {
        if (!available) return null
        val shell = listOf("/system/bin/sh", "/bin/sh").firstOrNull { File(it).exists() } ?: return null
        val command = argv.ifEmpty { listOf(shell, "-i") }
        val pair = runCatching {
            start(cwd.absolutePath, command.toTypedArray(), extraEnv.toTypedArray(),
                home.absolutePath, tmp.absolutePath, cols, rows)
        }.getOrNull() ?: return null
        if (pair.size != 2) return null
        val fd = pair[0]
        val pid = pair[1]
        if (fd < 0 || pid <= 0) return null
        val pfd = ParcelFileDescriptor.adoptFd(fd)
        return try {
            Session(fd, pid, pfd, FileInputStream(pfd.fileDescriptor), FileOutputStream(pfd.fileDescriptor))
        } catch (e: Exception) {
            runCatching { pfd.close() }
            runCatching { close(fd, pid) }
            null
        }
    }
}
