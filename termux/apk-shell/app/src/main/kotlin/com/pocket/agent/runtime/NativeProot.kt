package com.pocket.agent.runtime

import android.content.Context
import android.os.Build
import org.json.JSONObject
import java.io.File
import java.io.FileOutputStream
import java.io.InputStream
import java.nio.charset.StandardCharsets
import java.nio.file.Files
import java.nio.file.LinkOption
import java.nio.file.StandardCopyOption
import java.util.zip.GZIPInputStream

/**
 * PRoot 运行环境：Alpine rootfs（默认 123MB 预装开发工具链，回退 9MB minirootfs） + Termux 的 proot 二进制。
 *
 * 为什么打包而不是让用户 apt install：APK 的目标是「装上就有 Linux」。
 * proot 与 libtalloc/libandroid-shmem 放在 jniLibs，安装后位于 nativeLibraryDir
 * （Android 10+ 唯一可靠的可执行目录）；rootfs 压缩在 assets，首次打开 Linux
 * 终端时解到 filesDir。
 *
 * 仍然不是 root：proot 用 ptrace 模拟 chroot/bind，权限仍是 app uid。
 */
object NativeProot {
    @Volatile private var rootfsReady = false
    @Volatile var lastError: String? = null
        private set

    fun available(context: Context): Boolean {
        if (!Build.SUPPORTED_ABIS.contains("arm64-v8a")) return false
        val libDir = File(context.applicationInfo.nativeLibraryDir)
        return File(libDir, "libproot.so").isFile && File(libDir, "libproot-loader.so").isFile
    }

    /** 不解压、不检查 rootfs，只判断是否已经准备好。状态接口轮询不能触发大包解压。 */
    fun rootfsIfReady(context: Context): File? {
        if (!available(context)) return null
        val root = File(File(context.filesDir, "proot"), "rootfs")
        return root.takeIf { File(it, ".pocketmind-rootfs").isFile }
    }

    fun rootfsBytes(context: Context): Long = rootfsIfReady(context)?.walkTopDown()
        ?.filter { it.isFile }?.sumOf { it.length() } ?: 0L

    @Synchronized
    fun rootfs(context: Context): File? {
        val base = File(context.filesDir, "proot")
        val root = File(base, "rootfs")
        val stamp = File(root, ".pocketmind-rootfs")
        if (rootfsReady && stamp.isFile) return root
        base.mkdirs()
        if (stamp.isFile) {
            rootfsReady = true
            return root
        }
        val tmp = File(base, "rootfs.tmp-${System.nanoTime()}")
        tmp.deleteRecursively()
        tmp.mkdirs()
        try {
            // 优先使用「预装开发工具链」的大包：Node/Python/Git/gcc 已在 rootfs 里，
            // 手机不联网也能开工。扩展名用 .gzip 而不是 .gz，避免 AGP 把资产自动解压成
            // 363MB 的 .tar；没有大包时回退到 9MB Alpine minirootfs（老构建/单元测试）。
            val devAsset = if (!Build.FINGERPRINT.contains("robolectric", ignoreCase = true))
                runCatching { context.assets.open("proot/alpine-dev-rootfs.tar.gzip") }.getOrNull()
            else null
            var prebuilt = false
            if (devAsset != null) {
                try {
                    devAsset.use { raw -> GZIPInputStream(raw).use { gz -> extractTar(gz, tmp) } }
                    prebuilt = true
                } catch (e: Exception) {
                    // 大包损坏不应让 Linux 终端彻底不可用；清空后回退 minirootfs。
                    lastError = "开发工具链解压失败，已回退 minirootfs：${e.message}"
                    tmp.deleteRecursively()
                    tmp.mkdirs()
                }
            }
            if (!prebuilt) {
                // AGP 的 mergeAssets 会把 .gz 资产自动解成同名 .tar（APK 里也是 .tar）；
                // 仍保留 .tar.gz 回退，避免构建工具行为变化时把 rootfs 当损坏。
                val tarName = "proot/alpine-minirootfs.tar"
                val opened = runCatching { context.assets.open(tarName) }.getOrNull()
                if (opened != null) {
                    opened.use { extractTar(it, tmp) }
                } else {
                    context.assets.open("proot/alpine-minirootfs.tar.gz").use { raw ->
                        GZIPInputStream(raw).use { gz -> extractTar(gz, tmp) }
                    }
                }
            }
            // Alpine 的 /etc/resolv.conf 默认可能是空的，补一个可用 DNS。
            File(tmp, "etc/resolv.conf").apply {
                parentFile?.mkdirs()
                writeText("nameserver 223.5.5.5\nnameserver 8.8.8.8\n", StandardCharsets.UTF_8)
            }
            File(tmp, ".pocketmind-rootfs").writeText("alpine 3.20.3\n")
            if (prebuilt) File(tmp, ".pocketmind-dev-env").writeText("prebuilt-full")
            if (root.exists()) root.deleteRecursively()
            if (!tmp.renameTo(root)) {
                tmp.copyRecursively(root, overwrite = true)
                tmp.deleteRecursively()
            }
            rootfsReady = true
            return root
        } catch (e: Exception) {
            lastError = e.message ?: e.javaClass.simpleName
            tmp.deleteRecursively()
            return null
        }
    }

