package com.pocket.agent.runtime

import com.pocket.agent.data.SettingsStore
import com.pocket.agent.tools.DeviceEnv
import com.pocket.agent.tools.ToolDef
import com.pocket.agent.tools.ToolRegistry
import org.json.JSONArray
import org.json.JSONObject
import android.content.Context
import java.io.BufferedReader
import java.io.BufferedWriter
import java.io.InputStreamReader
import java.io.OutputStreamWriter
import java.net.HttpURLConnection
import java.net.URL
import java.nio.charset.StandardCharsets
import java.util.concurrent.LinkedBlockingQueue
import java.util.concurrent.TimeUnit
import java.util.concurrent.locks.ReentrantReadWriteLock

/**
 * 原生 MCP 客户端：HTTP 直连，stdio 走应用内 PRoot，不依赖 Termux。
 *
 * 传输一：Streamable HTTP（POST JSON-RPC，响应可以是 application/json 或
 * text/event-stream），手机上只需要网络权限。
 * 传输二：stdio（MCP 规范的行分隔 JSON-RPC），进程由 PRoot 的 Alpine 启动，
 * 因此需要先在「Linux 环境」里装 Node/Python；这不是 Termux 专属能力。
 *
 * 工具注册进原生 ToolRegistry 时默认 danger（外部服务器能干什么本地无从审计），
 * 由现有审批链路兜底。
 */
object McpManager {
    private val clients = LinkedHashMap<String, McpTransport>()
    private val loaded = LinkedHashMap<String, JSONArray>()

    /** 刷新/关闭与工具调用之间加读写锁，避免 refresh 在调用中途 destroy 子进程。 */
    private val lifecycle = ReentrantReadWriteLock()

    /**
     * [context] 非空时，非 http(s) 的 command 会经 PRoot 启动 stdio MCP；
     * 为空时保持旧行为（HTTP only），用于 JVM 单测与不依赖 Android 的调用方。
     */
    @Synchronized
    fun refresh(settings: SettingsStore, context: Context? = null): JSONObject {
        val write = lifecycle.writeLock()
        write.lock()
        try {
            val cfg = settings.mcpClientCfg()
            val out = JSONObject().put("enabled", cfg.optBoolean("enabled"))
                .put("servers", JSONArray()).put("tools", JSONArray())
            if (!cfg.optBoolean("enabled")) {
                closeAll()
                return out
            }
            val timeout = cfg.optInt("timeout", 15).coerceIn(3, 60)
            val servers = cfg.optJSONArray("servers") ?: JSONArray()
            closeAll()
            for (i in 0 until servers.length()) {
                val row = servers.optJSONObject(i) ?: continue
                val name = row.optString("name").ifBlank { "mcp" }.take(32)
                val url = row.optString("url").trim()
                val command = row.optString("command").trim() + run {
                    val args = row.optJSONArray("args")
                    if (args == null) "" else (0 until args.length()).joinToString(" ") { " " + args.optString(it) }
                }
                val transport = try {
                    when {
                        url.startsWith("http://") || url.startsWith("https://") ->
                            HttpMcpClient(name, url, timeout)
                        command.startsWith("http://") || command.startsWith("https://") ->
                            HttpMcpClient(name, command, timeout)
                        context != null && command.isNotBlank() -> {
                            val proc = NativeProot.startProcess(
                                context, command, context.filesDir, mergeStderr = false, maskSensitive = true
                            )
                                ?: throw IllegalStateException(
                                    NativeProot.lastError ?: "PRoot 不可用；stdio MCP 需要先安装 Linux 环境")
                            StdioMcpClient(name, proc, timeout)
                        }
                        else -> throw IllegalStateException(
                            "只支持 http(s) URL，或经 PRoot 启动的 stdio command；stdio 服务器需 Node/Python")
                    }
                } catch (e: Exception) {
                    out.getJSONArray("servers").put(JSONObject().put("name", name)
                        .put("ok", false).put("error", e.message ?: "连接失败"))
                    continue
                }
                try {
                    val tools = transport.initializeAndList()
                    clients[name] = transport
                    loaded[name] = tools
                    installTools(name, tools, transport)
                    out.getJSONArray("servers").put(JSONObject().put("name", name).put("ok", true)
                        .put("transport", if (transport is StdioMcpClient) "stdio" else "http")
                        .put("tools", tools.length()))
                    for (j in 0 until tools.length()) out.getJSONArray("tools").put(tools.getJSONObject(j))
                } catch (e: Exception) {
                    runCatching { transport.close() }
                    out.getJSONArray("servers").put(JSONObject().put("name", name)
                        .put("ok", false).put("error", e.message ?: "连接失败"))
                }
            }
            return out
        } finally {
            write.unlock()
        }
    }

    @Synchronized
    fun closeAll() {
        val write = lifecycle.writeLock()
        write.lock()
        try {
            for (c in clients.values) runCatching { c.close() }
            clients.clear()
            loaded.clear()
            ToolRegistry.removeDynamicGroup("mcp")
        } finally {
            write.unlock()
        }
    }

