package app.vds.relay

import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertNull
import kotlin.test.assertTrue

class KioskDispatcherTest {
    private class FakeActuator(var enforcement: Enforcement = Enforcement.LOCK_TASK) : KioskActuator {
        val calls = mutableListOf<KioskMode>()
        var failOn: KioskMode? = null
        override fun apply(mode: KioskMode): Enforcement {
            if (mode == failOn) error("boom")
            calls += mode
            return enforcement
        }
    }

    private class FakeTimer : KioskTimer {
        var delay: Long? = null
        var task: (() -> Unit)? = null
        override fun schedule(delayMs: Long, task: () -> Unit) {
            delay = delayMs
            this.task = task
        }
        override fun cancel() {
            delay = null
            task = null
        }
        fun fire() {
            val t = task
            cancel()
            t?.invoke()
        }
    }

    private fun setup(): Triple<FakeActuator, FakeTimer, KioskDispatcher> {
        val a = FakeActuator()
        val t = FakeTimer()
        return Triple(a, t, KioskDispatcher(a, t))
    }

    @Test
    fun maintenanceUnlocksThenRelocksAloneAtDeadline() {
        val (a, t, d) = setup()
        assertEquals("""kiosk:k1:{"ok":true,"enforcement":"lock_task"}""", d.handle("kiosk:k1:maintenance:600000"))
        assertEquals(KioskMode.MAINTENANCE, d.mode)
        assertEquals(600_000L, t.delay)
        assertEquals(listOf(KioskMode.UNLOCKED), a.calls)
        t.fire() // échéance : la page n'a rien à faire
        assertEquals(KioskMode.LOCKED, d.mode)
        assertEquals(listOf(KioskMode.UNLOCKED, KioskMode.LOCKED), a.calls)
    }

    @Test
    fun nativeBoundsMaintenanceTo240Minutes() {
        val (_, t, d) = setup()
        d.handle("kiosk:k:maintenance:99999999999")
        assertEquals(KioskDispatcher.MAX_MAINTENANCE_MS, t.delay)
    }

    @Test
    fun expiredOrZeroMaintenanceStaysLocked() {
        val (a, t, d) = setup()
        d.handle("kiosk:k:maintenance:0")
        assertEquals(KioskMode.LOCKED, d.mode)
        assertEquals(listOf(KioskMode.LOCKED), a.calls)
        assertNull(t.delay)
    }

    @Test
    fun unlockedHasNoDeadlineAndNewMessageCancelsTimer() {
        val (_, t, d) = setup()
        d.handle("kiosk:k:maintenance:1000")
        d.handle("kiosk:k:unlocked:0")
        assertEquals(KioskMode.UNLOCKED, d.mode)
        assertNull(t.delay)
        d.handle("kiosk:k:locked:0")
        assertEquals(KioskMode.LOCKED, d.mode)
    }

    @Test
    fun softEnforcementIsReported() {
        val (a, _, d) = setup()
        a.enforcement = Enforcement.SOFT
        assertEquals("""kiosk:z:{"ok":true,"enforcement":"soft"}""", d.handle("kiosk:z:locked:0"))
    }

    @Test
    fun failedUnlockFallsBackToLocked() {
        val (a, _, d) = setup()
        a.failOn = KioskMode.UNLOCKED
        assertEquals("""kiosk:k:{"ok":false,"enforcement":"soft"}""", d.handle("kiosk:k:unlocked:0"))
        assertEquals(KioskMode.LOCKED, d.mode)
        assertEquals(KioskMode.LOCKED, a.calls.last())
    }

    @Test
    fun failedRelockRetriesAtNextDeadline() {
        val (a, t, d) = setup()
        d.handle("kiosk:k:maintenance:1000")
        a.failOn = KioskMode.LOCKED
        t.fire()
        assertEquals(KioskDispatcher.RETRY_MS, t.delay)
        assertEquals(KioskMode.MAINTENANCE, d.mode)
        a.failOn = null
        t.fire()
        assertEquals(KioskMode.LOCKED, d.mode)
    }

    @Test
    fun rejectsMalformedMessages() {
        val (a, _, d) = setup()
        val bad = listOf(
            null, "kiosk", "kiosk:", "kiosk::locked:0", "kiosk:x:open:0", "kiosk:x:locked", "kiosk:x:locked:-1",
            "kiosk:x:locked:0;rm", "kiosk:x:maintenance:1e9", "kiosk:${"a".repeat(40)}:locked:0", "kiosk:x:locked:0\n",
            "kiosk:x:locked:9999999999999",
        )
        for (b in bad) assertNull(d.handle(b), "should ignore: $b")
        assertTrue(a.calls.isEmpty())
    }
}
