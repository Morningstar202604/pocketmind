package com.pocket.agent.runtime

import android.content.Context
import org.json.JSONArray
import org.json.JSONObject
import java.io.File
import java.nio.charset.StandardCharsets
import java.nio.file.AtomicMoveNotSupportedException
import java.nio.file.Files
import java.nio.file.StandardCopyOption
import java.util.UUID
import java.util.concurrent.TimeUnit

/**
 * 原生 APK 的开发工作区：文件树、读写、搜索、diff 预览、mkdir/rename/delete、命令执行。
 *
 * 根目录是应用私有 `filesDir`，与 Agent 的 `read_file` / `write_file` / `run_shell`
 * 使用同一片空间；config.json / pocketmind.db / checkpoints / audit 等敏感项
 * 从树和读写接口里统一隐藏，避免 Web UI 的 XSS 或误操作把凭据读走。
 *
 * 这不是 PRoot：`exec` 只是 app uid 下的 `/system/bin/sh`，没有 Python/Node/apt。
 */
class NativeWorkspace(private val context: Context) {

    class WorkspaceException(val status: Int, message: String) : RuntimeException(message)

    private val root: File = context.filesDir.canonicalFile
    private val changes = WorkspaceChanges(context, root)
    private val tasks = WorkspaceTasks.get(context)
    private val git = NativeGit(root)
    private val deniedFiles = setOf("config.json", "rules.json", "rules.json.bak", "pocketmind.db", "workspace_changes.json")
    private val deniedDirs = setOf("checkpoints", "audit", ".workspace-backups", "workspace_tasks", ".git", "proot")

    init {
        root.mkdirs()
    }

    fun info(): JSONObject = JSONObject()
        .put("ok", true)
        .put("root", root.absolutePath)
        .put("roots", JSONArray().put(JSONObject().put("path", root.absolutePath).put("label", "应用工作区")))
        .put("runtime", "kotlin")
        .put("features", JSONObject()
            .put("tree", true).put("read", true).put("write", true)
            .put("preview", true).put("search", true).put("exec", true)
            .put("git", true).put("changes", true).put("tasks", true)
            .put("locks", false).put("git_merge", true).put("git_blocks", true)
            .put("pty", NativePty.available).put("proot", NativeProot.available(context)))

    fun tree(path: String?): JSONObject {
        val dir = resolve(path, mustExist = true)
        if (!dir.isDirectory) throw WorkspaceException(400, "不是目录：${dir.name}")
        val entries = JSONArray()
        dir.listFiles()?.filter { visible(it) }?.sortedWith(compareBy<File>({ !it.isDirectory }, { it.name.lowercase() }))
            ?.take(500)?.forEach { f ->
                entries.put(JSONObject()
                    .put("name", f.name)
                    .put("type", when { f.isDirectory -> "dir"; f.isFile -> "file"; else -> "unknown" })
                    .put("size", if (f.isFile) f.length() else 0))
            }
        return JSONObject().put("path", dir.absolutePath).put("entries", entries)
            .put("count", entries.length())
    }

    fun read(path: String?): JSONObject {
        val f = resolve(path, mustExist = true)
        if (!f.isFile) throw WorkspaceException(404, "文件不存在：${f.name}")
        if (f.length() > MAX_READ_BYTES) throw WorkspaceException(413, "文件超过 200KB，请用 run_shell 分段处理")
        val bytes = f.readBytes()
        if (looksBinary(bytes)) throw WorkspaceException(415, "二进制文件不能在工作区编辑器打开")
        return JSONObject().put("path", f.absolutePath)
            .put("content", String(bytes, StandardCharsets.UTF_8))
            .put("size", bytes.size)
            .put("mtime", f.lastModified() / 1000)
    }

    fun write(path: String?, content: String, expectedMtime: Long?): JSONObject {
        val f = resolve(path, mustExist = false)
        if (f.isDirectory) throw WorkspaceException(400, "目标是目录")
        val oldMtime = if (f.isFile) f.lastModified() / 1000 else null
        if (expectedMtime != null && expectedMtime > 0 && oldMtime != null && oldMtime != expectedMtime) {
            throw WorkspaceException(409, "文件已被其他人修改（mtime $oldMtime != $expectedMtime）")
        }
        val existed = f.isFile
        val backup = changes.backupForWrite(f)
        f.parentFile?.mkdirs()
        val tmp = File(f.parentFile, ".${f.name}.tmp-${UUID.randomUUID().toString().take(8)}")
        try {
            tmp.writeText(content, StandardCharsets.UTF_8)
            try {
                Files.move(tmp.toPath(), f.toPath(), StandardCopyOption.REPLACE_EXISTING, StandardCopyOption.ATOMIC_MOVE)
            } catch (_: AtomicMoveNotSupportedException) {
                Files.move(tmp.toPath(), f.toPath(), StandardCopyOption.REPLACE_EXISTING)
            }
        } finally {
            tmp.delete()
        }
        val bytes = content.toByteArray(StandardCharsets.UTF_8).size
        val change = changes.record(
            path = f.absolutePath,
            kind = if (existed) "overwrite" else "create",
            backup = backup,
            afterMtimeMs = f.lastModified(),
            afterSize = f.length().toLong(),
            bytes = bytes,
        )
        return JSONObject().put("ok", true).put("path", f.absolutePath)
            .put("bytes", bytes)
            .put("mtime", f.lastModified() / 1000)
            .put("change_id", change.optString("id"))
    }

