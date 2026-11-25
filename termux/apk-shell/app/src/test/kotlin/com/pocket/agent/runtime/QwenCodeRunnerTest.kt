package com.pocket.agent.runtime

import android.content.Context
import androidx.test.core.app.ApplicationProvider
import com.pocket.agent.agent.QwenCodeRunner
import com.pocket.agent.data.SettingsStore
import com.pocket.agent.tools.DeviceEnv
import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config

/** Qwen Code 是 danger 工具；JVM 上必须 fail-closed，不能假装能跑内置 CLI。 */
@RunWith(RobolectricTestRunner::class)
@Config(sdk = [34])
class QwenCodeRunnerTest {
    private val context: Context = ApplicationProvider.getApplicationContext()

    @Test
    fun `qwen_code 元数据是 danger 且归入 agent`() {
        val def = QwenCodeRunner.toolDef(SettingsStore(context))
        assertEquals("qwen_code", def.name)
        assertEquals("danger", def.risk)
        assertEquals("agent", def.group)
    }

    @Test
    fun `qwen_code 在无 PRoot 时结构化失败`() {
        val settings = SettingsStore(context)
        val def = QwenCodeRunner.toolDef(settings)
        val env = DeviceEnv(context, com.pocket.agent.data.Store(context), "test")
        val out = def.handler(JSONObject().put("prompt", "分析当前工作区"), env)
        assertFalse(out.optBoolean("ok", false))
        assertEquals("capability_unavailable", out.optString("error_code"))
    }
}
