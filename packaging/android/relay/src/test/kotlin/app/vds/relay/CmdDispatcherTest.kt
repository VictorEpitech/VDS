package app.vds.relay

import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertTrue

class CmdDispatcherTest {
    private companion object {
        const val TOK = "0123456789abcdef0123"
    }

    private class Act(
        override val canScreenshot: Boolean = true,
        override val canRestartApp: Boolean = true,
        override val canRestartDevice: Boolean = false,
    ) : CmdActuator {
        var pending: ((Result<ByteArray>) -> Unit)? = null
        var shots = 0
        var app = 0
        var device = 0
        override fun screenshot(maxEdge: Int, quality: Int, done: (Result<ByteArray>) -> Unit) {
            shots++
            pending = done
        }
        override fun restartApp() { app++ }
        override fun restartDevice() { device++ }
    }

    private class Sched : CmdScheduler {
        val tasks = mutableListOf<() -> Unit>()
        override fun postDelayed(delayMs: Long, task: () -> Unit) { tasks += task }
        fun run() { tasks.toList().also { tasks.clear() }.forEach { it() } }
    }

    private class Fx(device: Boolean = false, shot: Boolean = true) {
        var now = 1_000_000L
        val act = Act(canScreenshot = shot, canRestartDevice = device)
        val sched = Sched()
        val out = mutableListOf<String>()
        val d = CmdDispatcher(act, sched, TOK) { now }
        fun send(m: String) = d.handle(m) { out += it }
    }

    @Test
    fun capsReflectActuator() {
        val f = Fx(device = true)
        f.send("cmd:a:caps:$TOK")
        assertEquals(listOf("""cmd:a:{"ok":true,"screenshot":true,"restart_app":true,"restart_device":true}"""), f.out)
    }

    @Test
    fun badTokenOrMalformedIsSilent() {
        val f = Fx()
        f.send("cmd:a:caps:ffffffffffffffffffff")
        f.send("cmd:a:caps:")
        f.send("cmd:a:caps")
        f.send("cmd:a b:caps:$TOK")
        f.send("cmd:a:shot-99999-70:$TOK")
        f.send("cmd:a:shot-10-70:$TOK")
        f.send("cmd:a:shot-800-0:$TOK")
        f.send("cmd:a:shot-800-101:$TOK")
        f.send("cmd:a:shot-800-70-1:$TOK")
        f.send("cmd:a:reboot:$TOK")
        f.send("cmd:a:restart-device;rm:$TOK")
        f.d.handle(null) { f.out += it }
        assertTrue(f.out.isEmpty())
        assertEquals(0, f.act.shots + f.act.app + f.act.device)
    }

    @Test
    fun screenshotReturnsJpegAsBase64AndEnforcesOneAtATime() {
        val f = Fx()
        f.send("cmd:a:shot-1280-70:$TOK")
        f.send("cmd:b:shot-1280-70:$TOK") // en cours
        assertEquals(listOf("""cmd:b:{"ok":false,"code":"busy"}"""), f.out)
        f.act.pending!!(Result.success(byteArrayOf(1, 2, 3)))
        assertEquals("""cmd:a:{"ok":true,"jpeg":"AQID"}""", f.out.last())
        f.send("cmd:c:shot-1280-70:$TOK") // trop tôt
        assertEquals("""cmd:c:{"ok":false,"code":"busy"}""", f.out.last())
        f.now += CmdDispatcher.MIN_SHOT_GAP_MS
        f.send("cmd:d:shot-1280-70:$TOK")
        assertEquals(2, f.act.shots)
    }

    @Test
    fun screenshotFailuresAreCodedNotTechnical() {
        val f = Fx()
        f.send("cmd:a:shot-800-70:$TOK")
        f.act.pending!!(Result.failure(IllegalStateException("secret internal detail")))
        assertEquals("""cmd:a:{"ok":false,"code":"capture_failed"}""", f.out.last())
        f.now += 10_000
        f.send("cmd:b:shot-800-70:$TOK")
        f.act.pending!!(Result.success(ByteArray(CmdDispatcher.MAX_JPEG_BYTES + 1)))
        assertEquals("""cmd:b:{"ok":false,"code":"too_large"}""", f.out.last())
        assertTrue(f.out.none { it.contains("secret") })
    }

    @Test
    fun screenshotUnsupported() {
        val f = Fx(shot = false)
        f.send("cmd:a:shot-800-70:$TOK")
        assertEquals("""cmd:a:{"ok":false,"code":"unsupported"}""", f.out.last())
    }

    @Test
    fun restartAppRepliesFirstThenActsAndIsRateLimited() {
        val f = Fx()
        f.send("cmd:a:restart-app:$TOK")
        assertEquals("""cmd:a:{"ok":true}""", f.out.last())
        assertEquals(0, f.act.app)
        f.sched.run()
        assertEquals(1, f.act.app)
        f.send("cmd:b:restart-app:$TOK")
        assertEquals("""cmd:b:{"ok":false,"code":"busy"}""", f.out.last())
        f.sched.run()
        assertEquals(1, f.act.app)
        f.now += CmdDispatcher.MIN_RESTART_GAP_MS
        f.send("cmd:c:restart-app:$TOK")
        f.sched.run()
        assertEquals(2, f.act.app)
    }

    @Test
    fun restartDeviceRefusedWithoutDeviceOwner() {
        val f = Fx(device = false)
        f.send("cmd:a:restart-device:$TOK")
        assertEquals("""cmd:a:{"ok":false,"code":"unsupported"}""", f.out.last())
        f.sched.run()
        assertEquals(0, f.act.device)
        val g = Fx(device = true)
        g.send("cmd:a:restart-device:$TOK")
        g.sched.run()
        assertEquals(1, g.act.device)
    }

    @Test
    fun routerIgnoresCmdFromSubframeOrUntrustedPage() {
        val f = Fx()
        var trusted = true
        val r = BridgeRouter(trusted = { trusted }, onPing = {}, onReload = {}, cmd = f.d)
        r.onMessage(false, "cmd:a:caps:$TOK") { f.out += it }
        trusted = false
        r.onMessage(true, "cmd:a:caps:$TOK") { f.out += it }
        assertTrue(f.out.isEmpty())
        trusted = true
        r.onMessage(true, "cmd:a:caps:$TOK") { f.out += it }
        assertEquals(1, f.out.size)
    }
}
