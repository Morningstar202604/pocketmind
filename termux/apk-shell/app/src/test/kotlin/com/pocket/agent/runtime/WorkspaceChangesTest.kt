package com.pocket.agent.runtime

import android.content.Context
import androidx.test.core.app.ApplicationProvider
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config
import java.io.File

/** M1.8 原生工作区变更记录：备份、mtime 冲突拒绝、目录撤销。 */
@RunWith(RobolectricTestRunner::class)
@Config(sdk = [34])
class WorkspaceChangesTest {

    private lateinit var context: Context
    private lateinit var ws: NativeWorkspace

    @Before
    fun setUp() {
        context = ApplicationProvider.getApplicationContext()
        File(context.filesDir, "ws-changes").deleteRecursively()
        File(context.filesDir, "workspace_changes.json").delete()
        File(context.filesDir, ".workspace-backups").deleteRecursively()
        ws = NativeWorkspace(context)
    }

    @After
    fun tearDown() {
        File(context.filesDir, "ws-changes").deleteRecursively()
        File(context.filesDir, "workspace_changes.json").delete()
        File(context.filesDir, ".workspace-backups").deleteRecursively()
    }

    @Test
    fun `新建文件后撤销会删除并计入已撤销`() {
        val w = ws.write("ws-changes/new.txt", "one\n", null)
        val id = w.getString("change_id")
        assertTrue(File(context.filesDir, "ws-changes/new.txt").isFile)

        val list = ws.listChanges(50, false)
        assertEquals(1, list.getJSONArray("items").length())

        val undo = ws.undoChange(id)
        assertEquals(true, undo.getBoolean("ok"))
        assertFalse(File(context.filesDir, "ws-changes/new.txt").exists())
        assertEquals(0, ws.listChanges(50, false).getJSONArray("items").length())
        assertEquals(1, ws.listChanges(50, true).getJSONArray("items").length())
    }

    @Test
    fun `覆盖后撤销恢复原内容与修改时间`() {
        ws.write("ws-changes/a.txt", "old\n", null)
        val oldMtime = File(context.filesDir, "ws-changes/a.txt").lastModified()
        val w = ws.write("ws-changes/a.txt", "new\n", null)

        assertEquals(true, ws.undoChange(w.getString("change_id")).getBoolean("ok"))
        val f = File(context.filesDir, "ws-changes/a.txt")
        assertEquals("old\n", f.readText())
        assertEquals(oldMtime, f.lastModified())
    }

    @Test
    fun `删除文件后撤销恢复备份`() {
        ws.write("ws-changes/gone.txt", "keep me\n", null)
        val d = ws.delete("ws-changes/gone.txt")
        assertFalse(File(context.filesDir, "ws-changes/gone.txt").exists())

        assertEquals(true, ws.undoChange(d.getString("change_id")).getBoolean("ok"))
        assertEquals("keep me\n", File(context.filesDir, "ws-changes/gone.txt").readText())
    }

    @Test
    fun `保存后又被手改则拒绝撤销`() {
        ws.write("ws-changes/b.txt", "one\n", null)
        val w = ws.write("ws-changes/b.txt", "two\n", null)
        val f = File(context.filesDir, "ws-changes/b.txt")
        f.writeText("user edit\n")
        f.setLastModified(f.lastModified() + 5_000)

        val undo = ws.undoChange(w.getString("change_id"))
        assertTrue(undo.has("error"))
        assertEquals("user edit\n", f.readText())
    }

    @Test
    fun `新建目录后撤销删除目录`() {
        ws.mkdir("ws-changes/dir")
        val list = ws.listChanges(50, false)
        val id = list.getJSONArray("items").getJSONObject(0).getString("id")
        assertEquals(true, ws.undoChange(id).getBoolean("ok"))
        assertFalse(File(context.filesDir, "ws-changes/dir").exists())
    }

    @Test
    fun `变更列表按时间倒序并遵守 limit`() {
        ws.write("ws-changes/1.txt", "1\n", null)
        ws.write("ws-changes/2.txt", "2\n", null)
        ws.write("ws-changes/3.txt", "3\n", null)
        val items = ws.listChanges(2, false).getJSONArray("items")
        assertEquals(2, items.length())
        assertEquals("3.txt", File(items.getJSONObject(0).getString("path")).name)
        assertEquals("2.txt", File(items.getJSONObject(1).getString("path")).name)
    }
}