    fun preview(path: String?, content: String): JSONObject {
        val f = resolve(path, mustExist = false)
        if (f.isDirectory) throw WorkspaceException(400, "目标是目录")
        val old = if (f.isFile && f.length() <= MAX_READ_BYTES) {
            String(f.readBytes(), StandardCharsets.UTF_8)
        } else {
            ""
        }
        val changed = old != content
        return JSONObject().put("path", f.absolutePath).put("changed", changed)
            .put("diff", if (changed) simpleDiff(old, content) else "")
    }

    fun search(q: String?, path: String?, caseSensitive: Boolean): JSONObject {
        val needle = q?.trim().orEmpty()
        if (needle.isEmpty()) throw WorkspaceException(400, "q 不能为空")
        if (needle.length > 200) throw WorkspaceException(400, "查询过长")
        val base = resolve(path, mustExist = true)
        if (!base.isDirectory) throw WorkspaceException(400, "搜索根不是目录")
        val hits = JSONArray()
        var scanned = 0
        var truncated = false
        for (f in base.walkTopDown().onEnter { dir -> visible(dir) }) {
            if (!f.isFile || !visible(f) || f.length() > MAX_SEARCH_BYTES) continue
            scanned += 1
            if (scanned > MAX_SEARCH_FILES) { truncated = true; break }
            val lines = runCatching { f.readLines(StandardCharsets.UTF_8) }.getOrNull() ?: continue
            for ((idx, line) in lines.withIndex()) {
                val hit = if (caseSensitive) line.contains(needle) else line.contains(needle, ignoreCase = true)
                if (hit) {
                    hits.put(JSONObject().put("path", f.absolutePath).put("line", idx + 1)
                        .put("text", line.trim().take(300)))
                    if (hits.length() >= MAX_HITS) { truncated = true; break }
                }
            }
            if (hits.length() >= MAX_HITS) break
        }
        return JSONObject().put("query", needle).put("root", base.absolutePath)
            .put("results", hits).put("truncated", truncated)
    }

    fun mkdir(path: String?): JSONObject {
        val f = resolve(path, mustExist = false)
        if (f.exists()) throw WorkspaceException(409, "路径已存在")
        if (!f.mkdirs()) throw WorkspaceException(500, "创建目录失败")
        val change = changes.record(
            path = f.absolutePath, kind = "create", afterMtimeMs = f.lastModified(), dir = true,
        )
        return JSONObject().put("ok", true).put("path", f.absolutePath)
            .put("change_id", change.optString("id"))
    }

    fun rename(from: String?, to: String?): JSONObject {
        val src = resolve(from, mustExist = true)
        val dst = resolve(to, mustExist = false)
        if (src == root) throw WorkspaceException(400, "不能重命名工作区根目录")
        if (dst.exists()) throw WorkspaceException(409, "目标已存在")
        dst.parentFile?.mkdirs()
        if (!src.renameTo(dst)) throw WorkspaceException(500, "重命名失败")
        return JSONObject().put("ok", true).put("from", src.absolutePath).put("to", dst.absolutePath)
    }

    fun delete(path: String?): JSONObject {
        val f = resolve(path, mustExist = true)
        if (f == root) throw WorkspaceException(400, "不能删除工作区根目录")
        val isDir = f.isDirectory
        if (isDir && f.listFiles()?.isNotEmpty() == true) {
            throw WorkspaceException(409, "目录非空，先清空再删除")
        }
        val backup = if (isDir) null else changes.backupForWrite(f)
        val oldSize = if (isDir) 0 else f.length()
        if (!f.delete()) throw WorkspaceException(500, if (isDir) "删除目录失败" else "删除文件失败")
        val change = changes.record(
            path = f.absolutePath, kind = "delete", backup = backup, bytes = oldSize.toInt(), dir = isDir,
        )
        return JSONObject().put("ok", true).put("deleted", f.absolutePath)
            .put("type", if (isDir) "dir" else "file").put("change_id", change.optString("id"))
    }

