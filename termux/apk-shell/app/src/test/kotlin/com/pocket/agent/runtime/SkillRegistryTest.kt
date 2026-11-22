package com.pocket.agent.runtime

import android.content.Context
import androidx.test.core.app.ApplicationProvider
import com.pocket.agent.data.Store
import com.pocket.agent.skills.SkillRegistry
import com.pocket.agent.tools.DeviceEnv
import com.pocket.agent.tools.ToolRegistry
import org.json.JSONObject
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config
import java.io.File

/** M1.4 原生 Skills：frontmatter、渐进披露、read_skill 工具与缺失技能回退。 */
@RunWith(RobolectricTestRunner::class)
@Config(sdk = [34])
class SkillRegistryTest {

    private lateinit var context: Context
    private lateinit var root: File

    @Before
    fun setUp() {
        context = ApplicationProvider.getApplicationContext()
        root = File(context.filesDir, "skills")
        root.deleteRecursively()
        root.mkdirs()
    }

    @After
    fun tearDown() {
        root.deleteRecursively()
    }

    private fun skill(dir: String, content: String) {
        val d = File(root, dir)
        d.mkdirs()
        File(d, "SKILL.md").writeText(content, Charsets.UTF_8)
    }

    @Test
    fun `扫描 frontmatter 并从正文回退描述`() {
        skill("battery", "---\nname: battery-care\ndescription: 电池保养\n---\n# 标题\n第一步")
        skill("plain", "# 手写技能\n这是一段没有 frontmatter 的描述\n")
        val items = SkillRegistry(root).list()
        assertEquals(2, items.size)
        assertTrue(items.any { it.name == "battery-care" && it.description == "电池保养" })
        assertTrue(items.any { it.name == "plain" && it.description == "这是一段没有 frontmatter 的描述" })
    }

    @Test
    fun `正文按需读取 未知技能返回 null`() {
        skill("demo", "---\nname: demo\ndescription: 演示\n---\n完整步骤在这里")
        val reg = SkillRegistry(root)
        assertEquals("完整步骤在这里", reg.read("DEMO"))
        assertEquals(null, reg.read("missing"))
    }

    @Test
    fun `system 索引只含名称与描述 不含正文`() {
        skill("demo", "---\nname: demo\ndescription: 演示\n---\nSECRET_BODY")
        val block = SkillRegistry(root).systemBlock()
        assertTrue(block.contains("demo"))
        assertTrue(block.contains("演示"))
        assertFalse(block.contains("SECRET_BODY"))
        assertTrue(block.contains("read_skill"))
    }

    @Test
    fun `read_skill 工具返回正文与可读技能列表`() {
        skill("demo", "---\nname: demo\ndescription: 演示\n---\n按这三步做")
        val tool = ToolRegistry.byName("read_skill")
        assertNotNull("应注册 read_skill 工具", tool)
        val env = DeviceEnv(context, Store(context), "skill-test")
        val ok = tool!!.handler(JSONObject().put("name", "demo"), env)
        assertEquals("按这三步做", ok.optString("content"))
        val missing = tool.handler(JSONObject().put("name", "nope"), env)
        assertTrue(missing.optString("error").contains("没有名为"))
        assertEquals(1, missing.optJSONArray("available")?.length())
    }
}
