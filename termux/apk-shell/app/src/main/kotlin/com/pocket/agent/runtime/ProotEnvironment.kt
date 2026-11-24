package com.pocket.agent.runtime

import android.content.Context
import org.json.JSONArray
import org.json.JSONObject
import java.io.File
import java.util.concurrent.Executors
import java.util.concurrent.TimeUnit

/**
 * PRoot 环境状态机：rootfs 是否就绪、开发依赖是否安装、最近一次自检结果。
 *
 * 这里只做「应用内可控的环境准备」，不是 root，也不修改宿主 Android：
 *  - bootstrap 在 guest 里执行 `apk add`，把 Node/Python/Git/编译链装进 rootfs；
 *  - selfTest 真正启动 proot 并执行 `/bin/sh -lc`，能验证 ptrace/loader/symlink；
 *  - status 只读缓存，不触发大 rootfs 解压，供前端轮询。
 */
object ProotEnvironment {
    private const val MAX_LOG = 24_000
    private val runner = Executors.newSingleThreadExecutor { r ->
        Thread(r, "pocketmind-proot-bootstrap").apply { isDaemon = true }
    }

    private val lock = Any()
    private val log = StringBuilder()
    @Volatile private var bootstrap = "idle"
    @Volatile private var profile = ""
    @Volatile private var lastError: String? = null
    @Volatile private var lastTest: JSONObject? = null
    @Volatile private var updatedAt = 0L

    fun status(context: Context): JSONObject {
        val rootfs = NativeProot.rootfsIfReady(context)
        // 进程重启后内存状态会丢；rootfs 里的完成标记让 UI 仍能显示「已安装」。
        val doneMarker = rootfs?.let { File(it, ".pocketmind-dev-env") }?.takeIf { it.isFile }
        val persistedProfile = doneMarker?.let { runCatching { it.readText() }.getOrDefault("") }?.trim().orEmpty()
        val state = if (bootstrap == "idle" && doneMarker != null) "done" else bootstrap
        val shownProfile = if (profile.isBlank()) persistedProfile else profile
        return JSONObject()
            .put("available", NativeProot.available(context))
            .put("rootfs_ready", rootfs != null)
            .put("rootfs_path", rootfs?.absolutePath ?: "")
            .put("rootfs_bytes", NativeProot.rootfsBytes(context))
            .put("bootstrap", state)
            .put("profile", shownProfile)
            .put("log", synchronized(lock) { log.toString() })
            .put("last_test", lastTest ?: JSONObject.NULL)
            .put("last_error", lastError ?: "")
            .put("updated_at", updatedAt)
    }

    /** profile=minimal|full；full 多装 build-base（gcc/make/headers）。 */
    fun startBootstrap(context: Context, profile: String): JSONObject {
        val p = if (profile == "full") "full" else "minimal"
        synchronized(lock) {
            if (bootstrap == "running") {
                return JSONObject().put("ok", false).put("error", "开发环境安装正在进行")
                    .put("bootstrap", bootstrap).put("log", log.toString())
            }
            bootstrap = "running"
            this.profile = p
            lastError = null
            log.setLength(0)
            log.append("正在准备 Alpine rootfs…\n")
            updatedAt = System.currentTimeMillis()
        }
        runner.submit {
            val rootfs = NativeProot.rootfs(context)
            if (rootfs == null) {
                finish("failed", NativeProot.lastError ?: "rootfs 解压失败")
                return@submit
            }
            File(rootfs, ".pocketmind-dev-env").takeIf { it.isFile }?.let { marker ->
                this.profile = marker.readText().trim().ifBlank { "prebuilt-full" }
                append("检测到预装开发工具链：$profile\n")
                append("Node/Python/Git/gcc 已就绪，跳过联网安装。\n")
                finish("done", null)
                return@submit
            }
            val packages = if (p == "full") {
                "nodejs npm python3 py3-pip git curl bash build-base"
            } else {
                "nodejs npm python3 py3-pip git curl bash"
            }
            append("rootfs 就绪：${rootfs.absolutePath}\n")
            append("开始安装：$packages\n")
            val command = "apk update && apk add --no-cache $packages"
            val proc = NativeProot.startProcess(context, command)
            if (proc == null) {
                finish("failed", NativeProot.lastError ?: "proot 启动失败")
                return@submit
            }
            val reader = Thread {
                runCatching {
                    proc.inputStream.bufferedReader().use { r ->
                        r.lineSequence().forEach { append(it + "\n") }
                    }
                }
            }.apply { isDaemon = true; start() }
            val finished = runCatching { proc.waitFor(900, TimeUnit.SECONDS) }.getOrDefault(false)
            if (!finished) {
                proc.destroyForcibly()
                reader.join(2_000)
                finish("failed", "安装超时（900 秒）；请检查网络或改用 minimal")
            } else {
                reader.join(5_000)
                val code = runCatching { proc.exitValue() }.getOrDefault(-1)
                if (code == 0) {
                    append("安装完成，退出码 $code\n")
                    runCatching { File(rootfs, ".pocketmind-dev-env").writeText(p) }
                    finish("done", null)
                } else {
                    finish("failed", "apk 退出码 $code")
                }
            }
        }
        return JSONObject().put("ok", true).put("bootstrap", "running").put("profile", p)
    }

