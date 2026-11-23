package com.pocket.agent.runtime

import android.content.Context
import org.json.JSONObject
import java.io.File
import java.util.UUID
import java.util.concurrent.ConcurrentHashMap
import java.util.concurrent.TimeUnit

/**
 * 原生工作区后台任务：长命令不占住 HTTP 请求，可查询、可取消。
 *
 * 输出有增量快照（最多 30KB），并提供 SSE 任务流；任务元数据落
 * `workspace_tasks/<id>.json` 并从工作区文件树里隐藏。进程运行在 app uid 的
 * `/system/bin/sh` 下，没有 Python/Node。
 */
class WorkspaceTasks(private val context: Context) {

    private val tasks = ConcurrentHashMap<String, JSONObject>()
    private val processes = ConcurrentHashMap<String, Process>()
    private val logDir = File(context.filesDir, "workspace_tasks")

    fun start(command: String, cwd: File, timeoutSec: Int): JSONObject {
        val cmd = command.trim()
        if (cmd.isEmpty()) throw NativeWorkspace.WorkspaceException(400, "command 不能为空")
        val id = UUID.randomUUID().toString().replace("-", "").take(12)
        val now = System.currentTimeMillis() / 1000.0
        val rec = JSONObject()
            .put("id", id)
            .put("command", cmd)
            .put("cwd", cwd.absolutePath)
            .put("timeout", timeoutSec.coerceIn(1, 300))
            .put("status", "running")
            .put("created_at", now)
            .put("started_at", now)
            .put("ended_at", JSONObject.NULL)
            .put("exit_code", JSONObject.NULL)
            .put("stdout", "").put("stderr", "").put("error", "")
            .put("timed_out", false).put("truncated", false)
            .put("log_path", JSONObject.NULL)
        val sh = listOf("/system/bin/sh", "/bin/sh").firstOrNull { File(it).exists() } ?: "sh"
        val proc = try {
            ProcessBuilder(sh, "-c", cmd).directory(cwd).apply {
                environment()["HOME"] = context.filesDir.absolutePath
                environment()["TMPDIR"] = context.cacheDir.absolutePath
                environment()["PATH"] = "/system/bin:/system/xbin:/vendor/bin:/usr/bin:/bin"
            }.start()
        } catch (e: Exception) {
            throw NativeWorkspace.WorkspaceException(500, "启动 shell 失败：${e.message}")
        }
        tasks[id] = rec
        processes[id] = proc
        trim()
        Thread({ run(id, proc, rec) }, "ws-task-$id").start()
        return public(rec)
    }

    fun list(limit: Int): JSONObject {
        val items = tasks.values.sortedByDescending { it.optDouble("created_at", 0.0) }
        val arr = org.json.JSONArray()
        items.take(limit.coerceIn(1, 50)).forEach { rec -> arr.put(synchronized(rec) { public(rec) }) }
        return JSONObject().put("items", arr).put("count", arr.length())
    }

    fun get(id: String): JSONObject =
        tasks[id]?.let { rec -> synchronized(rec) { public(rec) } }
            ?: JSONObject().put("error", "任务不存在")

    private fun trim() {
        if (tasks.size <= MAX_TASKS) return
        val finished = tasks.values
            .filter { it.optString("status") !in setOf("running", "cancelling") }
            .sortedBy { it.optDouble("created_at", 0.0) }
        for (rec in finished.take(tasks.size - MAX_TASKS)) {
            val oldId = rec.optString("id")
            tasks.remove(oldId)
            processes.remove(oldId)
            runCatching { File(logDir, "$oldId.json").delete() }
        }
    }

    /** SSE：每 0.5s 推一次快照，状态终结后发 done 并关闭。最长挂 300s，客户端可重连。 */
    fun stream(id: String, writer: SseWriter, maxSeconds: Int = 300) {
        val started = System.currentTimeMillis()
        var last = ""
        while (System.currentTimeMillis() - started < maxSeconds.coerceIn(1, 600) * 1000L) {
            val rec = get(id)
            if (!rec.has("id")) {
                writer.send(JSONObject().put("type", "error")
                    .put("error", rec.optString("error", "任务不存在")).toString())
                break
            }
            val payload = JSONObject().put("type", "snapshot").put("task", rec).toString()
            if (payload != last) {
                if (!writer.send(payload)) break
                last = payload
            }
            if (rec.optString("status") !in setOf("running", "cancelling")) {
                writer.send(JSONObject().put("type", "done").put("task", rec).toString())
                break
            }
            try { Thread.sleep(500) } catch (_: InterruptedException) { break }
        }
        writer.close()
    }

