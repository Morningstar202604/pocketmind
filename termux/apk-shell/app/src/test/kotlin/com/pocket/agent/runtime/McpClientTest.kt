package com.pocket.agent.runtime

import com.pocket.agent.data.SettingsStore
import com.pocket.agent.data.Store
import com.pocket.agent.tools.DeviceEnv
import com.pocket.agent.tools.ToolRegistry
import org.json.JSONArray
import org.json.JSONObject
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.RuntimeEnvironment
import org.robolectric.annotation.Config
import java.io.BufferedInputStream
import java.net.ServerSocket
import java.net.Socket
import java.nio.charset.StandardCharsets

@RunWith(RobolectricTestRunner::class)
@Config(sdk = [35])
class McpClientTest {
    private lateinit var server: MiniMcpServer
    private lateinit var settings: SettingsStore

    @Before
    fun setUp() {
        server = MiniMcpServer()
        settings = SettingsStore(RuntimeEnvironment.getApplication())
        settings.save(JSONObject().put("mcp_client", JSONObject()
            .put("enabled", true)
            .put("timeout", 5)
            .put("servers", JSONArray().put(JSONObject()
                .put("name", "demo")
                .put("command", "http://127.0.0.1:${server.port}/mcp")))))
    }

    @After
    fun tearDown() {
        server.close()
    }

    @Test
    fun `HTTP MCP 刷新后工具进入注册表并可调用`() {
        val info = McpManager.refresh(settings)
        assertTrue(info.optBoolean("enabled"))
        assertEquals(1, info.getJSONArray("tools").length())
        val tool = ToolRegistry.byName("mcp__demo__echo")
        assertNotNull(tool)
        val result = tool!!.handler(JSONObject().put("text", "hello"),
            DeviceEnv(RuntimeEnvironment.getApplication(), Store(RuntimeEnvironment.getApplication()), "mcp-test"))
        assertEquals("echo:hello", result.optString("text"))
    }
}

/** JDK 的 com.sun.net.httpserver 不在 Android 单测编译类路径里，这里用 ServerSocket 起一个最小 MCP HTTP 服务。 */
private class MiniMcpServer {
    private val socket = ServerSocket(0, 16, java.net.InetAddress.getByName("127.0.0.1"))
    private val running = java.util.concurrent.atomic.AtomicBoolean(true)
    val port: Int get() = socket.localPort

    init {
        Thread {
            while (running.get()) {
                val client = runCatching { socket.accept() }.getOrNull() ?: break
                runCatching { handle(client) }
                runCatching { client.close() }
            }
        }.apply { isDaemon = true; start() }
    }

    private fun handle(client: Socket) {
        val input = BufferedInputStream(client.getInputStream())
        val requestLine = readLine(input) ?: return
        val headers = HashMap<String, String>()
        while (true) {
            val line = readLine(input) ?: return
            if (line.isEmpty()) break
            val i = line.indexOf(':')
            if (i > 0) headers[line.substring(0, i).lowercase()] = line.substring(i + 1).trim()
        }
        val length = headers["content-length"]?.toIntOrNull() ?: 0
        val body = ByteArray(length)
        var read = 0
        while (read < length) {
            val n = input.read(body, read, length - read)
            if (n < 0) break
            read += n
        }
        val req = JSONObject(String(body, StandardCharsets.UTF_8))
        val method = req.optString("method")
        val id = if (req.has("id")) req.optInt("id") else null
        val response = JSONObject().put("jsonrpc", "2.0")
        if (id != null) response.put("id", id)
        when (method) {
            "initialize" -> response.put("result", JSONObject()
                .put("protocolVersion", "2024-11-05")
                .put("capabilities", JSONObject())
                .put("serverInfo", JSONObject().put("name", "test")))
            "tools/list" -> response.put("result", JSONObject().put("tools", JSONArray()
                .put(JSONObject().put("name", "echo").put("description", "echo input")
                    .put("inputSchema", JSONObject().put("type", "object")
                        .put("properties", JSONObject().put("text", JSONObject().put("type", "string")))))))
            "tools/call" -> {
                val args = req.optJSONObject("params")?.optJSONObject("arguments") ?: JSONObject()
                response.put("result", JSONObject().put("content", JSONArray()
                    .put(JSONObject().put("type", "text").put("text", "echo:" + args.optString("text")))))
            }
            else -> response.put("result", JSONObject())
        }
        val bytes = if (id == null) ByteArray(0) else response.toString().toByteArray(StandardCharsets.UTF_8)
        val status = if (id == null) "202 Accepted" else "200 OK"
        val out = client.getOutputStream()
        out.write(("HTTP/1.1 $status\r\nContent-Type: application/json\r\n" +
            "Content-Length: ${bytes.size}\r\nConnection: close\r\n\r\n").toByteArray(StandardCharsets.US_ASCII))
        out.write(bytes)
        out.flush()
    }

    private fun readLine(input: BufferedInputStream): String? {
        val bytes = ArrayList<Byte>()
        while (bytes.size < 8192) {
            val b = input.read()
            if (b < 0) return null
            if (b == 10) return bytes.toByteArray().toString(StandardCharsets.US_ASCII).trimEnd('\r')
            bytes.add(b.toByte())
        }
        return null
    }

    fun close() {
        running.set(false)
        runCatching { socket.close() }
    }
}
