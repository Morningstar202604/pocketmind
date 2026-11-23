package com.pocket.agent.runtime

import android.content.Context
import androidx.test.core.app.ApplicationProvider
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config
import java.io.File

/** M1.9 原生工作区后台任务：启动、查询、取消。 */
@RunWith(RobolectricTestRunner::class)
@Config(sdk = [34])
class WorkspaceTasksTest {

    private lateinit var context: Context
    private lateinit var ws: NativeWorkspace

    @Before
    fun setUp() {
        context = ApplicationProvider.getApplicationContext()
        File(context.filesDir, "ws-tasks").deleteRecursively()
        File(context.filesDir, "workspace_tasks").deleteRecursively()
        ws = NativeWorkspace(context)
        ws.mkdir("ws-tasks")
    }

    @After
    fun tearDown() {
        File(context.filesDir, "ws-tasks").deleteRecursively()
        File(context.filesDir, "workspace_tasks").deleteRecursively()
    }

    @Test
    fun `后台任务跑完后可查询输出`() {
        val t = ws.startTask("printf ok", "ws-tasks", 10)
        val id = t.getString("id")
        val done = waitFor(id)
        assertEquals("done", done.getString("status"))
        assertTrue(done.getString("stdout").contains("ok"))
        assertTrue(done.getString("log_path").isNotEmpty())
        assertTrue((0 until ws.listTasks(10).getJSONArray("items").length()).any {
            ws.listTasks(10).getJSONArray("items").getJSONObject(it).getString("id") == id
        })
    }

    @Test
    fun `任务未结束就能看到增量输出`() {
        val t = ws.startTask("printf 'early\\n'; sleep 1; printf 'late\\n'", "ws-tasks", 10)
        val id = t.getString("id")
        Thread.sleep(250)
        var mid = ws.getTask(id)
        val sawEarly = mid.getString("stdout").contains("early")
        assertTrue("运行中应看到 early，实际：$mid", sawEarly)
        assertEquals("running", mid.getString("status"))
        assertEquals("done", waitFor(id).getString("status"))
    }

    @Test
    fun `SSE 推送增量并在任务结束后发 done`() {
        val t = ws.startTask("printf 'SSE_A\\n'; sleep 0.4; printf 'SSE_B\\n'", "ws-tasks", 10)
        val id = t.getString("id")
        val out = java.io.ByteArrayOutputStream()
        ws.streamTask(id, SseWriter(out))
        val text = out.toString(Charsets.UTF_8.name())
        assertTrue("应包含 snapshot：$text", text.contains("\"type\":\"snapshot\""))
        assertTrue("应包含 done：$text", text.contains("\"type\":\"done\""))
        assertTrue("应包含尾部输出：$text", text.contains("SSE_B"))
    }

    @Test
    fun `取消运行中的任务`() {
        val t = ws.startTask("sleep 30", "ws-tasks", 5)
        val id = t.getString("id")
        Thread.sleep(200)
        assertEquals("cancelling", ws.cancelTask(id).getString("status"))
        val done = waitFor(id)
        assertEquals("cancelled", done.getString("status"))
        assertEquals("任务已取消", done.getString("error"))
    }

    private fun waitFor(id: String): org.json.JSONObject {
        repeat(80) {
            val r = ws.getTask(id)
            if (r.optString("status") !in setOf("running", "cancelling")) return r
            Thread.sleep(100)
        }
        return ws.getTask(id)
    }
}