    @Synchronized
    fun snapshot(): JSONObject {
        val tools = JSONArray()
        for ((server, arr) in loaded) {
            for (i in 0 until arr.length()) {
                tools.put(JSONObject(arr.getJSONObject(i).toString()).put("server", server))
            }
        }
        return JSONObject().put("servers", JSONArray(clients.keys.toList()))
            .put("tools", tools).put("count", tools.length())
    }

    private fun installTools(server: String, tools: JSONArray, client: McpTransport) {
        for (i in 0 until tools.length()) {
            val t = tools.getJSONObject(i)
            val rawName = t.optString("name")
            if (rawName.isBlank()) continue
            val full = "mcp__${server}__$rawName".take(64)
            val schema = t.optJSONObject("inputSchema") ?: JSONObject()
                .put("type", "object").put("properties", JSONObject())
            val desc = (t.optString("description").ifBlank { rawName }).take(400)
            ToolRegistry.registerDynamic(ToolDef(
                name = full,
                description = "[MCP:$server] $desc",
                risk = "danger",
                summary = "MCP $server · $rawName",
                group = "mcp",
                params = schema,
                handler = { args: JSONObject, _: DeviceEnv ->
                    val read = lifecycle.readLock()
                    read.lock()
                    try {
                        client.callTool(rawName, args)
                    } finally {
                        read.unlock()
                    }
                },
            ))
        }
    }
}

private interface McpTransport {
    fun initializeAndList(): JSONArray
    fun callTool(tool: String, args: JSONObject): JSONObject
    fun close()
}

private class HttpMcpClient(
    private val name: String,
    private val url: String,
    private val timeoutSec: Int,
) : McpTransport {
    private var sessionId: String? = null
    private var nextId = 1
    private var protocolVersion = "2024-11-05"

    override fun initializeAndList(): JSONArray {
        val init = rpc("initialize", JSONObject()
            .put("protocolVersion", protocolVersion)
            .put("capabilities", JSONObject())
            .put("clientInfo", JSONObject().put("name", "PocketMind").put("version", "1.9.2")))
        protocolVersion = init.optJSONObject("result")?.optString("protocolVersion")
            ?.takeIf { it.isNotBlank() } ?: protocolVersion
        notify("notifications/initialized", JSONObject())
        val listed = rpc("tools/list", JSONObject())
        return listed.optJSONObject("result")?.optJSONArray("tools") ?: JSONArray()
    }

    override fun callTool(tool: String, args: JSONObject): JSONObject {
        val result = rpc("tools/call", JSONObject().put("name", tool).put("arguments", args))
        val err = result.optJSONObject("error")
        if (err != null) {
            return JSONObject().put("error", err.optString("message").ifBlank { "MCP 调用失败" })
                .put("error_code", "mcp_error")
        }
        val content = result.optJSONObject("result")?.optJSONArray("content")
        val text = StringBuilder()
        if (content != null) {
            for (i in 0 until content.length()) {
                val part = content.optJSONObject(i) ?: continue
                if (part.optString("type") == "text") text.append(part.optString("text"))
            }
        }
        val structured = result.optJSONObject("result")?.optJSONObject("structuredContent")
        return JSONObject().put("ok", true).put("server", name).put("tool", tool)
            .put("text", text.toString())
            .put("structured", structured ?: JSONObject.NULL)
    }

    override fun close() {
        // 无持久连接，无需释放。
    }

    private fun notify(method: String, params: JSONObject): JSONObject {
        return post(JSONObject().put("jsonrpc", "2.0").put("method", method).put("params", params))
    }

    @Synchronized
    private fun rpc(method: String, params: JSONObject): JSONObject {
        val id = nextId++
        return post(JSONObject().put("jsonrpc", "2.0").put("id", id)
            .put("method", method).put("params", params))
    }

    private fun post(payload: JSONObject): JSONObject {
        val conn = (URL(url).openConnection() as HttpURLConnection).apply {
            requestMethod = "POST"
            connectTimeout = timeoutSec * 1000
            readTimeout = timeoutSec * 1000
            doOutput = true
            setRequestProperty("Content-Type", "application/json")
            setRequestProperty("Accept", "application/json, text/event-stream")
            setRequestProperty("MCP-Protocol-Version", protocolVersion)
            sessionId?.let { setRequestProperty("Mcp-Session-Id", it) }
        }
        try {
            conn.outputStream.use { it.write(payload.toString().toByteArray(StandardCharsets.UTF_8)) }
            val code = conn.responseCode
            sessionId = conn.getHeaderField("Mcp-Session-Id")?.takeIf { it.isNotBlank() } ?: sessionId
            if (code !in 200..299) {
                val body = (conn.errorStream ?: conn.inputStream)?.bufferedReader()?.use { it.readText() }.orEmpty()
                throw IllegalStateException("MCP HTTP $code：${body.take(300)}")
            }
            val contentType = conn.contentType.orEmpty()
            if (contentType.contains("text/event-stream")) {
                return parseSse(conn.inputStream.bufferedReader())
            }
            val text = conn.inputStream.bufferedReader().use { it.readText() }
            if (text.isBlank()) return JSONObject()
            return JSONObject(text)
        } finally {
            conn.disconnect()
        }
    }

    /** SSE 里可能有 notification 与 response 交错；按 JSON-RPC id 取第一个带 result/error 的帧。 */
    private fun parseSse(reader: BufferedReader): JSONObject {
        var last = JSONObject()
        val data = StringBuilder()
        fun flush() {
            if (data.isEmpty()) return
            val frame = runCatching { JSONObject(data.toString()) }.getOrNull()
            if (frame != null && (frame.has("result") || frame.has("error"))) last = frame
            data.setLength(0)
        }
        reader.use { r ->
            while (true) {
                val line = r.readLine() ?: break
                if (line.startsWith("data:")) {
                    if (data.isNotEmpty()) data.append('\n')
                    data.append(line.removePrefix("data:").trimStart())
                } else if (line.isEmpty()) {
                    flush()
                }
            }
        }
        flush()
        return last
    }
}

