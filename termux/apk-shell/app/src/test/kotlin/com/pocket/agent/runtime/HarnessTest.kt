package com.pocket.agent.runtime

import android.content.Context
import androidx.test.core.app.ApplicationProvider
import com.pocket.agent.agent.AgentLoop
import com.pocket.agent.agent.ApprovalCenter
import com.pocket.agent.data.SettingsStore
import com.pocket.agent.data.Store
import com.pocket.agent.llm.LlmClient
import org.json.JSONArray
import org.json.JSONObject
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config
import java.io.ByteArrayOutputStream

/**
 * M1.3 原生 Agent Harness 回归：参数坏掉不能执行、重复调用被拦、配置范围收敛。
 * 与 Python `smoke/p40_harness_test.py` 对齐；这里只测 Kotlin 循环，不经过 HTTP。
 */
@RunWith(RobolectricTestRunner::class)
@Config(sdk = [34])
class HarnessTest {

    private lateinit var context: Context
    private lateinit var store: Store
    private lateinit var settings: SettingsStore
    private lateinit var approval: ApprovalCenter
    private lateinit var fake: FakeLlm
    private lateinit var loop: AgentLoop

    class FakeTurn(val text: String? = null, val calls: List<Triple<String, String, String>> = emptyList())

    class FakeLlm(cfg: SettingsStore.LlmConfig) : LlmClient(cfg) {
        val turns = ArrayDeque<FakeTurn>()
        var slowMs: Long = 0
        override fun streamChat(
            messages: JSONArray,
            tools: JSONArray?,
            maxDepthTokens: Int,
            callbacks: StreamCallbacks,
            abortSignal: () -> Boolean,
        ): List<Triple<String, String, String>> {
            if (slowMs > 0) Thread.sleep(slowMs)
            val turn = turns.removeFirstOrNull() ?: FakeTurn("(脚本耗尽)")
            turn.text?.let { callbacks.onText(it) }
            callbacks.onDone(LlmClient.Usage(10, 5, false), turn.calls)
            return turn.calls
        }

        override fun complete(messages: JSONArray, maxTokens: Int, temperature: Double): String = "测试会话"
    }

    @Before
    fun setUp() {
        context = ApplicationProvider.getApplicationContext()
        context.filesDir.listFiles()?.forEach { it.deleteRecursively() }
        store = Store(context)
        settings = SettingsStore(context)
        settings.save(JSONObject()
            .put("permission_mode", "auto")
            .put("llm", JSONObject().put("base_url", "http://fake.local")
                .put("api_key", "sk-test").put("model", "fake-model"))
            .put("agent", JSONObject().put("repeat_tool_limit", 2)
                .put("max_tool_chain_depth", 4).put("max_turn_seconds", 900)))
        approval = ApprovalCenter(context)
        AgentLoop.appContext = { context }
        fake = FakeLlm(SettingsStore.LlmConfig("http://fake.local", "sk-test", "fake-model", 0.7, 2048))
        loop = AgentLoop(store, settings, approval) { fake }
    }

    @After
    fun tearDown() {
        context.filesDir.listFiles()?.forEach { it.deleteRecursively() }
    }

    private fun runChat(sessionId: String): String {
        val out = ByteArrayOutputStream()
        val sse = SseWriter(out)
        loop.chat(sessionId, "测试", sse)
        sse.close()
        return out.toString(Charsets.UTF_8.name())
    }

    @Test
    fun `非法 JSON 参数不执行写文件`() {
        val sid = store.createSession().getString("id")
        val target = java.io.File(context.filesDir, "should-not-exist.txt")
        val invalid = "{\"path\":\"${target.absolutePath}\",\"content\":"
        fake.turns.add(FakeTurn(calls = listOf(Triple("bad", "write_file", invalid))))
        fake.turns.add(FakeTurn(text = "参数坏了，没有写。"))
        val sse = runChat(sid)
        assertTrue("应返回 invalid_arguments：$sse", sse.contains("invalid_arguments"))
        assertFalse("参数坏了却写了文件", target.exists())
    }

    @Test
    fun `连续相同工具调用被拦截后仍能收尾`() {
        val sid = store.createSession().getString("id")
        val missing = java.io.File(context.filesDir, "missing.txt").absolutePath
        val same = "{\"path\":\"$missing\"}"
        fake.turns.add(FakeTurn(calls = listOf(
            Triple("r1", "read_file", same),
            Triple("r2", "read_file", same),
            Triple("r3", "read_file", same),
        )))
        fake.turns.add(FakeTurn(text = "已停止重复读取。"))
        val sse = runChat(sid)
        assertTrue("应返回 repeat_tool_call：$sse", sse.contains("repeat_tool_call"))
        assertTrue("重复后应正常 done：$sse", sse.contains("\"stop_reason\":\"end_turn\""))
    }

    @Test
    fun `单轮墙钟超时会停止`() {
        settings.save(JSONObject().put("agent", JSONObject()
            .put("max_turn_seconds", 1).put("max_tool_chain_depth", 4)
            .put("repeat_tool_limit", 3)))
        val sid = store.createSession().getString("id")
        fake.slowMs = 1200
        fake.turns.add(FakeTurn(calls = listOf(
            Triple("c1", "read_file", "{\"path\":\"${context.filesDir.absolutePath}/nope.txt\"}")
        )))
        val sse = runChat(sid)
        assertTrue("应返回 turn_timeout：$sse", sse.contains("turn_timeout"))
    }

    @Test
    fun `agent 配置范围收敛`() {
        settings.save(JSONObject().put("agent", JSONObject()
            .put("max_tool_chain_depth", 999)
            .put("max_tool_retries", -3)
            .put("max_turn_seconds", 9999)
            .put("repeat_tool_limit", 99)))
        val cfg = settings.agentCfg()
        assertEquals(50, cfg.maxToolChainDepth)
        assertEquals(0, cfg.maxToolRetries)
        assertEquals(3600, cfg.maxTurnSeconds)
        assertEquals(10, cfg.repeatToolLimit)
    }
}
