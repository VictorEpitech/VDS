package app.vds.relay

import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertNull

class CecDispatcherTest {
    private class Fake(var avail: Boolean = true, var boom: Boolean = false) : CecController {
        val calls = mutableListOf<String>()
        override fun available() = avail
        override fun power(on: Boolean): CecResult {
            if (boom) error("x")
            calls += if (on) "on" else "off"
            return CecResult(true, if (on) "on" else "standby", true)
        }
        override fun state() = "standby"
    }

    @Test
    fun routesActionsAndEchoesId() {
        val f = Fake()
        val d = CecDispatcher(f)
        assertEquals("""cec:a1:{"ok":true,"state":"on","verified":true}""", d.handle("cec:a1:on"))
        assertEquals("""cec:a2:{"ok":true,"state":"standby","verified":true}""", d.handle("cec:a2:off"))
        assertEquals("""cec:a3:{"ok":true,"state":"standby","verified":false}""", d.handle("cec:a3:state"))
        assertEquals("""cec:a4:{"ok":true,"state":"unknown","verified":false}""", d.handle("cec:a4:probe"))
        assertEquals(listOf("on", "off"), f.calls)
    }

    @Test
    fun unavailableAndFailures() {
        assertEquals(
            """cec:b:{"ok":false,"state":"unknown","verified":false,"code":"power.cec.unavailable"}""",
            CecDispatcher(null).handle("cec:b:on"),
        )
        assertEquals(
            """cec:b:{"ok":false,"state":"unknown","verified":false,"code":"power.cec.unavailable"}""",
            CecDispatcher(Fake(avail = false)).handle("cec:b:probe"),
        )
        assertEquals(
            """cec:b:{"ok":false,"state":"unknown","verified":false,"code":"power.cec.failed"}""",
            CecDispatcher(Fake(boom = true)).handle("cec:b:on"),
        )
    }

    @Test
    fun rejectsMalformedMessages() {
        val d = CecDispatcher(Fake())
        for (bad in listOf(null, "ping", "cec:", "cec::on", "cec:x:reboot", "cec:x:on;rm", "cec:${"a".repeat(40)}:on", "cec:x:on\n")) {
            assertNull(d.handle(bad), "should ignore: $bad")
        }
    }
}
