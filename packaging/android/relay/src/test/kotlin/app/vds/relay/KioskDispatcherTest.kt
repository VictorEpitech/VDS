package app.vds.relay

import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertNull
import kotlin.test.assertTrue

class KioskDispatcherTest {
    private companion object {
        const val TOK = "0123456789abcdef0123"
    }

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

    private class Fx {
        val a = FakeActuator()
        val t = FakeTimer()
        var now = 1_000_000L
        val d = KioskDispatcher(a, t, TOK) { now }
        fun send(mode: String, ms: Long = 0, win: Long = 0) = d.handle("kiosk:k:$mode:$ms:$win:$TOK")
    }

    private fun ok(e: String, mode: String) = """kiosk:k:{"ok":true,"enforcement":"$e","mode":"$mode"}"""

    @Test
    fun maintenanceUnlocksThenRelocksAloneAtDeadline() {
        val f = Fx()
        assertEquals(ok("lock_task", "maintenance"), f.send("maintenance", 600_000, 1))
        assertEquals(KioskMode.MAINTENANCE, f.d.mode)
        assertEquals(600_000L, f.t.delay)
        assertEquals(listOf(KioskMode.MAINTENANCE), f.a.calls)
        f.t.fire()
        assertEquals(KioskMode.LOCKED, f.d.mode)
        assertEquals(listOf(KioskMode.MAINTENANCE, KioskMode.LOCKED), f.a.calls)
    }

    @Test
    fun nativeBoundsMaintenanceTo240Minutes() {
        val f = Fx()
        f.send("maintenance", 99_999_999_999, 1)
        assertEquals(KioskDispatcher.MAX_MAINTENANCE_MS, f.t.delay)
    }

    @Test
    fun reloadOrClockRewindNeverRearmsOrExtendsTheCap() {
        val f = Fx()
        f.send("maintenance", 240L * 60_000, 7)
        f.now += 200L * 60_000 // 200 min plus tard : page rechargée, horloge murale reculée => durée "restante" énorme
        assertEquals(ok("lock_task", "maintenance"), f.send("maintenance", 240L * 60_000, 7))
        assertEquals(40L * 60_000, f.t.delay)
        f.now += 40L * 60_000
        f.t.fire()
        assertEquals(KioskMode.LOCKED, f.d.mode)
        // même fenêtre redemandée après l'échéance : reste verrouillé
        f.send("maintenance", 240L * 60_000, 7)
        assertEquals(KioskMode.LOCKED, f.d.mode)
        // nouvelle fenêtre légitime (nouvel until servi) : accordée
        f.send("maintenance", 10 * 60_000, 8)
        assertEquals(KioskMode.MAINTENANCE, f.d.mode)
    }

    @Test
    fun expiredOrZeroMaintenanceStaysLocked() {
        val f = Fx()
        f.send("maintenance", 0, 1)
        assertEquals(KioskMode.LOCKED, f.d.mode)
        assertEquals(listOf(KioskMode.LOCKED), f.a.calls)
        assertNull(f.t.delay)
    }

    @Test
    fun unlockedIsALeaseAndNewMessageReplacesTimer() {
        val f = Fx()
        f.send("maintenance", 1000, 1)
        f.send("unlocked")
        assertEquals(KioskMode.UNLOCKED, f.d.mode)
        assertEquals(KioskDispatcher.UNLOCK_LEASE_MS, f.t.delay)
        f.t.fire() // le player ne renouvelle plus : reverrouillage
        assertEquals(KioskMode.LOCKED, f.d.mode)
    }

    @Test
    fun softEnforcementIsReported() {
        val f = Fx()
        f.a.enforcement = Enforcement.SOFT
        assertEquals(ok("soft", "locked"), f.send("locked"))
    }

    @Test
    fun failedUnlockFallsBackToLocked() {
        val f = Fx()
        f.a.failOn = KioskMode.UNLOCKED
        assertEquals("""kiosk:k:{"ok":false,"enforcement":"soft","mode":"locked"}""", f.send("unlocked"))
        assertEquals(KioskMode.LOCKED, f.d.mode)
        assertEquals(KioskMode.LOCKED, f.a.calls.last())
    }

    @Test
    fun failedRelockIsRetriedUntilConfirmedAndLockedRequestDoesNotCancelIt() {
        val f = Fx()
        f.send("maintenance", 1000, 1)
        f.a.failOn = KioskMode.LOCKED
        f.t.fire()
        assertEquals(KioskDispatcher.RETRY_MS, f.t.delay)
        assertEquals(KioskMode.MAINTENANCE, f.d.mode) // mode APPLIQUÉ, pas le mode voulu
        // le tick JS envoie locked : échec aussi => ok:false, la relance reste programmée
        assertEquals("""kiosk:k:{"ok":false,"enforcement":"soft","mode":"maintenance"}""", f.send("locked"))
        assertEquals(KioskDispatcher.RETRY_MS, f.t.delay)
        f.t.fire() // encore refusé
        assertEquals(KioskDispatcher.RETRY_MS, f.t.delay)
        // retour au premier plan : le verrouillage réussit
        f.a.failOn = null
        f.d.resume()
        assertEquals(KioskMode.LOCKED, f.d.mode)
        assertNull(f.t.delay)
    }

    @Test
    fun lockFailingThenSucceedingOnNextTimerTick() {
        val f = Fx()
        f.a.failOn = KioskMode.LOCKED
        f.send("locked")
        assertEquals(KioskDispatcher.RETRY_MS, f.t.delay)
        f.a.failOn = null
        f.t.fire()
        assertNull(f.t.delay)
        assertEquals(KioskMode.LOCKED, f.d.mode)
    }

    @Test
    fun wrongOrMissingTokenIsIgnored() {
        val f = Fx()
        assertNull(f.d.handle("kiosk:k:unlocked:0:0:ffffffffffffffffffff"))
        assertNull(f.d.handle("kiosk:k:unlocked:0:0"))
        assertTrue(f.a.calls.isEmpty())
    }

    @Test
    fun rejectsMalformedMessages() {
        val f = Fx()
        val bad = listOf(
            null, "kiosk", "kiosk:", "kiosk::locked:0:0:$TOK", "kiosk:x:open:0:0:$TOK", "kiosk:x:locked:0", "kiosk:x:locked:-1:0:$TOK",
            "kiosk:x:locked:0:0:$TOK;rm", "kiosk:x:maintenance:1e9:0:$TOK", "kiosk:${"a".repeat(40)}:locked:0:0:$TOK",
            "kiosk:x:locked:0:0:$TOK\n", "kiosk:x:locked:9999999999999:0:$TOK",
        )
        for (b in bad) assertNull(f.d.handle(b), "should ignore: $b")
        assertTrue(f.a.calls.isEmpty())
    }
}
