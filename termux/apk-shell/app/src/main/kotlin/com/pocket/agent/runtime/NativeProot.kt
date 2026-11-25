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
    /**
     * rootfs 内容标记。改这个字符串 = 强制重新解压，用来把旧 APK 下载的
     * rootfs 替换成新包；只有工具链内容真的变了才改，不要跟着 App 版本号走，
     * 否则每次升级都要重解 585MB。
     */
    private const val ROOTFS_STAMP = "alpine 3.21.3\npocketmind toolchain r2"
    @Volatile private var rootfsReady = false
    @Volatile private var bytesCacheRoot: String? = null
    @Volatile private var bytesCache = 0L
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
        return root.takeIf { isCurrentRootfs(it) }
    }

    private fun isCurrentRootfs(root: File): Boolean {
        val stamp = File(root, ".pocketmind-rootfs")
        return stamp.isFile && runCatching { stamp.readText().trim() }.getOrDefault("") == ROOTFS_STAMP
    }

    /** 状态接口每几秒轮询一次；585MB/数万文件的 walk 只做一次，避免 UI 卡顿。 */
    fun rootfsBytes(context: Context): Long {
        val root = rootfsIfReady(context) ?: return 0L
        if (bytesCacheRoot == root.absolutePath) return bytesCache
        val bytes = root.walkTopDown().filter { it.isFile }.sumOf { it.length() }
        bytesCacheRoot = root.absolutePath
        bytesCache = bytes
        return bytes
    }

    @Synchronized
    fun rootfs(context: Context): File? {
        val base = File(context.filesDir, "proot")
        val root = File(base, "rootfs")
        if (rootfsReady && isCurrentRootfs(root)) return root
        base.mkdirs()
        if (isCurrentRootfs(root)) {
            rootfsReady = true
            return root
        }
        // 旧版 rootfs（qwen 符号链接 / 旧工具链）不能继续复用，先释放空间再解压。
        if (root.exists()) root.deleteRecursively()
        bytesCacheRoot = null
        bytesCache = 0L
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
                    validateRootfs(tmp)
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
            File(tmp, ".pocketmind-rootfs").writeText(ROOTFS_STAMP + "\n")
            if (prebuilt) {
                val marker = File(tmp, ".pocketmind-dev-env")
                if (!marker.isFile || marker.readText().isBlank()) marker.writeText("prebuilt-full")
            }
            if (root.exists()) root.deleteRecursively()
            if (!tmp.renameTo(root)) {
                tmp.copyRecursively(root, overwrite = true)
                tmp.deleteRecursively()
            }
            rootfsReady = true
            bytesCacheRoot = null
            bytesCache = 0L
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
    fun argv(
        context: Context,
        workspace: File? = null,
        command: String? = null,
        maskSensitive: Boolean = false,
    ): List<String>? {
        if (!available(context)) return null
        val root = rootfs(context) ?: return null
        val libDir = File(context.applicationInfo.nativeLibraryDir)
        val proot = File(libDir, "libproot.so")
        val args = mutableListOf(proot.absolutePath, "-r", root.absolutePath, "-0")
        if (workspace != null) {
            workspace.mkdirs()
            args += listOf("-b", "${workspace.absolutePath}:/workspace", "-w", "/workspace")
            if (maskSensitive) addSensitiveMasks(context, workspace, args)
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

    /**
     * 启动一个非 PTY 的 guest 命令（bootstrap / 自检 / stdio MCP）。
     *
     * maskSensitive=true 时把 app 私有目录里的配置/数据库/rootfs 从 /workspace
     * 遮掉。Qwen Code 是模型驱动的写权限进程，不能让它读到 config.json 里的 API Key。
     */
    fun startProcess(
        context: Context,
        command: String,
        workspace: File? = null,
        mergeStderr: Boolean = true,
        extraEnv: Map<String, String> = emptyMap(),
        maskSensitive: Boolean = false,
    ): Process? {
        val argv = argv(context, workspace, command, maskSensitive) ?: return null
        val pb = ProcessBuilder(argv).directory(context.filesDir).redirectErrorStream(mergeStderr)
        for (pair in env(context)) {
            val i = pair.indexOf('=')
            if (i > 0) pb.environment()[pair.substring(0, i)] = pair.substring(i + 1)
        }
        pb.environment()["HOME"] = "/root"
        pb.environment()["PATH"] = "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
        pb.environment()["TERM"] = "dumb"
        for ((k, v) in extraEnv) pb.environment()[k] = v
        return runCatching { pb.start() }.getOrElse { e ->
            lastError = e.message ?: e.javaClass.simpleName
            null
        }
    }

    /** 用空文件/空目录覆盖工作区里的敏感项；只在 Qwen 等模型驱动进程使用。 */
    private fun addSensitiveMasks(context: Context, workspace: File, args: MutableList<String>) {
        val denyFile = File(context.cacheDir, "proot-deny-file").apply { writeText("") }
        val denyDir = File(context.cacheDir, "proot-deny-dir").apply { mkdirs() }
        val names = listOf(
            "config.json", "pocketmind.db", "rules.json", "workspace_changes.json",
            "checkpoints", "audit", "workspace_tasks", ".workspace-backups", "proot",
        )
        for (name in names) {
            val target = File(workspace, name)
            if (!target.exists()) continue
            val src = if (target.isDirectory) denyDir else denyFile
            args += listOf("-b", "${src.absolutePath}:/workspace/$name")
        }
    }

    fun env(context: Context): List<String> {
        val libDir = File(context.applicationInfo.nativeLibraryDir).absolutePath
        val tmp = File(context.cacheDir, "proot-tmp").apply { mkdirs() }
        val loader = File(context.applicationInfo.nativeLibraryDir, "libproot-loader.so").absolutePath
        return listOf(
            "HOME=/root",
            "PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
            "LD_LIBRARY_PATH=$libDir",
            "PROOT_TMP_DIR=${tmp.absolutePath}",
            "PROOT_LOADER=$loader",
            "PROOT_NO_SECCOMP=1",
        )
    }

    /**
     * 解压后的最低可用性检查。tar 里的符号链接在 Android 上可能因 SELinux/文件系统
     * 差异创建失败；若不检查，用户会得到“解压成功但 /bin/sh 不存在”的黑屏/127。
     * 失败就回退 minirootfs，并保留明确原因。
     */
    private fun validateRootfs(root: File) {
        val files = listOf(
            "bin/busybox",
            "usr/bin/node",
            "usr/bin/python3.12",
            "usr/bin/git",
            "usr/bin/qwen",
            "usr/local/bin/mcp-server-filesystem",
        )
        for (rel in files) {
            if (!File(root, rel).isFile) throw IllegalStateException("rootfs 缺少 $rel")
        }
        for (rel in listOf("bin/sh", "usr/bin/python3")) {
            if (!Files.isSymbolicLink(File(root, rel).toPath())) {
                throw IllegalStateException("rootfs 符号链接创建失败：$rel")
            }
        }
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
