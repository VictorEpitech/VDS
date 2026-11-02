package app.vds.relay

import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertFalse
import kotlin.test.assertTrue

class WebDispatcherTest {
    private class FakeHost : WebHost {
        val views = mutableSetOf<String>()
        val calls = mutableListOf<String>()
        var result: Pair<Boolean, String?> = true to null
        override fun count() = views.size
        override fun has(id: String) = id in views
        override fun prepare(id: String, url: String, rect: WebRect, done: (Boolean, String?) -> Unit) {
            views += id
            calls += "prepare:$id:$url:$rect"
            done(result.first, result.second)
        }
        override fun show(id: String) { calls += "show:$id" }
        override fun rect(id: String, rect: WebRect) { calls += "rect:$id:$rect" }
        override fun hide(id: String) { views -= id; calls += "hide:$id" }
    }

    private class Fixture(var trusted: Boolean = true) {
        val host = FakeHost()
        val replies = mutableListOf<String>()
        val router = BridgeRouter(trusted = { trusted }, onPing = {}, onReload = {}, web = WebDispatcher(host))
        fun send(main: Boolean, m: String) = router.onMessage(main, m) { replies += it }
    }

    private val prep = """web:a1:prepare:v1:{"url":"https:\/\/www.epitech.eu\/","rect":{"x":10,"y":20,"w":300,"h":200}}"""

    @Test
    fun mainFrameTrustedPrepareShowRectHide() {
        val f = Fixture()
        f.send(true, prep)
        f.send(true, "web:0:show:v1")
        f.send(true, """web:0:rect:v1:{"x":1,"y":2,"w":3,"h":4}""")
        f.send(true, "web:0:hide:v1")
        assertEquals(listOf("""web:a1:{"ok":true}"""), f.replies)
        assertEquals(
            listOf("prepare:v1:https://www.epitech.eu/:WebRect(x=10, y=20, w=300, h=200)", "show:v1", "rect:v1:WebRect(x=1, y=2, w=3, h=4)", "hide:v1"),
            f.host.calls,
        )
    }

    @Test
    fun refusedOutsideTrustedMainFrame() {
        val f = Fixture()
        for (m in listOf(prep, "web:0:show:v1", "web:0:hide:v1")) f.send(false, m)
        f.trusted = false
        for (m in listOf(prep, "web:0:show:v1", "web:0:hide:v1")) f.send(true, m)
        assertTrue(f.replies.isEmpty() && f.host.calls.isEmpty())
    }

    @Test
    fun invalidIdAndMalformedAreIgnored() {
        val f = Fixture()
        f.send(true, prep.replace(":v1:", ":v 1:"))
        f.send(true, prep.replace(":v1:", ":${"x".repeat(33)}:"))
        f.send(true, "web:a1:prepare:v1:{}")
        f.send(true, "web:a1:bogus:v1")
        assertTrue(f.host.calls.isEmpty())
    }

    @Test
    fun atMostTwoViews() {
        val f = Fixture()
        f.send(true, prep)
        f.send(true, prep.replace(":v1:", ":v2:"))
        f.send(true, prep.replace(":v1:", ":v3:").replace("a1", "a3"))
        assertEquals("""web:a3:{"ok":false,"code":"url_refused"}""", f.replies.last())
        assertEquals(setOf("v1", "v2"), f.host.views)
        f.send(true, prep.replace("a1", "a4")) // recharge d'une vue existante
        assertEquals("""web:a4:{"ok":true}""", f.replies.last())
    }

    @Test
    fun forbiddenUrlsAreRefusedWithoutReachingHost() {
        val f = Fixture()
        for (u in listOf("http://example.com/", "intent://x#Intent;end", "file:///etc/passwd", "content://x", "https://127.0.0.1:8080/player/")) {
            f.send(true, """web:r:prepare:v1:{"url":"$u","rect":{"x":0,"y":0,"w":1,"h":1}}""")
        }
        assertEquals(5, f.replies.size)
        assertTrue(f.replies.all { it == """web:r:{"ok":false,"code":"url_refused"}""" })
        assertTrue(f.host.calls.isEmpty())
    }

    @Test
    fun nativeFailureCodeIsForwarded() {
        val f = Fixture()
        f.host.result = false to "url_timeout"
        f.send(true, prep)
        assertEquals("""web:a1:{"ok":false,"code":"url_timeout"}""", f.replies.single())
    }

    @Test
    fun urlFilter() {
        assertTrue(WebUrlFilter.allowed("https://www.epitech.eu/a?b=c"))
        assertTrue(WebUrlFilter.allowed("HTTPS://example.com"))
        for (u in listOf(null, "", "http://example.com", "https://localhost/", "https://a.localhost/", "https://[::1]/", "https://127.0.0.1/", "https://2130706433/", "https://0x7f.1/", "https://[::ffff:127.0.0.1]/", "https://10.1.2.3/",
            "https://192.168.1.1/", "https://169.254.169.254/", "https://[fd00::1]/", "https://[fe80::1]/",
            "https://u:p@example.com/", "intent:#Intent;end", "javascript:alert(1)", "https:///x", "https://" + "a".repeat(2100))) {
            assertFalse(WebUrlFilter.allowed(u), "doit être refusée : $u")
        }
    }
}