    /**
     * 返回 proot 的 argv；rootfs 解压失败返回 null。
     *
     * [workspace] 非空时通过 `-b host:/workspace` 把应用工作区挂进 guest，
     * 并把初始 cwd 设成 /workspace——否则 Linux 终端是一个与代码仓库无关的空 Alpine，
     * 用户在 guest 里看不到自己的项目。终端、bootstrap、stdio MCP 共用这个入口。
     */
    fun argv(context: Context, workspace: File? = null, command: String? = null): List<String>? {
        if (!available(context)) return null
        val root = rootfs(context) ?: return null
        val libDir = File(context.applicationInfo.nativeLibraryDir)
        val proot = File(libDir, "libproot.so")
        val args = mutableListOf(proot.absolutePath, "-r", root.absolutePath, "-0")
        if (workspace != null) {
            workspace.mkdirs()
            args += listOf("-b", "${workspace.absolutePath}:/workspace", "-w", "/workspace")
        } else {
            args += listOf("-w", "/root")
        }
        if (command != null) {
            args += listOf("/bin/sh", "-lc", command)
        } else {
            args += listOf("/bin/sh", "-i")
        }
        return args
    }

    /** 启动一个非 PTY 的 guest 命令（bootstrap / 自检 / stdio MCP）。 */
    fun startProcess(
        context: Context,
        command: String,
        workspace: File? = null,
        mergeStderr: Boolean = true,
    ): Process? {
        val argv = argv(context, workspace, command) ?: return null
        val pb = ProcessBuilder(argv).directory(context.filesDir).redirectErrorStream(mergeStderr)
        for (pair in env(context)) {
            val i = pair.indexOf('=')
            if (i > 0) pb.environment()[pair.substring(0, i)] = pair.substring(i + 1)
        }
        pb.environment()["HOME"] = "/root"
        pb.environment()["PATH"] = "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
        pb.environment()["TERM"] = "dumb"
        return runCatching { pb.start() }.getOrElse { e ->
            lastError = e.message ?: e.javaClass.simpleName
            null
        }
    }

    fun env(context: Context): List<String> {
        val libDir = File(context.applicationInfo.nativeLibraryDir).absolutePath
        val tmp = File(context.cacheDir, "proot-tmp").apply { mkdirs() }
        val loader = File(context.applicationInfo.nativeLibraryDir, "libproot-loader.so").absolutePath
        return listOf(
            "LD_LIBRARY_PATH=$libDir",
            "PROOT_TMP_DIR=${tmp.absolutePath}",
            "PROOT_LOADER=$loader",
            "PROOT_NO_SECCOMP=1",
        )
    }

    private fun extractTar(gz: InputStream, target: File) {
        val canonicalRoot = target.canonicalFile
        var pendingLongName: String? = null
        var pendingPaxPath: String? = null
        val buf = ByteArray(512)
        while (true) {
            var read = 0
            while (read < 512) {
                val n = gz.read(buf, read, 512 - read)
                if (n < 0) return
                read += n
            }
            if (buf.all { it.toInt() == 0 }) return
            val name = readString(buf, 0, 100)
            val prefix = readString(buf, 345, 155)
            var full = if (prefix.isNotEmpty()) "$prefix/$name" else name
            val size = readNumber(buf, 124, 12)
            val type = buf[156].toInt().toChar()
            val link = readString(buf, 157, 100)
            if (type == 'L') {
                pendingLongName = readPayload(gz, size)
                continue
            }
            if (type == 'x') {
                val pax = readPayload(gz, size)
                pendingPaxPath = Regex("""\d+ path=([^\n]+)""").find(pax)?.groupValues?.get(1)
                continue
            }
            val effective = pendingPaxPath ?: pendingLongName ?: full
            pendingPaxPath = null
            pendingLongName = null
            val out = File(canonicalRoot, effective.removePrefix("./")).canonicalFile
            if (out.path != canonicalRoot.path && !out.path.startsWith(canonicalRoot.path + File.separator)) {
                readPayload(gz, size)
                continue
            }
            when (type) {
                '5' -> out.mkdirs()
                '2' -> {
                    out.parentFile?.mkdirs()
                    runCatching {
                        if (out.exists()) out.delete()
                        Files.createSymbolicLink(out.toPath(), java.nio.file.Paths.get(link))
                    }
                    readPayload(gz, size)
                }
                '0', '\u0000', '7' -> {
                    out.parentFile?.mkdirs()
                    val bytes = readPayloadBytes(gz, size)
                    FileOutputStream(out).use { it.write(bytes) }
                    runCatching { out.setExecutable(true, false) }
                }
                '1' -> {
                    out.parentFile?.mkdirs()
                    val src = File(canonicalRoot, link.removePrefix("./"))
                    if (src.isFile) src.copyTo(out, overwrite = true)
                    readPayload(gz, size)
                }
                else -> readPayload(gz, size)
            }
        }
    }

    private fun readPayload(gz: InputStream, size: Long): String {
        if (size <= 0) return ""
        val bytes = readPayloadBytes(gz, size)
        return String(bytes, StandardCharsets.UTF_8)
    }

    private fun readPayloadBytes(gz: InputStream, size: Long): ByteArray {
        val bytes = ByteArray(size.toInt())
        var read = 0
        while (read < bytes.size) {
            val n = gz.read(bytes, read, bytes.size - read)
            if (n < 0) break
            read += n
        }
        // tar 内容按 512 对齐，跳到下一个 header。
        var pad = (512 - (bytes.size % 512)) % 512
        while (pad > 0) {
            val n = gz.read(ByteArray(pad))
            if (n < 0) break
            pad -= n
        }
        return bytes
    }

    private fun readString(buf: ByteArray, offset: Int, length: Int): String {
        val bytes = buf.copyOfRange(offset, offset + length)
        val end = bytes.indexOfFirst { it.toInt() == 0 }.let { if (it < 0) bytes.size else it }
        return String(bytes, 0, end, StandardCharsets.UTF_8).trim()
    }

    private fun readNumber(buf: ByteArray, offset: Int, length: Int): Long {
        val text = readString(buf, offset, length)
        return text.toLongOrNull(8) ?: 0L
    }
}
