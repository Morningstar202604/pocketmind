package com.pocket.agent.runtime

import org.eclipse.jgit.api.Git
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Rule
import org.junit.Test
import org.junit.rules.TemporaryFolder
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config
import org.json.JSONObject
import java.io.File

/** M1.12 原生 Git（JGit）：状态、分支、预合并、冲突、提交与撤销。 */
@RunWith(RobolectricTestRunner::class)
@Config(sdk = [34])
class NativeGitTest {

    @get:Rule
    val tmp = TemporaryFolder()

    private lateinit var root: File
    private lateinit var repoDir: File
    private lateinit var git: NativeGit

    @Before
    fun setUp() {
        root = tmp.root
        repoDir = tmp.newFolder("repo")
        repoDir.mkdirs()
        Git.init().setDirectory(repoDir).setInitialBranch("main").call().use {
            it.repository.config.apply {
                setString("user", null, "name", "Test")
                setString("user", null, "email", "test@example.com")
                save()
            }
            File(repoDir, "a.txt").writeText("base\n")
            it.add().addFilepattern("a.txt").call()
            commit(it, "init")
        }
        git = NativeGit(root)
    }

    @Test
    fun `状态返回分支与干净标记`() {
        val s = git.status(repoDir)
        assertTrue(s.getBoolean("available"))
        assertEquals("main", s.getString("branch"))
        assertTrue(s.getBoolean("clean"))
        assertEquals(0, s.getJSONArray("files").length())
        assertTrue(s.getJSONArray("branches").length() >= 1)
    }

    @Test
    fun `未提交文件出现在状态里`() {
        File(repoDir, "a.txt").writeText("dirty\n")
        File(repoDir, "new.txt").writeText("new\n")
        val s = git.status(repoDir)
        assertFalse(s.getBoolean("clean"))
        val paths = (0 until s.getJSONArray("files").length()).map { s.getJSONArray("files").getJSONObject(it).getString("path") }
        assertTrue(paths.contains("a.txt"))
        assertTrue(paths.contains("new.txt"))
    }

    @Test
    fun `提交全部改动后工作区干净`() {
        File(repoDir, "a.txt").writeText("committed\n")
        File(repoDir, "new.txt").writeText("new\n")
        val r = git.commitAll(repoDir, "ui commit")
        assertTrue(r.getBoolean("ok"))
        val s = r.getJSONObject("state")
        assertTrue(s.getBoolean("clean"))
        assertEquals("ui commit", headSubject())
        assertEquals("committed\n", File(repoDir, "a.txt").readText())
    }

    @Test
    fun `无冲突合并可预合并再提交`() {
        Git.open(repoDir).use { g ->
            g.checkout().setCreateBranch(true).setName("feature").call()
            File(repoDir, "b.txt").writeText("feature\n")
            g.add().addFilepattern("b.txt").call()
            commit(g, "feature")
            g.checkout().setName("main").call()
            File(repoDir, "c.txt").writeText("main\n")
            g.add().addFilepattern("c.txt").call()
            commit(g, "main2")
        }
        val preview = git.mergePreview(repoDir, "feature")
        assertTrue(preview.getJSONObject("state").getBoolean("merging"))
        assertEquals(0, preview.getJSONArray("conflicts").length())
        val done = git.mergeCommit(repoDir, "merge feature")
        val state = done.getJSONObject("state")
        assertFalse(state.getBoolean("merging"))
        assertTrue(state.getBoolean("clean"))
        assertTrue(File(repoDir, "b.txt").exists())
        assertEquals(2, headParents())
    }

    @Test
    fun `冲突可列三路并选我的版本解决`() {
        Git.open(repoDir).use { g ->
            g.checkout().setCreateBranch(true).setName("feature").call()
            File(repoDir, "a.txt").writeText("feature\n")
            g.add().addFilepattern("a.txt").call()
            commit(g, "feature")
            g.checkout().setName("main").call()
            File(repoDir, "a.txt").writeText("main\n")
            g.add().addFilepattern("a.txt").call()
            commit(g, "main2")
        }
        val preview = git.mergePreview(repoDir, "feature")
        assertTrue(preview.getJSONObject("state").getBoolean("merging"))
        val conflicts = git.conflicts(repoDir)
        assertEquals(1, conflicts.getInt("count"))
        val item = conflicts.getJSONArray("items").getJSONObject(0)
        assertEquals("main\n", item.getString("ours"))
        assertEquals("feature\n", item.getString("theirs"))
        val resolved = git.resolveConflict(repoDir, "a.txt", "ours", null)
        assertEquals(0, resolved.getJSONArray("remaining").length())
        assertEquals("main\n", File(repoDir, "a.txt").readText())
        val done = git.mergeCommit(repoDir, "merge feature")
        assertFalse(done.getJSONObject("state").getBoolean("merging"))
    }

    @Test
    fun `中止合并回到合并前`() {
        Git.open(repoDir).use { g ->
            g.checkout().setCreateBranch(true).setName("feature").call()
            File(repoDir, "a.txt").writeText("feature\n")
            g.add().addFilepattern("a.txt").call()
            commit(g, "feature")
            g.checkout().setName("main").call()
            File(repoDir, "a.txt").writeText("main\n")
            g.add().addFilepattern("a.txt").call()
            commit(g, "main2")
        }
        git.mergePreview(repoDir, "feature")
        val aborted = git.mergeAbort(repoDir)
        val state = aborted.getJSONObject("state")
        assertFalse(state.getBoolean("merging"))
        assertTrue(state.getBoolean("clean"))
        assertEquals("main\n", File(repoDir, "a.txt").readText())
    }

    @Test
    fun `提交后可撤销合并且恢复前树`() {
        Git.open(repoDir).use { g ->
            g.checkout().setCreateBranch(true).setName("feature").call()
            File(repoDir, "b.txt").writeText("feature\n")
            g.add().addFilepattern("b.txt").call()
            commit(g, "feature")
            g.checkout().setName("main").call()
        }
        val preview = git.mergePreview(repoDir, "feature")
        val cp = preview.getJSONObject("checkpoint")
        git.mergeCommit(repoDir, "merge feature")
        assertTrue(File(repoDir, "b.txt").exists())
        val undone = git.undoMerge(repoDir)
        assertEquals(cp.getString("sha"), undone.getJSONObject("checkpoint").getString("sha"))
        assertFalse(File(repoDir, "b.txt").exists())
    }

    @Test
    fun `非仓库返回 available false 并可初始化`() {
        val empty = tmp.newFolder("empty")
        val s = git.status(empty)
        assertFalse(s.getBoolean("available"))
        val init = git.init(empty)
        assertTrue(init.getBoolean("available"))
        assertTrue(File(empty, ".git").isDirectory)
    }

    private fun commit(g: Git, msg: String) {
        g.commit().setMessage(msg).setAuthor("Test", "test@example.com")
            .setCommitter("Test", "test@example.com").call()
    }

    private fun headSubject(): String = Git.open(repoDir).use { g ->
        g.repository.resolve("HEAD")?.let { id ->
            org.eclipse.jgit.revwalk.RevWalk(g.repository).use { rw -> rw.parseCommit(id).shortMessage }
        } ?: ""
    }

    private fun headParents(): Int = Git.open(repoDir).use { g ->
        g.repository.resolve("HEAD")?.let { id ->
            org.eclipse.jgit.revwalk.RevWalk(g.repository).use { rw -> rw.parseCommit(id).parentCount }
        } ?: 0
    }
}
