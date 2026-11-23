package com.pocket.agent.runtime

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Test

class NativeWebSocketTest {
    @Test
    fun `RFC6455 握手 accept 值`() {
        assertEquals(
            "s3pPLMBiTxaQ9kYGzzhZRbK+xOo=",
            NativeWebSocket.acceptKey("dGhlIHNhbXBsZSBub25jZQ=="),
        )
    }

    @Test
    fun `JVM 上没有 PTY 本地库时明确降级`() {
        // Robolectric 的 JVM 进程加载不了 Android ABI 的 libpty.so；
        // 这里锁住 available=false，避免 UI 误以为终端可用。
        assertFalse(NativePty.available)
    }
}
