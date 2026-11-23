package com.pocket.agent.runtime

import android.content.Context
import org.json.JSONArray
import org.json.JSONObject
import java.io.File
import java.nio.file.Files
import java.nio.file.StandardCopyOption
import java.util.UUID

/**
 * 工作区变更记录与撤销。与 Python 的 workspace_changes.json 保持同样的字段，
 * 但备份放在应用私有的 `.workspace-backups`，既不进文件树，也不会被 Web UI 读到。
 *
 * 撤销仍然防「改完又改」：记录写入后的 mtime（毫秒）和大小，目标对不上就拒绝，
 * 避免把用户在保存后的第二次修改一并覆盖掉。
 */
class WorkspaceChanges(private val context: Context, private val root: File) {

    private val storeFile = File(root, "workspace_changes.json")
    private val backupDir = File(root, ".workspace-backups")

    fun hiddenPaths(): Set<String> = setOf(storeFile.name, backupDir.name)

    /** 写文件之前调用；返回备份文件的绝对路径，文件不存在或备份失败时返回 null。 */
    fun backupForWrite(target: File): String? {
        if (!target.isFile) return null
        if (target.length() > MAX_BACKUP_BYTES) return null
        return runCatching {
            backupDir.mkdirs()
            val backup = File(backupDir, "${UUID.randomUUID().toString().take(8)}-${target.name}")
            target.copyTo(backup, overwrite = true)
            backup.setLastModified(target.lastModified())
            backup.absolutePath
        }.getOrNull()
    }

    fun record(
        path: String,
        kind: String,
        backup: String? = null,
        afterMtimeMs: Long? = null,
        afterSize: Long? = null,
        bytes: Int = 0,
        dir: Boolean = false,
    ): JSONObject = synchronized(LOCK) {
        require(kind in setOf("overwrite", "create", "delete")) { "未知变更类型：$kind" }
        val items = read()
        val item = JSONObject()
            .put("id", UUID.randomUUID().toString().replace("-", "").take(12))
            .put("path", path)
            .put("kind", kind)
            .put("backup", backup)
            .put("after_mtime", afterMtimeMs)
            .put("after_size", afterSize)
            .put("bytes", bytes)
            .put("dir", dir)
            .put("source", "ui")
            .put("created_at", System.currentTimeMillis() / 1000.0)
            .put("undone", false)
        items.put(item)
        trim(items)
        write(items)
        item
    }

    fun list(limit: Int, includeUndone: Boolean): JSONObject = synchronized(LOCK) {
        val items = read()
        val visible = JSONArray()
        for (i in items.length() - 1 downTo 0) {
            val item = items.getJSONObject(i)
            if (!includeUndone && item.optBoolean("undone")) continue
            visible.put(item)
            if (visible.length() >= limit.coerceIn(1, 200)) break
        }
        JSONObject().put("items", visible).put("count", visible.length())
    }

    /**
     * 撤销当前这一批未撤销的变更（变更集），按记录倒序执行。
     * 单条失败不会中断其余条目，结果里逐条返回，让 UI 能说明哪一条没还原。
     */
    fun undoAll(): JSONObject = synchronized(LOCK) {
        val items = read()
        val results = JSONArray()
        var ok = 0
        var failed = 0
        for (i in items.length() - 1 downTo 0) {
            val item = items.getJSONObject(i)
            if (item.optBoolean("undone")) continue
            val result = undo(item.optString("id"))
            val entry = JSONObject().put("id", item.optString("id"))
                .put("path", item.optString("path"))
                .put("ok", result.optBoolean("ok"))
            if (!result.optBoolean("ok")) {
                entry.put("error", result.optString("error"))
                failed += 1
            } else ok += 1
            results.put(entry)
        }
        JSONObject().put("ok", failed == 0).put("restored", ok).put("failed", failed)
            .put("results", results)
    }

