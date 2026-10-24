package app.vds.relay

import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertTrue

class BridgeRouterTest {
    private companion object {
        const val TOK = "0123456789abcdef0123"
    }

    private class Act : KioskActuator {
        val calls = mutableListOf<KioskMode>()
        override fun apply(mode: KioskMode): Enforcement {
            calls += mode
            return Enforcement.LOCK_TASK
        }
    }

    private object NoTimer : KioskTimer {
        override fun schedule(delayMs: Long, task: () -> Unit) {}
        override fun cancel() {}
    }

    private class Fixture(var trusted: Boolean = true) {
        val act = Act()
        val pings = mutableListOf<Unit>()
        val reloads = mutableListOf<Unit>()
        val replies = mutableListOf<String>()
        val router = BridgeRouter(
            trusted = { trusted },
            onPing = { pings += Unit },
            onReload = { reloads += Unit },
            kiosk = KioskDispatcher(act, NoTimer, TOK),
        )
    }

    @Test
    fun mainFrameOfTrustedPageIsServed() {
        val f = Fixture()
        f.router.onMessage(true, "ping")
        f.router.onMessage(true, "reload")
        f.router.onMessage(true, "kiosk:a:unlocked:0:0:$TOK") { f.replies += it }
        assertEquals(1, f.pings.size)
        assertEquals(1, f.reloads.size)
        assertEquals(listOf("""kiosk:a:{"ok":true,"enforcement":"lock_task","mode":"unlocked"}"""), f.replies)
        assertEquals(listOf(KioskMode.UNLOCKED), f.act.calls)
    }

    @Test
    fun subFramesCannotUnlockNorReload() {
        val f = Fixture()
        // Une iframe content.url n'a pas le pont ; même un message forgé est refusé côté natif.
        for (m in listOf("ping", "reload", "kiosk:a:unlocked:0:0:$TOK", "cec:a:on")) f.router.onMessage(false, m) { f.replies += it }
        assertTrue(f.pings.isEmpty() && f.reloads.isEmpty() && f.replies.isEmpty() && f.act.calls.isEmpty())
    }

    @Test
    fun untrustedPageCannotUnlock() {
        val f = Fixture(trusted = false)
        f.router.onMessage(true, "kiosk:a:unlocked:0:0:$TOK") { f.replies += it }
        f.router.onMessage(true, "reload")
        assertTrue(f.reloads.isEmpty() && f.replies.isEmpty() && f.act.calls.isEmpty())
    }

    @Test
    fun wrongTokenIsIgnored() {
        val f = Fixture()
        f.router.onMessage(true, "kiosk:a:unlocked:0:0:ffffffffffffffffffff") { f.replies += it }
        f.router.onMessage(true, "kiosk:a:unlocked:0") { f.replies += it }
        assertTrue(f.replies.isEmpty() && f.act.calls.isEmpty())
    }

    @Test
    fun unknownAndMissingKioskAreIgnored() {
        val f = Fixture()
        f.router.onMessage(true, "bogus")
        f.router.onMessage(true, null)
        f.router.onMessage(true, "kiosk:a:open:0:0:$TOK") { f.replies += it }
        assertTrue(f.replies.isEmpty() && f.act.calls.isEmpty())
        BridgeRouter({ true }, {}, {}).onMessage(true, "kiosk:a:unlocked:0:0:$TOK") { f.replies += it }
        assertTrue(f.replies.isEmpty())
    }
}
