package com.pocket.agent.runtime

import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Assume.assumeTrue
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config
import java.io.File

/** stdio MCP 的 JSON-RPC 生命周期；在 JVM 上用系统 python3 起一个最小 server。 */
@RunWith(RobolectricTestRunner::class)
@Config(sdk = [34])
class McpStdioTest {
    @Test
    fun `stdio initialize list call`() {
        val py = File("/usr/bin/python3")
        assumeTrue("需要 python3 才能跑 stdio 集成测试", py.canExecute())
        val script = File.createTempFile("mini-mcp-", ".py").apply {
            writeText("""
                import sys, json
                for line in sys.stdin:
                    try:
                        req = json.loads(line)
                    except Exception:
                        continue
                    if req.get("method") == "initialize":
                        out = {"jsonrpc":"2.0","id":req.get("id"),"result":{"protocolVersion":"2024-11-05","capabilities":{},"serverInfo":{"name":"mini"}}}
                    elif req.get("method") == "tools/list":
                        out = {"jsonrpc":"2.0","id":req.get("id"),"result":{"tools":[{"name":"echo","description":"echo","inputSchema":{"type":"object","properties":{"text":{"type":"string"}}}}]}}
                    elif req.get("method") == "tools/call":
                        out = {"jsonrpc":"2.0","id":req.get("id"),"result":{"content":[{"type":"text","text":"echo:" + str(req.get("params",{}).get("arguments",{}).get("text",""))}]}}
                    elif req.get("id") is None:
                        out = None
                    else:
                        out = {"jsonrpc":"2.0","id":req.get("id"),"error":{"message":"unknown"}}
                    if out:
                        print(json.dumps(out), flush=True)
            """.trimIndent())
        }
        val proc = ProcessBuilder(py.absolutePath, script.absolutePath).redirectErrorStream(true).start()
        val client = StdioMcpClient("mini", proc, 5)
        try {
            val tools = client.initializeAndList()
            assertEquals(1, tools.length())
            val result = client.callTool("echo", JSONObject().put("text", "hello"))
            assertTrue(result.getBoolean("ok"))
            assertEquals("echo:hello", result.getString("text"))
        } finally {
            client.close()
            script.delete()
        }
    }
}