    /** 真正在 guest 内执行一组检查；失败也返回结构化结果，前端逐项显示。 */
    fun selfTest(context: Context): JSONObject {
        if (bootstrap == "running") {
            return JSONObject().put("ok", false).put("error", "开发环境安装正在进行，完成后再自检")
                .put("checks", JSONArray())
        }
        if (!NativeProot.available(context)) {
            return JSONObject().put("ok", false).put("error", "PRoot 不可用（需要 arm64 设备与 libproot.so）")
                .put("checks", JSONArray())
        }
        val rootfs = NativeProot.rootfs(context)
            ?: return JSONObject().put("ok", false).put("error", "rootfs 解压失败：${NativeProot.lastError ?: ""}")
                .put("checks", JSONArray())
        val script = """
            echo "alpine=$(cat /etc/alpine-release 2>/dev/null)"
            echo "uid=$(id -u 2>/dev/null)"
            for c in apk node npm python3 pip3 git gcc make; do
              if command -v ${'$'}c >/dev/null 2>&1; then
                echo "have_${'$'}c=$(${'$'}c --version 2>&1 | head -n 1)"
              else
                echo "missing_${'$'}c"
              fi
            done
        """.trimIndent()
        val proc = NativeProot.startProcess(context, script)
            ?: return JSONObject().put("ok", false).put("error", "proot 启动失败：${NativeProot.lastError ?: ""}")
                .put("checks", JSONArray())
        val output = StringBuilder()
        val reader = Thread {
            runCatching { proc.inputStream.bufferedReader().use { r -> r.forEachLine { output.append(it).append('\n') } } }
        }.apply { isDaemon = true; start() }
        val done = runCatching { proc.waitFor(60, TimeUnit.SECONDS) }.getOrDefault(false)
        if (!done) proc.destroyForcibly()
        reader.join(3_000)
        val code = if (done) runCatching { proc.exitValue() }.getOrDefault(-1) else -1
        val lines = output.toString().lines().filter { it.isNotBlank() }
        val checks = JSONArray()
        for (line in lines) {
            val i = line.indexOf('=')
            if (i <= 0) continue
            val key = line.substring(0, i)
            val value = line.substring(i + 1).trim()
            val name = key.removePrefix("have_").removePrefix("missing_")
            val ok = !key.startsWith("missing_") && value.isNotBlank()
            checks.put(JSONObject()
                .put("name", name)
                .put("ok", ok)
                .put("detail", value.ifBlank { if (key.startsWith("missing_")) "未安装" else "输出为空" }))
        }
        val result = JSONObject().put("ok", done && code == 0)
            .put("exit_code", code)
            .put("rootfs", rootfs.absolutePath)
            .put("checks", checks)
            .put("output", output.toString().take(8_000))
        if (!done) result.put("error", "自检超时（60 秒）")
        lastTest = result
        updatedAt = System.currentTimeMillis()
        return result
    }

    private fun append(line: String) {
        synchronized(lock) {
            if (log.length > MAX_LOG) log.delete(0, log.length - MAX_LOG / 2)
            log.append(line)
            updatedAt = System.currentTimeMillis()
        }
    }

    private fun finish(state: String, error: String?) {
        synchronized(lock) {
            bootstrap = state
            lastError = error
            updatedAt = System.currentTimeMillis()
            if (error != null) {
                if (log.length > MAX_LOG) log.delete(0, log.length - MAX_LOG / 2)
                log.append("失败：$error\n")
            }
        }
    }
}
