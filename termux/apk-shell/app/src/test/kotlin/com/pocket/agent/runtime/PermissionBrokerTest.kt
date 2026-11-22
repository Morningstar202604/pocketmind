package com.pocket.agent.runtime

import android.Manifest
import android.app.Activity
import android.content.pm.PackageManager
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.Robolectric
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config

/** M1.5 权限桥：回调不会被 request 的锁挡住；无 Activity 时明确拒绝。 */
@RunWith(RobolectricTestRunner::class)
@Config(sdk = [34])
class PermissionBrokerTest {

    private var controller: org.robolectric.android.controller.ActivityController<Activity>? = null

    @After
    fun tearDown() {
        controller?.let {
            PermissionBroker.detach(it.get())
            it.destroy()
        }
    }

    @Test
    fun `请求在后台线程等待 主线程回调后返回已授予`() {
        controller = Robolectric.buildActivity(Activity::class.java).setup()
        val activity = controller!!.get()
        PermissionBroker.attach(activity)
        var result: Map<String, Boolean>? = null
        val worker = Thread {
            result = PermissionBroker.request(listOf(Manifest.permission.CAMERA), 2_000)
        }
        worker.start()
        Thread.sleep(80)
        PermissionBroker.onResult(
            1002,
            arrayOf(Manifest.permission.CAMERA),
            intArrayOf(PackageManager.PERMISSION_GRANTED),
        )
        worker.join(3_000)
        assertEquals(true, result?.get(Manifest.permission.CAMERA))
        assertFalse("request 不应一直占着锁", worker.isAlive)
    }

    @Test
    fun `没有前台 Activity 时抛出 no_activity`() {
        val e = runCatching {
            PermissionBroker.request(listOf(Manifest.permission.CAMERA), 100)
        }.exceptionOrNull()
        assertTrue(e is IllegalStateException)
        assertEquals("no_activity", e?.message)
    }
}
