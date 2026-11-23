package com.pocket.agent.runtime

import com.pocket.agent.data.SettingsStore
import com.pocket.agent.tools.DeviceEnv
import com.pocket.agent.tools.ToolDef
import com.pocket.agent.tools.ToolRegistry
import org.json.JSONArray
import org.json.JSONObject
import java.io.BufferedReader
import java.io.InputStreamReader
import java.net.HttpURLConnection
import java.net.URL
import java.nio.charset.StandardCharsets

/**
 * 原生 MCP 客户端：不用 Python/Node，也不吃 Termux 的 stdio。
 *
 * 传输选 Streamable HTTP（POST JSON-RPC，响应可以是 application/json 或
 * text/event-stream），这样手机上只需要网络权限。stdio MCP 服务器仍然
 * 需要外部进程，属于 Termux 能力，不在这里假装支持。
 *
 * 工具注册进原生 ToolRegistry 时默认 danger（外部服务器能干什么本地无从审计），
 * 由现有审批链路兜底。
 */
object McpManager {
    private val clients = LinkedHashMap<String, HttpMcpClient>()
    private val loaded = LinkedHashMap<String, JSONArray>()

    @Synchronized
    fun refresh(settings: SettingsStore): JSONObject {
        val cfg = settings.mcpClientCfg()
        val out = JSONObject().put("enabled", cfg.optBoolean("enabled"))
            .put("servers", JSONArray()).put("tools", JSONArray())
        if (!cfg.optBoolean("enabled")) return out
        val timeout = cfg.optInt("timeout", 15).coerceIn(3, 60)
        val servers = cfg.optJSONArray("servers") ?: JSONArray()
        for (i in 0 until servers.length()) {
            val row = servers.optJSONObject(i) ?: continue
            val name = row.optString("name").ifBlank { "mcp" }
            val url = (row.optString("url").ifBlank { row.optString("command") }).trim()
            if (!url.startsWith("http://") && !url.startsWith("https://")) {
                out.getJSONArray("servers").put(JSONObject().put("name", name)
                    .put("ok", false).put("error", "只有 http(s) URL 能在原生引擎里连接；stdio 请用 Termux"))
                continue
            }
            try {
                val client = HttpMcpClient(name, url, timeout)
                val tools = client.initializeAndList()
                clients[name] = client
                loaded[name] = tools
                installTools(name, tools, client)
                out.getJSONArray("servers").put(JSONObject().put("name", name).put("ok", true)
                    .put("tools", tools.length()))
                for (j in 0 until tools.length()) out.getJSONArray("tools").put(tools.getJSONObject(j))
            } catch (e: Exception) {
                out.getJSONArray("servers").put(JSONObject().put("name", name)
                    .put("ok", false).put("error", e.message ?: "连接失败"))
            }
        }
        return out
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

    private fun installTools(server: String, tools: JSONArray, client: HttpMcpClient) {
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
                    client.callTool(rawName, args)
                },
            ))
        }
    }
}

private class HttpMcpClient(
    private val name: String,
    private val url: String,
    private val timeoutSec: Int,
) {
    private var sessionId: String? = null
    private var nextId = 1
    private var protocolVersion = "2024-11-05"

    fun initializeAndList(): JSONArray {
        val init = rpc("initialize", JSONObject()
            .put("protocolVersion", protocolVersion)
            .put("capabilities", JSONObject())
            .put("clientInfo", JSONObject().put("name", "PocketMind").put("version", "1.6.2")))
        protocolVersion = init.optJSONObject("result")?.optString("protocolVersion")
            ?.takeIf { it.isNotBlank() } ?: protocolVersion
        notify("notifications/initialized", JSONObject())
        val listed = rpc("tools/list", JSONObject())
        return listed.optJSONObject("result")?.optJSONArray("tools") ?: JSONArray()
    }

    fun callTool(tool: String, args: JSONObject): JSONObject {
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

    private fun notify(method: String, params: JSONObject): JSONObject {
        return post(JSONObject().put("jsonrpc", "2.0").put("method", method).put("params", params))
    }

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
