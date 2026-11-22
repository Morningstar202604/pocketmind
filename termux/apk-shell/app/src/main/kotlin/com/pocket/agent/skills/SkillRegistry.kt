package com.pocket.agent.skills

import android.content.Context
import org.json.JSONArray
import org.json.JSONObject
import java.io.File

/**
 * 原生 Skills 目录：与 Python `agentd/skills.py` 同一套目录约定。
 *
 * `filesDir/skills/<name>/SKILL.md`，只把 name + description 注入 system 前缀；
 * 正文由模型按需调用 `read_skill` 读取。这样挂几十个技能也不会撑爆手机端上下文。
 *
 * 不做 mtime 缓存：手机上的技能目录通常只有几个文件，每次扫描成本远低于一次
 * LLM 请求；缓存反而会在用户直接改文件后返回旧索引，调试时难以解释。
 */
class SkillRegistry(val root: File) {

    data class Skill(
        val name: String,
        val description: String,
        val dir: File,
        val file: File,
    )

    fun list(): List<Skill> {
        if (!root.isDirectory) return emptyList()
        val items = ArrayList<Skill>()
        root.listFiles()
            ?.filter { it.isDirectory }
            ?.sortedBy { it.name.lowercase() }
            ?.forEach { child ->
                val md = File(child, "SKILL.md")
                if (!md.isFile) return@forEach
                val raw = runCatching { md.readText(Charsets.UTF_8) }.getOrNull() ?: return@forEach
                val (meta, body) = parseFrontmatter(raw)
                val name = meta["name"]?.takeIf { it.isNotBlank() } ?: child.name
                val desc = meta["description"]?.takeIf { it.isNotBlank() }
                    ?: body.lineSequence()
                        .map { it.trim() }
                        .firstOrNull { it.isNotEmpty() && !it.startsWith("#") }
                    ?: ""
                items.add(Skill(name, desc.take(MAX_DESC_CHARS), child, md))
            }
        return items
    }

    /** 按名字读取正文（去掉 frontmatter）；未知技能返回 null。 */
    fun read(name: String): String? {
        val target = name.trim().lowercase()
        if (target.isEmpty()) return null
        val skill = list().firstOrNull { it.name.lowercase() == target } ?: return null
        val raw = runCatching { skill.file.readText(Charsets.UTF_8) }.getOrNull() ?: return null
        val (_, body) = parseFrontmatter(raw)
        return body.take(MAX_BODY_CHARS)
    }

    /** 注入 system 的索引；内容按名字排序，保持前缀稳定。 */
    fun systemBlock(): String {
        val items = list()
        if (items.isEmpty()) return ""
        return buildString {
            append("可用技能（只有当任务与描述匹配时才调用 read_skill 读取完整步骤，不要凭空猜测技能内容）：")
            items.forEach { append("\n- ").append(it.name).append("：").append(it.description) }
        }
    }

    fun listJson(): JSONArray = JSONArray().apply {
        list().forEach { put(JSONObject().put("name", it.name).put("description", it.description)) }
    }

    private fun parseFrontmatter(raw: String): Pair<Map<String, String>, String> {
        val text = raw.removePrefix("\ufeff")
        if (!text.startsWith("---")) return emptyMap<String, String>() to text
        val lines = text.lines()
        val end = (1 until lines.size).firstOrNull { lines[it].trim() == "---" }
            ?: return emptyMap<String, String>() to text
        val meta = LinkedHashMap<String, String>()
        for (i in 1 until end) {
            val idx = lines[i].indexOf(':')
            if (idx <= 0) continue
            val key = lines[i].substring(0, idx).trim().lowercase()
            val value = lines[i].substring(idx + 1).trim().trim('"').trim('\'')
            if (key.isNotEmpty() && value.isNotEmpty()) meta[key] = value
        }
        val body = lines.subList(end + 1, lines.size).joinToString("\n").trim()
        return meta to body
    }

    companion object {
        const val MAX_BODY_CHARS = 12_000
        const val MAX_DESC_CHARS = 200

        fun forContext(context: Context): SkillRegistry =
            SkillRegistry(File(context.filesDir, "skills"))
    }
}
