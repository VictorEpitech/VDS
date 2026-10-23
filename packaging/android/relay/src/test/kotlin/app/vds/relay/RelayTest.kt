package app.vds.relay

import com.sun.net.httpserver.HttpExchange
import com.sun.net.httpserver.HttpServer
import java.net.InetSocketAddress
import java.net.ServerSocket
import java.net.URI
import java.net.http.HttpClient
import java.net.http.HttpRequest
import java.net.http.HttpResponse
import kotlin.test.AfterTest
import kotlin.test.BeforeTest
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertFailsWith
import kotlin.test.assertNotNull
import kotlin.test.assertNull
import kotlin.test.assertTrue

class RelayTest {
    private val big = ByteArray(300_000) { (it * 7).toByte() }
    private lateinit var upstream: HttpServer
    private val seen = mutableListOf<Map<String, String>>()
    private var lastBody = ByteArray(0)
    private var relay: Relay? = null
    private val http = HttpClient.newHttpClient()

    private fun freePort() = ServerSocket(0).use { it.localPort }

    @BeforeTest
    fun up() {
        upstream = HttpServer.create(InetSocketAddress("127.0.0.1", 0), 0)
        upstream.createContext("/") { ex -> route(ex) }
        upstream.start()
    }

    @AfterTest
    fun down() {
        relay?.stop()
        upstream.stop(0)
    }

    private fun route(ex: HttpExchange) {
        seen += ex.requestHeaders.entries.associate { it.key.lowercase() to it.value.joinToString(",") } +
            ("method" to ex.requestMethod) + ("path" to ex.requestURI.toString())
        lastBody = ex.requestBody.readBytes()
        val path = ex.requestURI.path
        when {
            path.endsWith("/media/big") -> {
                val range = ex.requestHeaders.getFirst("Range")
                ex.responseHeaders.add("Content-Type", "video/webm")
                ex.responseHeaders.add("Accept-Ranges", "bytes")
                ex.responseHeaders.add("Set-Cookie", "a=b")
                if (range != null) {
                    val (s, e) = range.removePrefix("bytes=").split("-").map { it.toInt() }
                    ex.responseHeaders.add("Content-Range", "bytes $s-$e/${big.size}")
                    ex.sendResponseHeaders(206, (e - s + 1).toLong())
                    ex.responseBody.use { it.write(big, s, e - s + 1) }
                } else {
                    ex.sendResponseHeaders(200, if (ex.requestMethod == "HEAD") -1 else big.size.toLong())
                    if (ex.requestMethod != "HEAD") ex.responseBody.use { it.write(big) }
                }
            }
            path.endsWith("/etag") -> {
                ex.responseHeaders.add("ETag", "\"v1\"")
                if (ex.requestHeaders.getFirst("If-None-Match") == "\"v1\"") {
                    ex.sendResponseHeaders(304, -1)
                } else {
                    ex.responseHeaders.add("Content-Type", "application/json")
                    val b = "{\"ok\":true}".toByteArray()
                    ex.sendResponseHeaders(200, b.size.toLong())
                    ex.responseBody.use { it.write(b) }
                }
            }
            path.endsWith("/redirect") -> {
                ex.responseHeaders.add("Location", "http://127.0.0.1:${upstream.address.port}/x/y")
                ex.sendResponseHeaders(302, -1)
            }
            else -> {
                val b: ByteArray = if (lastBody.isEmpty()) "hello".toByteArray() else lastBody
                ex.responseHeaders.add("Content-Type", "application/json")
                ex.responseHeaders.add("Content-Security-Policy", "default-src 'self'")
                ex.sendResponseHeaders(201, b.size.toLong())
                ex.responseBody.use { it.write(b) }
            }
        }
        ex.close()
    }

    private fun start(base: String = ""): Pair<Relay, String> {
        val cfg = assertNotNull(RelayConfig.parse("http://127.0.0.1:${upstream.address.port}$base"))
        val port = freePort()
        val r = Relay(cfg, port, bindDelayMs = 10)
        r.start()
        relay = r
        return r to "http://127.0.0.1:$port"
    }

    private fun req(url: String, method: String = "GET", body: ByteArray? = null, vararg h: Pair<String, String>): HttpResponse<ByteArray> {
        val b = HttpRequest.newBuilder(URI(url))
        h.forEach { (k, v) -> b.header(k, v) }
        b.method(method, if (body == null) HttpRequest.BodyPublishers.noBody() else HttpRequest.BodyPublishers.ofByteArray(body))
        return http.send(b.build(), HttpResponse.BodyHandlers.ofByteArray())
    }