    fun cancel(id: String): JSONObject {
        val rec = tasks[id] ?: return JSONObject().put("error", "任务不存在")
        if (rec.optString("status") !in setOf("running", "cancelling")) {
            return JSONObject().put("error", "任务已是 ${rec.optString("status")} 状态，不能取消")
        }
        rec.put("status", "cancelling")
        processes[id]?.destroy()
        return JSONObject().put("ok", true).put("id", id).put("status", "cancelling")
    }

    private fun run(id: String, proc: Process, rec: JSONObject) {
        val out = StringBuilder()
        val err = StringBuilder()
        val t1 = Thread {
            runCatching {
                proc.inputStream.bufferedReader().use { r ->
                    r.lineSequence().forEach { line ->
                        synchronized(rec) {
                            if (out.length < MAX_OUTPUT) {
                                out.append(line).append('\n')
                                rec.put("stdout", out.toString().take(MAX_OUTPUT))
                                rec.put("updated_at", System.currentTimeMillis() / 1000.0)
                            }
                        }
                    }
                }
            }
        }
        val t2 = Thread {
            runCatching {
                proc.errorStream.bufferedReader().use { r ->
                    r.lineSequence().forEach { line ->
                        synchronized(rec) {
                            if (err.length < MAX_OUTPUT) {
                                err.append(line).append('\n')
                                rec.put("stderr", err.toString().take(MAX_OUTPUT))
                                rec.put("updated_at", System.currentTimeMillis() / 1000.0)
                            }
                        }
                    }
                }
            }
        }
        t1.start(); t2.start()
        val timeout = rec.optInt("timeout", 60).coerceIn(1, 300)
        val finished = runCatching { proc.waitFor(timeout.toLong(), TimeUnit.SECONDS) }.getOrDefault(false)
        if (!finished) {
            proc.destroy()
            runCatching { if (!proc.waitFor(2, TimeUnit.SECONDS)) proc.destroyForcibly() }
        }
        t1.join(2_000); t2.join(2_000)
        val cancelled = rec.optString("status") == "cancelling"
        val status = when {
            cancelled -> "cancelled"
            !finished -> "failed"
            proc.exitValue() == 0 -> "done"
            else -> "failed"
        }
        synchronized(rec) {
            rec.put("status", status)
            rec.put("ended_at", System.currentTimeMillis() / 1000.0)
            if (finished) runCatching { rec.put("exit_code", proc.exitValue()) }
            rec.put("stdout", out.toString().take(MAX_OUTPUT))
            rec.put("stderr", err.toString().take(MAX_OUTPUT))
            rec.put("error", when {
                cancelled -> "任务已取消"
                !finished -> "任务超时"
                else -> ""
            })
            rec.put("timed_out", !finished && !cancelled)
            rec.put("truncated", out.length >= MAX_OUTPUT || err.length >= MAX_OUTPUT)
            rec.put("log_path", persist(rec))
        }
        processes.remove(id)
    }

    private fun persist(rec: JSONObject): String? = runCatching {
        logDir.mkdirs()
        val path = File(logDir, "${rec.optString("id")}.json")
        path.writeText(public(rec).toString(2))
        path.absolutePath
    }.getOrNull()

    private fun public(rec: JSONObject): JSONObject = JSONObject(rec.toString())

    companion object {
        private const val MAX_OUTPUT = 30_000
        private const val MAX_TASKS = 100
        private val INSTANCES = ConcurrentHashMap<String, WorkspaceTasks>()

        fun get(context: Context): WorkspaceTasks =
            INSTANCES.getOrPut(context.applicationContext.packageName) {
                WorkspaceTasks(context.applicationContext)
            }
    }
}