    fun exec(command: String?, cwd: String?, timeoutSec: Int): JSONObject {
        val cmd = command?.trim().orEmpty()
        if (cmd.isEmpty()) throw WorkspaceException(400, "command 不能为空")
        val dir = resolve(cwd ?: ".", mustExist = true)
        if (!dir.isDirectory) throw WorkspaceException(400, "cwd 不是目录")
        val sh = listOf("/system/bin/sh", "/bin/sh").firstOrNull { File(it).exists() } ?: "sh"
        val proc = try {
            ProcessBuilder(sh, "-c", cmd).directory(dir).redirectErrorStream(false).apply {
                environment()["HOME"] = root.absolutePath
                environment()["TMPDIR"] = context.cacheDir.absolutePath
                environment()["PATH"] = "/system/bin:/system/xbin:/vendor/bin:/usr/bin:/bin"
            }.start()
        } catch (e: Exception) {
            throw WorkspaceException(500, "启动 shell 失败：${e.message}")
        }
        val out = StringBuilder()
        val err = StringBuilder()
        val t1 = Thread {
            runCatching {
                proc.inputStream.bufferedReader().use { r ->
                    r.lineSequence().forEach { if (out.length < MAX_EXEC_OUTPUT) out.append(it).append('\n') }
                }
            }
        }
        val t2 = Thread {
            runCatching {
                proc.errorStream.bufferedReader().use { r ->
                    r.lineSequence().forEach { if (err.length < MAX_EXEC_OUTPUT) err.append(it).append('\n') }
                }
            }
        }
        t1.start(); t2.start()
        val finished = proc.waitFor(timeoutSec.coerceIn(1, 300).toLong(), TimeUnit.SECONDS)
        if (!finished) {
            proc.destroy()
            if (!proc.waitFor(2, TimeUnit.SECONDS)) proc.destroyForcibly()
        }
        t1.join(2_000); t2.join(2_000)
        val exit = if (finished) proc.exitValue() else -1
        return JSONObject().put("cwd", dir.absolutePath).put("command", cmd)
            .put("ok", finished && exit == 0).put("exit_code", exit)
            .put("stdout", out.toString().take(MAX_EXEC_OUTPUT))
            .put("stderr", err.toString().take(MAX_EXEC_OUTPUT))
            .put("timed_out", !finished)
            .put("truncated", out.length >= MAX_EXEC_OUTPUT || err.length >= MAX_EXEC_OUTPUT)
    }

    fun listChanges(limit: Int, includeUndone: Boolean): JSONObject =
        changes.list(limit, includeUndone)

    fun undoChange(id: String): JSONObject = changes.undo(id)

    fun undoAllChanges(): JSONObject = changes.undoAll()

    fun startTask(command: String?, cwd: String?, timeoutSec: Int): JSONObject {
        val dir = resolve(cwd ?: ".", mustExist = true)
        if (!dir.isDirectory) throw WorkspaceException(400, "cwd 不是目录")
        return tasks.start(command.orEmpty(), dir, timeoutSec)
            .put("path", dir.absolutePath)
    }

    fun listTasks(limit: Int): JSONObject = tasks.list(limit)

    fun getTask(id: String): JSONObject = tasks.get(id)

    fun cancelTask(id: String): JSONObject = tasks.cancel(id)

    fun streamTask(id: String, writer: SseWriter) = tasks.stream(id, writer)

    fun term(ws: NativeWebSocket, cwd: String?, requested: String?, mode: String?) {
        val dir = resolve(cwd ?: ".", mustExist = true)
        if (!dir.isDirectory) throw WorkspaceException(400, "终端 cwd 不是目录")
        if (mode == "proot") {
            val argv = NativeProot.argv(context, dir)
                ?: throw WorkspaceException(503, "PRoot 不可用：rootfs 解压失败或 ABI 不支持")
            NativeTerminalHub.serve(ws, dir, root, context.cacheDir, requested, "proot", argv, NativeProot.env(context))
        } else {
            NativeTerminalHub.serve(ws, dir, root, context.cacheDir, requested, "shell")
        }
    }

    fun gitClone(url: String?, target: String?, branch: String?): JSONObject {
        if (url.isNullOrBlank()) throw WorkspaceException(400, "url 不能为空")
        if (target.isNullOrBlank()) throw WorkspaceException(400, "target 不能为空")
        return git.clone(url, target, branch)
    }

