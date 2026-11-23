package com.pocket.agent.runtime

import android.content.Context
import androidx.test.core.app.ApplicationProvider
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Assert.fail
import org.junit.Before
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config
import java.io.File

/** M1.7 原生工作区：同一 filesDir + 隐藏配置 + 原子写 + 搜索/预览/命令。 */
@RunWith(RobolectricTestRunner::class)
@Config(sdk = [34])
class NativeWorkspaceTest {

    private lateinit var context: Context
    private lateinit var ws: NativeWorkspace

    @Before
    fun setUp() {
        context = ApplicationProvider.getApplicationContext()
        ws = NativeWorkspace(context)
        File(context.filesDir, "ws-test").deleteRecursively()
        File(context.filesDir, "config.json").delete()
    }

    @After
    fun tearDown() {
        File(context.filesDir, "ws-test").deleteRecursively()
        File(context.filesDir, "config.json").delete()
    }

    @Test
    fun `info 暴露工作区且声明能力边界`() {
        val info = ws.info()
        assertEquals(true, info.getBoolean("ok"))
        assertEquals(context.filesDir.canonicalPath, File(info.getString("root")).canonicalPath)
        assertEquals("kotlin", info.getString("runtime"))
        assertEquals(true, info.getJSONObject("features").getBoolean("git"))
        assertEquals(false, info.getJSONObject("features").getBoolean("git_blocks"))
        assertEquals(true, info.getJSONObject("features").getBoolean("exec"))
    }

    @Test
    fun `写入读取树与按 mtime 拒绝旧保存`() {
        ws.write("ws-test/hello.txt", "one\n", null)
        val read = ws.read("ws-test/hello.txt")
        assertEquals("one\n", read.getString("content"))
        val mtime = read.getLong("mtime")
        val tree = ws.tree(File(context.filesDir, "ws-test").absolutePath)
        assertTrue((0 until tree.getJSONArray("entries").length()).any {
            tree.getJSONArray("entries").getJSONObject(it).getString("name") == "hello.txt"
        })
        val external = File(context.filesDir, "ws-test/hello.txt")
        external.writeText("other\n")
        external.setLastModified((mtime + 5) * 1000)
        try {
            ws.write("ws-test/hello.txt", "two\n", mtime)
            fail("旧 mtime 应被拒绝")
        } catch (e: NativeWorkspace.WorkspaceException) {
            assertEquals(409, e.status)
        }
    }

    @Test
    fun `config_json 不在树里也不能读`() {
        File(context.filesDir, "config.json").writeText("{\"api_key\":\"secret\"}")
        val tree = ws.tree(".")
        assertFalse((0 until tree.getJSONArray("entries").length()).any {
            tree.getJSONArray("entries").getJSONObject(it).getString("name") == "config.json"
        })
        try {
            ws.read("config.json")
            fail("config.json 不应可读")
        } catch (e: NativeWorkspace.WorkspaceException) {
            assertEquals(403, e.status)
        }
    }

    @Test
    fun `路径穿越被拒绝`() {
        try {
            ws.read("../../etc/passwd")
            fail("路径穿越应被拒绝")
        } catch (e: NativeWorkspace.WorkspaceException) {
            assertEquals(403, e.status)
        }
    }

    @Test
    fun `搜索预览 mkdir rename delete`() {
        ws.mkdir("ws-test/sub")
        ws.write("ws-test/sub/a.txt", "alpha\nneedle here\n", null)
        val hits = ws.search("needle", "ws-test", false)
        assertEquals(1, hits.getJSONArray("results").length())
        val preview = ws.preview("ws-test/sub/a.txt", "alpha\nchanged\n")
        assertEquals(true, preview.getBoolean("changed"))
        assertTrue(preview.getString("diff").contains("changed"))
        ws.rename("ws-test/sub/a.txt", "ws-test/sub/b.txt")
        assertEquals(true, File(context.filesDir, "ws-test/sub/b.txt").isFile)
        ws.delete("ws-test/sub/b.txt")
        ws.delete("ws-test/sub")
        assertFalse(File(context.filesDir, "ws-test/sub").exists())
    }

    @Test
    fun `exec 在指定 cwd 执行`() {
        ws.mkdir("ws-test")
        val r = ws.exec("printf ok", "ws-test", 10)
        assertEquals(true, r.getBoolean("ok"))
        assertTrue(r.getString("stdout").contains("ok"))
    }
}