/**
 * stdio MCP：进程由 PRoot 启动，JSON-RPC 按行读写。
 *
 * 为什么单独一个类：HTTP MCP 的 session/SSE 语义在 stdio 上不存在；
 * 混在一个客户端里会让超时、关闭和错误处理互相污染。这里只负责
 * initialize → tools/list → tools/call，进程退出即失败。
 */
internal class StdioMcpClient(
    private val name: String,
    private val process: Process,
    private val timeoutSec: Int,
) : McpTransport {
    private val lines = LinkedBlockingQueue<String>()
    private val writer = BufferedWriter(OutputStreamWriter(process.outputStream, StandardCharsets.UTF_8))
    private var nextId = 1
    private var protocolVersion = "2024-11-05"
    private val reader = Thread {
        runCatching {
            BufferedReader(InputStreamReader(process.inputStream, StandardCharsets.UTF_8)).use { r ->
                r.forEachLine { lines.put(it) }
            }
        }
    }.apply { isDaemon = true; start() }
    private val errReader = Thread {
        runCatching {
            process.errorStream.bufferedReader(StandardCharsets.UTF_8).use { r ->
                r.forEachLine { /* drain，避免 MCP server 写满 stderr 阻塞 */ }
            }
        }
    }.apply { isDaemon = true; start() }

    override fun initializeAndList(): JSONArray {
        val init = rpc("initialize", JSONObject()
            .put("protocolVersion", protocolVersion)
            .put("capabilities", JSONObject())
            .put("clientInfo", JSONObject().put("name", "PocketMind").put("version", "1.9.2")))
        protocolVersion = init.optJSONObject("result")?.optString("protocolVersion")
            ?.takeIf { it.isNotBlank() } ?: protocolVersion
        notify("notifications/initialized", JSONObject())
        val listed = rpc("tools/list", JSONObject())
        return listed.optJSONObject("result")?.optJSONArray("tools") ?: JSONArray()
    }

    override fun callTool(tool: String, args: JSONObject): JSONObject {
        val result = rpc("tools/call", JSONObject().put("name", tool).put("arguments", args))
        val err = result.optJSONObject("error")
        if (err != null) {
            return JSONObject().put("error", err.optString("message").ifBlank { "MCP 调用失败" })
                .put("error_code", "mcp_error")
        }
        val content = result.optJSONObject("result")?.optJSONArray("content")
        val text = StringBuilder()
        if (content != null) {
            for (i in 0 until content.length()) {
                val part = content.optJSONObject(i) ?: continue
                if (part.optString("type") == "text") text.append(part.optString("text"))
            }
        }
        val structured = result.optJSONObject("result")?.optJSONObject("structuredContent")
        return JSONObject().put("ok", true).put("server", name).put("tool", tool)
            .put("text", text.toString()).put("structured", structured ?: JSONObject.NULL)
    }

    private fun notify(method: String, params: JSONObject) {
        write(JSONObject().put("jsonrpc", "2.0").put("method", method).put("params", params))
    }

    @Synchronized
    private fun rpc(method: String, params: JSONObject): JSONObject {
        val id = nextId++
        write(JSONObject().put("jsonrpc", "2.0").put("id", id)
            .put("method", method).put("params", params))
        val deadline = System.currentTimeMillis() + timeoutSec * 1000L
        while (System.currentTimeMillis() < deadline) {
            val line = lines.poll(250, TimeUnit.MILLISECONDS) ?: continue
            val frame = runCatching { JSONObject(line) }.getOrNull() ?: continue
            if (frame.opt("id") != id) continue
            return frame
        }
        throw IllegalStateException("stdio MCP 响应超时（${timeoutSec}s）；进程是否已退出？")
    }

    @Synchronized
    private fun write(obj: JSONObject) {
        if (!process.isAlive) throw IllegalStateException("stdio MCP 进程已退出")
        writer.write(obj.toString())
        writer.newLine()
        writer.flush()
    }

    override fun close() {
        runCatching { writer.close() }
        reader.interrupt()
        errReader.interrupt()
        runCatching { process.destroy() }
        if (process.isAlive) runCatching { process.destroyForcibly() }
    }
}
