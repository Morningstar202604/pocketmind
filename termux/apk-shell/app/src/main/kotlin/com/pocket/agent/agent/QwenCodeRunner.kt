package com.pocket.agent.agent

import android.content.Context
import com.pocket.agent.data.SettingsStore
import com.pocket.agent.runtime.NativeProot
import com.pocket.agent.tools.ToolDef
import org.json.JSONArray
import org.json.JSONObject
import java.io.ByteArrayOutputStream
import java.io.File
import java.util.concurrent.TimeUnit

/**
 * 内置 Qwen Code CLI 的桥。
 *
 * APK 的 Alpine rootfs 里已经有 Node 22 + `qwen`（@qwen-code/qwen-code 0.28.4，
 * Apache-2.0），不是运行时 npx 下载。它使用主设置里的 OpenAI-compatible
 * base_url / api_key / model，因此国产模型（百炼、DeepSeek、火山方舟等）都能直接跑。
 *
 * 外层仍走 PocketMind 的 danger 审批：用户在卡片上批准后，CLI 才在 guest
 * `/workspace` 里运行；mode=plan 只分析，mode=write 才让 Qwen Code 改文件/跑命令。
 */
object QwenCodeRunner {
    private const val MAX_OUTPUT = 30_000
    private const val MAX_STDERR = 8_000

    fun toolDef(settings: SettingsStore): ToolDef {
        val props = JSONObject()
            .put("prompt", JSONObject().put("type", "string").put("description", "交给 Qwen Code 的任务"))
            .put("mode", JSONObject().put("type", "string")
                .put("description", "plan（只分析，默认）或 write（允许改文件/跑命令）；write 需外层审批"))
            .put("timeout", JSONObject().put("type", "integer").put("description", "超时秒数，默认 600"))
        val required = JSONArray().put("prompt")
        return ToolDef(
            "qwen_code",
            "在本机内置的 Qwen Code CLI（Node 22）里执行一个 coding 任务。plan 模式只读分析；write 模式可改 /workspace，外层必须先批准。",
            "danger",
            "运行 Qwen Code",
            "agent",
            JSONObject().put("type", "object").put("properties", props).put("required", required)
        ) { args, env ->
            run(env.context, settings, args)
        }
    }

    private fun run(context: Context, settings: SettingsStore, args: JSONObject): JSONObject {
        if (!NativeProot.available(context)) {
            return JSONObject().put("error", "PRoot 不可用，Qwen Code 无法启动")
                .put("error_code", "capability_unavailable")
        }
        val prompt = args.optString("prompt").trim()
        if (prompt.isEmpty()) return JSONObject().put("error", "prompt 不能为空").put("error_code", "tool_error")
        val mode = if (args.optString("mode").equals("write", true)) "write" else "plan"
        val timeout = args.optInt("timeout", 600).coerceIn(30, 1800)
        val llm = settings.llmConfig()
        if (llm.baseUrl.isBlank() || llm.apiKey.isBlank() || llm.model.isBlank()) {
            return JSONObject().put("error", "请先在设置里填写 Base URL、API Key 和模型名")
                .put("error_code", "llm_not_configured")
        }
        val rootfs = NativeProot.rootfs(context)
            ?: return JSONObject().put("error", "rootfs 解压失败：${NativeProot.lastError ?: ""}")
                .put("error_code", "rootfs_unavailable")
        if (!File(rootfs, "usr/bin/qwen").isFile) {
            return JSONObject().put("error", "当前 rootfs 是 minirootfs，未包含 Qwen Code；请安装完整工具链")
                .put("error_code", "capability_unavailable")
        }
        val approval = if (mode == "write") "--approval-mode=yolo" else "--approval-mode=plan"
        val command = "qwen $approval --output-format text ${shellQuote(prompt)}"
        val proc = NativeProot.startProcess(
            context,
            command,
            context.filesDir,
            mergeStderr = false,
            extraEnv = mapOf(
                "OPENAI_API_KEY" to llm.apiKey,
                "OPENAI_BASE_URL" to llm.baseUrl,
                "OPENAI_MODEL" to llm.model,
                "QWEN_CODE_DISABLE_UPDATE_CHECK" to "1",
                "CI" to "1",
                "NO_COLOR" to "1",
                "TERM" to "dumb",
            ),
        ) ?: return JSONObject().put("error", NativeProot.lastError ?: "Qwen Code 启动失败")
            .put("error_code", "proot_start_failed")

        runCatching { proc.outputStream.close() }
        val out = ByteArrayOutputStream()
        val err = ByteArrayOutputStream()
        val outThread = Thread { runCatching { proc.inputStream.copyTo(out) } }.apply { isDaemon = true; start() }
        val errThread = Thread { runCatching { proc.errorStream.copyTo(err) } }.apply { isDaemon = true; start() }
        val finished = runCatching { proc.waitFor(timeout.toLong(), TimeUnit.SECONDS) }.getOrDefault(false)
        if (!finished) {
            proc.destroyForcibly()
            outThread.join(2_000)
            errThread.join(2_000)
            return JSONObject().put("error", "Qwen Code 超时（${timeout}s）").put("error_code", "timeout")
                .put("output", cap(out.toString("UTF-8"), MAX_OUTPUT))
                .put("stderr", cap(err.toString("UTF-8"), MAX_STDERR))
        }
        outThread.join(5_000)
        errThread.join(5_000)
        val code = proc.exitValue()
        val text = out.toString("UTF-8").trim()
        val stderr = err.toString("UTF-8").trim()
        return JSONObject()
            .put("ok", code == 0)
            .put("exit_code", code)
            .put("mode", mode)
            .put("output", cap(text, MAX_OUTPUT))
            .put("truncated", text.length > MAX_OUTPUT)
            .put("stderr", cap(stderr, MAX_STDERR))
    }

    private fun shellQuote(s: String): String = "'" + s.replace("'", "'\\''") + "'"

    private fun cap(s: String, max: Int) = if (s.length <= max) s else s.take(max) + "\n…（已截断）"
}