    fun undo(id: String): JSONObject = synchronized(LOCK) {
        val items = read()
        var found: JSONObject? = null
        for (i in 0 until items.length()) {
            val item = items.getJSONObject(i)
            if (item.optString("id") == id) { found = item; break }
        }
        val item = found ?: return@synchronized JSONObject().put("error", "变更记录不存在")
        if (item.optBoolean("undone")) return@synchronized JSONObject().put("error", "该变更已撤销过")

        val target = File(item.optString("path"))
        if (target.canonicalPath != root.canonicalPath &&
            !target.canonicalPath.startsWith(root.canonicalPath + File.separator)
        ) return@synchronized JSONObject().put("error", "变更路径已越出工作区")

        val kind = item.optString("kind")
        val isDir = item.optBoolean("dir")
        val backup = item.optString("backup").takeIf { it.isNotEmpty() }?.let { File(it) }
        try {
            when {
                kind == "create" && isDir -> {
                    if (!target.exists()) return@synchronized JSONObject().put("error", "目录已不存在，无需撤销")
                    if (!target.isDirectory) return@synchronized JSONObject().put("error", "路径已不是目录")
                    if (target.listFiles()?.isNotEmpty() == true) {
                        return@synchronized JSONObject().put("error", "目录里已有文件，已拒绝撤销；请先清空")
                    }
                    if (!target.delete()) return@synchronized JSONObject().put("error", "删除目录失败")
                }
                kind == "create" -> {
                    if (!target.exists()) return@synchronized JSONObject().put("error", "文件已不存在，无需撤销")
                    if (!matchesAfter(target, item)) {
                        return@synchronized JSONObject().put("error", "文件在新建后又被修改，已拒绝撤销；请先确认内容")
                    }
                    if (!target.delete()) return@synchronized JSONObject().put("error", "删除文件失败")
                }
                kind == "overwrite" -> {
                    if (backup == null || !backup.isFile) {
                        return@synchronized JSONObject().put("error", "备份文件已丢失，无法撤销")
                    }
                    if (!target.isFile || !matchesAfter(target, item)) {
                        return@synchronized JSONObject().put("error", "文件在保存后又被修改，已拒绝撤销；请先确认内容")
                    }
                    restore(backup, target)
                }
                kind == "delete" -> {
                    if (target.exists()) {
                        return@synchronized JSONObject().put("error", "该路径已被重新创建，已拒绝撤销；请先确认内容")
                    }
                    if (isDir) {
                        target.mkdirs()
                        if (!target.isDirectory) return@synchronized JSONObject().put("error", "恢复目录失败")
                    } else {
                        if (backup == null || !backup.isFile) {
                            return@synchronized JSONObject().put("error", "备份文件已丢失，无法撤销")
                        }
                        restore(backup, target)
                    }
                }
                else -> return@synchronized JSONObject().put("error", "未知变更类型：$kind")
            }
        } catch (e: Exception) {
            return@synchronized JSONObject().put("error", "撤销失败：${e.message}")
        }
        item.put("undone", true).put("undone_at", System.currentTimeMillis() / 1000.0)
        write(items)
        JSONObject().put("ok", true).put("restored", target.absolutePath).put("kind", kind)
    }

    private fun restore(backup: File, target: File) {
        target.parentFile?.mkdirs()
        val tmp = File(target.parentFile, ".${target.name}.undo-${UUID.randomUUID().toString().take(8)}")
        try {
            Files.copy(backup.toPath(), tmp.toPath(), StandardCopyOption.REPLACE_EXISTING)
            try {
                Files.move(tmp.toPath(), target.toPath(), StandardCopyOption.REPLACE_EXISTING, StandardCopyOption.ATOMIC_MOVE)
            } catch (_: Exception) {
                Files.move(tmp.toPath(), target.toPath(), StandardCopyOption.REPLACE_EXISTING)
            }
            target.setLastModified(backup.lastModified())
        } finally {
            tmp.delete()
        }
    }

    private fun matchesAfter(target: File, item: JSONObject): Boolean {
        if (!target.isFile) return false
        if (item.has("after_mtime") && !item.isNull("after_mtime") &&
            item.optLong("after_mtime") != target.lastModified()
        ) return false
        if (item.has("after_size") && !item.isNull("after_size") &&
            item.optLong("after_size") != target.length()
        ) return false
        return true
    }

    private fun read(): JSONArray {
        if (!storeFile.isFile) return JSONArray()
        return runCatching { JSONArray(storeFile.readText()) }.getOrDefault(JSONArray())
    }

    private fun write(items: JSONArray) {
        val tmp = File(root, "workspace_changes.json.tmp")
        tmp.writeText(items.toString(2))
        try {
            Files.move(tmp.toPath(), storeFile.toPath(), StandardCopyOption.REPLACE_EXISTING, StandardCopyOption.ATOMIC_MOVE)
        } catch (_: Exception) {
            Files.move(tmp.toPath(), storeFile.toPath(), StandardCopyOption.REPLACE_EXISTING)
        }
    }

    private fun trim(items: JSONArray) {
        val extra = items.length() - MAX_RECORDS
        if (extra <= 0) return
        val removed = mutableListOf<JSONObject>()
        for (i in 0 until extra) removed.add(items.getJSONObject(i))
        val keep = JSONArray()
        for (i in extra until items.length()) keep.put(items.getJSONObject(i))
        while (items.length() > 0) items.remove(items.length() - 1)
        for (i in 0 until keep.length()) items.put(keep.getJSONObject(i))
        removed.forEach { item ->
            item.optString("backup").takeIf { it.isNotEmpty() }?.let { runCatching { File(it).delete() } }
        }
    }

    companion object {
        private val LOCK = Any()
        private const val MAX_RECORDS = 200
        private const val MAX_BACKUP_BYTES = 20L * 1024 * 1024
    }
}