    fun gitPull(cwd: String?): JSONObject = git.pull(resolve(cwd ?: ".", mustExist = true))

    fun gitStatus(cwd: String?): JSONObject = git.status(resolve(cwd ?: ".", mustExist = true))

    fun gitInit(cwd: String?): JSONObject = git.init(resolve(cwd ?: ".", mustExist = true))

    fun gitCheckout(cwd: String?, branch: String, create: Boolean): JSONObject =
        git.checkout(resolve(cwd ?: ".", mustExist = true), branch, create)

    fun gitCommit(cwd: String?, message: String): JSONObject =
        git.commitAll(resolve(cwd ?: ".", mustExist = true), message)

    fun gitMergePreview(cwd: String?, branch: String): JSONObject =
        git.mergePreview(resolve(cwd ?: ".", mustExist = true), branch)

    fun gitMergeAbort(cwd: String?): JSONObject =
        git.mergeAbort(resolve(cwd ?: ".", mustExist = true))

    fun gitMergeCommit(cwd: String?, message: String): JSONObject =
        git.mergeCommit(resolve(cwd ?: ".", mustExist = true), message)

    fun gitMergeCheckpoint(cwd: String?): JSONObject =
        git.checkpoint(resolve(cwd ?: ".", mustExist = true))

    fun gitMergeUndo(cwd: String?): JSONObject =
        git.undoMerge(resolve(cwd ?: ".", mustExist = true))

    fun gitConflicts(cwd: String?): JSONObject =
        git.conflicts(resolve(cwd ?: ".", mustExist = true))

    fun gitResolveConflict(cwd: String?, path: String, strategy: String, content: String?): JSONObject =
        git.resolveConflict(resolve(cwd ?: ".", mustExist = true), path, strategy, content)

    fun gitConflictBlocks(cwd: String?, path: String): JSONObject =
        git.conflictBlocks(resolve(cwd ?: ".", mustExist = true), path)

    fun gitResolveConflictBlocks(cwd: String?, path: String, choices: JSONArray): JSONObject =
        git.resolveConflictBlocks(resolve(cwd ?: ".", mustExist = true), path, choices)

    private fun resolve(path: String?, mustExist: Boolean): File {
        val raw = path?.trim().orEmpty()
        val f = if (raw.isEmpty() || raw == ".") root else if (raw.startsWith("/")) File(raw) else File(root, raw)
        val canonical = try {
            f.canonicalFile
        } catch (e: Exception) {
            throw WorkspaceException(400, "路径不合法：${e.message}")
        }
        if (canonical != root && !canonical.path.startsWith(root.path + File.separator)) {
            throw WorkspaceException(403, "路径越界")
        }
        if (!visible(canonical)) throw WorkspaceException(403, "该路径属于应用私有数据，不开放给工作区")
        if (mustExist && !canonical.exists()) throw WorkspaceException(404, "路径不存在")
        return canonical
    }

    private fun visible(f: File): Boolean {
        val rel = f.canonicalPath.removePrefix(root.canonicalPath).trimStart(File.separatorChar)
        if (rel.isEmpty()) return true
        val parts = rel.split(File.separatorChar)
        val first = parts.firstOrNull() ?: return true
        if (first in deniedDirs) return false
        return rel !in deniedFiles
    }

    private fun looksBinary(bytes: ByteArray): Boolean {
        val n = minOf(bytes.size, 512)
        for (i in 0 until n) {
            if (bytes[i] == 0.toByte()) return true
        }
        return false
    }

    private fun simpleDiff(old: String, new: String): String {
        val a = old.lines().take(MAX_DIFF_LINES)
        val b = new.lines().take(MAX_DIFF_LINES)
        val out = StringBuilder("--- 原文件\n+++ 新文件\n")
        val max = maxOf(a.size, b.size)
        var i = 0
        while (i < max) {
            val l = a.getOrNull(i)
            val r = b.getOrNull(i)
            if (l != r) {
                if (l != null) out.append('-').append(l).append('\n')
                if (r != null) out.append('+').append(r).append('\n')
            }
            i += 1
        }
        if (old.lines().size > MAX_DIFF_LINES || new.lines().size > MAX_DIFF_LINES) {
            out.append("… diff 已截断\n")
        }
        return out.toString()
    }

    companion object {
        private const val MAX_READ_BYTES = 200 * 1024
        private const val MAX_SEARCH_BYTES = 1024 * 1024
        private const val MAX_SEARCH_FILES = 800
        private const val MAX_HITS = 100
        private const val MAX_EXEC_OUTPUT = 30_000
        private const val MAX_DIFF_LINES = 2_000
    }
}
