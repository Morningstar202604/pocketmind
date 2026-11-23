package com.pocket.agent.agent

import com.pocket.agent.data.Store
import com.pocket.agent.runtime.SseWriter
import com.pocket.agent.tools.ToolDef
import org.json.JSONArray
import org.json.JSONObject
import java.io.ByteArrayOutputStream

/**
 * 只读子代理：复用主 Agent 的模型与循环，但只暴露 list_dir/read_file。
 *
 * 为什么不是「再开一个完整 Agent」：手机会议里两个完整循环同时跑，
 * 审批、文件写、任务取消都会互相踩；而且子代理的价值是省上下文/并行调研，
 * 不是绕过审批。这里用一次性子会视，headless 模式下写工具一律拒绝，
 * 结束后删除子会视，不在会话列表里留垃圾。
 */
object SubAgentRunner {
    val READ_ONLY_TOOLS: Set<String> = setOf("list_dir", "read_file")

    fun toolDef(agent: AgentLoop, store: Store): ToolDef = ToolDef(
        name = "subagent",
        description = "启动一个只读子代理，独立阅读工作区文件并返回结论；不会写文件或执行命令。",
        risk = "safe",
        summary = "只读子代理",
        group = "agent",
        params = JSONObject().put("type", "object")
            .put("properties", JSONObject().put("task",
                JSONObject().put("type", "string").put("description", "要调研的问题")))
            .put("required", JSONArray().put("task")),
        handler = { args, _ -> run(agent, store, args.optString("task", "")) },
    )

    fun run(agent: AgentLoop, store: Store, task: String, maxChars: Int = 12_000): JSONObject {
        val clean = task.trim()
        if (clean.isEmpty()) {
            return JSONObject().put("error", "task 不能为空").put("error_code", "invalid_arguments")
        }
        val sub = store.createSession("子代理 · ${clean.take(24)}")
        val subId = sub.getString("id")
        val sink = ByteArrayOutputStream()
        val sse = SseWriter(sink)
        var text = StringBuilder()
        var toolCalls = 0
        var error: String? = null
        try {
            agent.chat(
                subId,
                "$clean\n\n（你是只读子代理：只能列目录/读文件，不能写文件或执行命令。请给出结论与依据。）",
                sse,
                headless = true,
                allowedTools = READ_ONLY_TOOLS,
            )
        } catch (e: Exception) {
            error = e.message ?: e.javaClass.simpleName
        } finally {
            runCatching { store.deleteSession(subId) }
        }
        for (line in sink.toString("UTF-8").lineSequence()) {
            if (!line.startsWith("data: ")) continue
            val ev = runCatching { JSONObject(line.removePrefix("data: ")) }.getOrNull() ?: continue
            when (ev.optString("type")) {
                "text" -> text.append(ev.optString("text"))
                "tool_start" -> toolCalls += 1
                "error" -> error = ev.optString("message", error ?: "子代理失败")
            }
        }
        val answer = text.toString().trim().take(maxChars)
        return JSONObject()
            .put("ok", error == null && answer.isNotEmpty())
            .put("agent", "readonly-subagent")
            .put("tool_calls", toolCalls)
            .put("text", answer)
            .put("truncated", text.length > maxChars)
            .put("error", error ?: "")
    }
}
