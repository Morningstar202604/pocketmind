package com.pocket.agent.runtime

import android.app.Activity
import android.content.Context
import android.content.pm.PackageManager
import org.json.JSONArray
import org.json.JSONObject
import java.lang.ref.WeakReference
import java.util.concurrent.ConcurrentHashMap
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit

/**
 * 运行时权限桥：把「工具缺少系统权限」从一句报错变成一次可完成的授权。
 *
 * 为什么不在 NativeEngineService 里直接 requestPermissions：Android 只在
 * Activity 上弹权限对话框，Service 没有这个窗口；所以这里保存一个当前
 * Activity 的弱引用，由 HTTP 线程把请求 post 到主线程，再等待回调。
 *
 * 没有 Activity（后台服务/无界面）时明确返回 no_activity，绝不假装授权成功。
 */
object PermissionBroker {

    data class Def(val name: String, val label: String, val group: String)

    /** 只列 manifest 里声明、且工具有可能用到的权限；未知权限一律拒绝请求。 */
    val KNOWN: List<Def> = listOf(
        Def("android.permission.POST_NOTIFICATIONS", "通知", "notification"),
        Def("android.permission.ACCESS_FINE_LOCATION", "精确位置", "location"),
        Def("android.permission.ACCESS_COARSE_LOCATION", "大致位置", "location"),
        Def("android.permission.CAMERA", "相机", "camera"),
        Def("android.permission.SEND_SMS", "发送短信", "sms"),
        Def("android.permission.READ_SMS", "读取短信", "sms"),
        Def("android.permission.CALL_PHONE", "拨打电话", "call"),
    )

    private const val REQUEST_CODE = 1002

    private class Pending(
        val names: List<String>,
        val latch: CountDownLatch,
        val result: MutableMap<String, Boolean>,
    )

    private val lock = Any()
    private var activityRef: WeakReference<Activity>? = null
    private var pending: Pending? = null

    fun attach(activity: Activity) {
        activityRef = WeakReference(activity)
    }

    fun detach(activity: Activity) {
        if (activityRef?.get() === activity) {
            activityRef = null
            pending = null
        }
    }

    fun hasActivity(): Boolean {
        val a = activityRef?.get() ?: return false
        return !a.isFinishing && !a.isDestroyed
    }

    fun status(context: Context): JSONArray = JSONArray().apply {
        KNOWN.forEach { d ->
            put(JSONObject()
                .put("name", d.name)
                .put("label", d.label)
                .put("group", d.group)
                .put("granted", context.checkSelfPermission(d.name) == PackageManager.PERMISSION_GRANTED)
            )
        }
    }

    /**
     * 请求一组权限；返回 name -> granted。
     *
     * 调用线程不是主线程；Activity 的 requestPermissions 必须在主线程。
     * 默认等 30 秒，超时按「未授予」返回，不阻塞 HTTP 线程超过这个时间。
     */
    fun request(names: List<String>, timeoutMs: Long = 30_000): Map<String, Boolean> {
        val activity = activityRef?.get()
        if (activity == null || activity.isFinishing || activity.isDestroyed) {
            throw IllegalStateException("no_activity")
        }
        val valid = names.map { it.trim() }.filter { n -> KNOWN.any { it.name == n } }.distinct()
        if (valid.isEmpty()) throw IllegalArgumentException("unknown_permission")
        val missing = valid.filter {
            activity.checkSelfPermission(it) != PackageManager.PERMISSION_GRANTED
        }
        if (missing.isEmpty()) return valid.associateWith { true }
        val p = Pending(missing, CountDownLatch(1), ConcurrentHashMap())
        synchronized(lock) {
            if (pending != null) throw IllegalStateException("request_in_progress")
            pending = p
        }
        activity.runOnUiThread {
            try {
                activity.requestPermissions(missing.toTypedArray(), REQUEST_CODE)
            } catch (e: Exception) {
                p.latch.countDown()
            }
        }
        val done = try {
            p.latch.await(timeoutMs, TimeUnit.MILLISECONDS)
        } catch (e: InterruptedException) {
            Thread.currentThread().interrupt()
            false
        }
        synchronized(lock) {
            if (pending === p) pending = null
        }
        val result = LinkedHashMap<String, Boolean>()
        valid.forEach { result[it] = p.result[it] ?: false }
        if (!done) result[missing.first()] = false
        return result
    }

    /** MainActivity 的 onRequestPermissionsResult 必须转发到这里。 */
    fun onResult(requestCode: Int, permissions: Array<out String>, grantResults: IntArray) {
        if (requestCode != REQUEST_CODE) return
        val p = pending ?: return
        permissions.forEachIndexed { i, name ->
            p.result[name] = grantResults.getOrNull(i) == PackageManager.PERMISSION_GRANTED
        }
        synchronized(lock) {
            if (pending === p) pending = null
        }
        p.latch.countDown()
    }
}
