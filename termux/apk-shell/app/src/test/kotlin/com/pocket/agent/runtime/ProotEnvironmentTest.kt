package com.pocket.agent.runtime

import android.content.Context
import androidx.test.core.app.ApplicationProvider
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config

/** PRoot 环境状态在 JVM 上必须 fail-closed，不能假装 Linux 可用。 */
@RunWith(RobolectricTestRunner::class)
@Config(sdk = [34])
class ProotEnvironmentTest {
    private val context: Context = ApplicationProvider.getApplicationContext()

    @Test
    fun `status 不触发 rootfs 解压并声明不可用`() {
        val st = ProotEnvironment.status(context)
        assertFalse(st.getBoolean("available"))
        assertFalse(st.getBoolean("rootfs_ready"))
        assertEquals(0L, st.getLong("rootfs_bytes"))
        assertEquals("idle", st.getString("bootstrap"))
    }

    @Test
    fun `selftest 在无 arm64 原生库时返回结构化失败`() {
        val r = ProotEnvironment.selfTest(context)
        assertFalse(r.getBoolean("ok"))
        assertTrue(r.getString("error").isNotBlank())
        assertEquals(0, r.getJSONArray("checks").length())
    }
}