    @Test
    fun configParsing() {
        assertEquals("/espace/affichage", RelayConfig.parse("https://allia-innovation.fr/espace/affichage/")!!.basePath)
        assertEquals("", RelayConfig.parse("https://vds.client.fr")!!.basePath)
        assertNull(RelayConfig.parse("http://h:8080"))
        assertEquals("http://h:8080", RelayConfig.parse("http://h:8080", allowInsecureLan = true)!!.upstreamOrigin)
        assertEquals(true, RelayConfig.parse("http://192.168.1.5", allowInsecureLan = true)!!.insecureUpstream)
        assertEquals(false, RelayConfig.parse("https://h", allowInsecureLan = true)!!.insecureUpstream)
        assertEquals(false, RelayConfig.parse("http://127.0.0.1:9")!!.insecureUpstream)
        assertNull(RelayConfig.parse("ftp://x"))
        assertNull(RelayConfig.parse("https://u:p@x"))
        assertNull(RelayConfig.parse("https://x/a/../b"))
        assertNull(RelayConfig.parse("https://x?q=1"))
        assertNull(RelayConfig.parse(""))
        val c = RelayConfig.parse("https://x/espace/affichage")!!
        assertEquals("http://127.0.0.1:47800/espace/affichage/player/", c.playerUrl(47800))
        assertTrue(c.inScope("/espace/affichage/api") && c.inScope("/espace/affichage"))
        assertTrue(!c.inScope("/espace/affichage2") && !c.inScope("/"))
    }

    @Test
    fun rangeIs206AndStreamsExactBytes() {
        val (_, local) = start()
        val r = req("$local/media/big", "GET", null, "Range" to "bytes=100-199")
        assertEquals(206, r.statusCode())
        assertEquals("bytes 100-199/${big.size}", r.headers().firstValue("content-range").get())
        assertTrue(big.copyOfRange(100, 200).contentEquals(r.body()))
        val full = req("$local/media/big")
        assertEquals(200, full.statusCode())
        assertTrue(big.contentEquals(full.body()))
        assertTrue(full.headers().firstValue("set-cookie").isEmpty)
        assertEquals("video/webm", full.headers().firstValue("content-type").get())
    }

    @Test
    fun etagAnd304() {
        val (_, local) = start()
        val first = req("$local/etag")
        assertEquals("\"v1\"", first.headers().firstValue("etag").get())
        val second = req("$local/etag", "GET", null, "If-None-Match" to "\"v1\"")
        assertEquals(304, second.statusCode())
        assertEquals(0, second.body().size)
    }

    @Test
    fun postBodyIntactAndNoCookieOrigin() {
        val (_, local) = start()
        val payload = """{"device":{"platform":"android"}}""".toByteArray()
        val r = req(
            "$local/api/v1/player/pairings", "POST", payload,
            "Content-Type" to "application/json", "Cookie" to "s=1", "Origin" to "http://127.0.0.1",
            "Referer" to "http://127.0.0.1/x", "Authorization" to "Bearer vds_dev_x",
        )
        assertEquals(201, r.statusCode())
        assertTrue(payload.contentEquals(r.body()))
        assertTrue(payload.contentEquals(lastBody))
        val h = seen.last()
        assertEquals("POST", h["method"])
        assertNull(h["cookie"]); assertNull(h["origin"]); assertNull(h["referer"])
        assertEquals("Bearer vds_dev_x", h["authorization"])
        assertEquals("application/json", h["content-type"])
        assertEquals("default-src 'self'", r.headers().firstValue("content-security-policy").get())
        assertEquals("127.0.0.1:${upstream.address.port}", h["host"])
    }

    @Test
    fun largeStreamedPostIsIntact() {
        val (_, local) = start()
        val r = req("$local/api/up", "POST", big)
        assertTrue(big.contentEquals(lastBody))
        assertTrue(big.contentEquals(r.body()))
    }

    @Test
    fun baseScopeAndTraversal() {
        val (_, local) = start("/espace/affichage")
        assertEquals(201, req("$local/espace/affichage/api/x").statusCode())
        assertEquals("/espace/affichage/api/x?a=1", run { req("$local/espace/affichage/api/x?a=1"); seen.last()["path"] })
        assertEquals(404, req("$local/api/x").statusCode())
        assertEquals(404, req("$local/espace/affichage2/x").statusCode())
        assertEquals(404, req("$local/espace/affichage/%2e%2e/secret").statusCode())
    }

    @Test
    fun redirectLocationRewrittenToLoopback() {
        val (_, local) = start()
        val r = req("$local/redirect")
        assertEquals(302, r.statusCode())
        assertEquals("$local/x/y", r.headers().firstValue("location").get())
    }

    @Test
    fun unreachableUpstreamIs502() {
        val dead = freePort()
        val port = freePort()
        val r = Relay(RelayConfig.parse("http://127.0.0.1:$dead")!!, port, bindDelayMs = 10)
        r.start(); relay = r
        val resp = req("http://127.0.0.1:$port/api/x")
        assertEquals(502, resp.statusCode())
        assertEquals(0, resp.body().size)
    }

    @Test
    fun occupiedPortRaisesAfterRetries() {
        ServerSocket(0, 1, java.net.InetAddress.getByName("127.0.0.1")).use { busy ->
            val r = Relay(RelayConfig.parse("http://127.0.0.1:1")!!, busy.localPort, bindAttempts = 3, bindDelayMs = 10)
            val e = assertFailsWith<RelayPortException> { r.start() }
            assertEquals(busy.localPort, e.port)
        }
    }

    @Test
    fun purgePageIsLocal() {
        val (_, local) = start("/espace/affichage")
        val before = seen.size
        val r = req("$local${Relay.PURGE_PATH}")
        assertEquals(200, r.statusCode())
        assertTrue(String(r.body()).contains("/espace/affichage/player/"))
        assertEquals(before, seen.size)
    }
}
