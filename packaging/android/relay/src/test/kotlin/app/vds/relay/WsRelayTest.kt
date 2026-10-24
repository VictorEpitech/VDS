package app.vds.relay

import java.io.DataInputStream
import java.io.InputStream
import java.io.OutputStream
import java.net.ServerSocket
import java.net.Socket
import java.net.URI
import java.net.http.HttpClient
import java.net.http.WebSocket
import java.nio.ByteBuffer
import java.util.concurrent.CompletableFuture
import java.util.concurrent.CompletionStage
import java.util.concurrent.LinkedBlockingQueue
import java.util.concurrent.TimeUnit
import kotlin.concurrent.thread
import kotlin.test.AfterTest
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertNull
import kotlin.test.assertTrue

/** Faux amont WebSocket RFC 6455 minimal (socket brut, indépendant du code du relais). */
private class FakeWsUpstream(private val mode: String = "echo") {
    val server = ServerSocket(0, 5, java.net.InetAddress.getByName("127.0.0.1"))
    val port get() = server.localPort
    val requests = LinkedBlockingQueue<Map<String, String>>()
    val received = LinkedBlockingQueue<String>()
    val closeSeen = LinkedBlockingQueue<Int>()

    init { thread(isDaemon = true) { while (!server.isClosed) { val s = try { server.accept() } catch (_: Exception) { break }; thread(isDaemon = true) { serve(s) } } } }

    private fun serve(s: Socket) = s.use {
        val inp = s.getInputStream(); val out = s.getOutputStream()
        val head = StringBuilder()
        while (!head.endsWith("\r\n\r\n")) { val b = inp.read(); if (b < 0) return; head.append(b.toChar()) }
        val lines = head.toString().split("\r\n").filter { it.isNotEmpty() }
        val h = lines.drop(1).associate { it.substringBefore(':').lowercase() to it.substringAfter(':').trim() } + ("request-line" to lines[0])
        requests += h
        if (mode == "refuse") { out.write("HTTP/1.1 401 Unauthorized\r\nContent-Length: 0\r\n\r\n".toByteArray()); return }
        val key = h["sec-websocket-key"] ?: run { out.write("HTTP/1.1 400 Bad Request\r\nContent-Length: 0\r\n\r\n".toByteArray()); return }
        val acc = WsBridge.accept(key)
        val proto = h["sec-websocket-protocol"]?.split(",")?.first()?.trim()
        out.write(("HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Accept: $acc\r\n" +
            (if (proto != null) "Sec-WebSocket-Protocol: $proto\r\n" else "") + "\r\n").toByteArray())
        out.flush()
        val d = DataInputStream(inp)
        while (true) {
            val b0 = d.read(); if (b0 < 0) return
            val op = b0 and 0x0f
            val b1 = d.read(); var len = (b1 and 0x7f).toLong()
            if (len == 126L) len = d.readUnsignedShort().toLong() else if (len == 127L) len = d.readLong()
            val mask = ByteArray(4).also { d.readFully(it) }
            val p = ByteArray(len.toInt()).also { d.readFully(it) }
            for (i in p.indices) p[i] = (p[i].toInt() xor mask[i % 4].toInt()).toByte()
            when (op) {
                1 -> { received += String(p); frame(out, 1, p) ; if (String(p) == "bye") frame(out, 8, byteArrayOf(0x0b, 0xb9.toByte()) + "fin".toByteArray()) }
                2 -> { received += "bin:" + p.size; frame(out, 2, p.reversedArray()) }
                8 -> { closeSeen += if (p.size >= 2) ((p[0].toInt() and 0xff) shl 8) or (p[1].toInt() and 0xff) else 1005; frame(out, 8, p); return }
            }
        }
    }

    private fun frame(out: OutputStream, op: Int, p: ByteArray) {
        out.write(0x80 or op)
        when {
            p.size < 126 -> out.write(p.size)
            p.size < 65536 -> { out.write(126); out.write(p.size shr 8); out.write(p.size and 0xff) }
            else -> { out.write(127); for (i in 7 downTo 0) out.write((p.size.toLong() shr (8 * i)).toInt() and 0xff) }
        }
        out.write(p); out.flush()
    }
}

class WsRelayTest {
    private var relay: Relay? = null
    private var up: FakeWsUpstream? = null
    private fun freePort() = ServerSocket(0).use { it.localPort }

    @AfterTest fun down() { relay?.stop(); up?.server?.close() }

    private fun start(base: String = "", mode: String = "echo", max: Int = WsBridge.MAX_MESSAGE): Int {
        val u = FakeWsUpstream(mode).also { up = it }
        val cfg = RelayConfig.parse("http://127.0.0.1:${u.port}$base", allowInsecureLan = true)!!
        val port = freePort()
        relay = Relay(cfg, port, maxWsMessage = max).also { it.start() }
        return port
    }

    private class Client {
        val events = LinkedBlockingQueue<String>()
        val bins = LinkedBlockingQueue<ByteArray>()
        var ws: WebSocket? = null
        val listener = object : WebSocket.Listener {
            private val sb = StringBuilder()
            override fun onOpen(w: WebSocket) { w.request(1_000) }
            override fun onText(w: WebSocket, d: CharSequence, last: Boolean): CompletionStage<*>? {
                sb.append(d); if (last) { events += "text:$sb"; sb.setLength(0) }; return null
            }
            override fun onBinary(w: WebSocket, d: ByteBuffer, last: Boolean): CompletionStage<*>? {
                bins += ByteArray(d.remaining()).also { d.get(it) }; return null
            }
            override fun onClose(w: WebSocket, code: Int, reason: String): CompletionStage<*>? { events += "close:$code:$reason"; return CompletableFuture.completedFuture(null) }
            override fun onError(w: WebSocket, e: Throwable) { events += "error" }
        }
        fun connect(port: Int, path: String, vararg headers: Pair<String, String>, proto: String? = null): CompletableFuture<WebSocket> {
            val b = HttpClient.newHttpClient().newWebSocketBuilder()
            for ((k, v) in headers) b.header(k, v)
            if (proto != null) b.subprotocols(proto)
            return b.buildAsync(URI("ws://127.0.0.1:$port$path"), listener).also { f -> f.thenAccept { ws = it } }
        }
        fun poll(): String? = events.poll(5, TimeUnit.SECONDS)
    }

    @Test fun `texte et binaire dans les deux sens, meme chemin et Authorization, sans Origin`() {
        val port = start("/espace/affichage")
        val c = Client()
        val ws = c.connect(port, "/espace/affichage/ws/player?x=1", "Authorization" to "Bearer t0k", "Origin" to "http://127.0.0.1:$port").get(5, TimeUnit.SECONDS)
        val seen = up!!.requests.poll(5, TimeUnit.SECONDS)!!
        assertTrue(seen["request-line"]!!.startsWith("GET /espace/affichage/ws/player?x=1 "))
        assertEquals("Bearer t0k", seen["authorization"])
        assertNull(seen["origin"]); assertNull(seen["cookie"])
        ws.sendText("héllo", true).get(5, TimeUnit.SECONDS)
        assertEquals("text:héllo", c.poll())
        ws.sendBinary(ByteBuffer.wrap(byteArrayOf(1, 2, 3)), true).get(5, TimeUnit.SECONDS)
        assertEquals(listOf<Byte>(3, 2, 1), c.bins.poll(5, TimeUnit.SECONDS)!!.toList())
        ws.sendClose(1000, "ok").get(5, TimeUnit.SECONDS)
        assertEquals("close:1000:ok", c.poll())
        assertEquals(1000, up!!.closeSeen.poll(5, TimeUnit.SECONDS))
    }

    @Test fun `sous-protocole transmis dans les deux sens`() {
        val port = start()
        val c = Client()
        val ws = c.connect(port, "/ws/player", proto = "vds.v1").get(5, TimeUnit.SECONDS)
        assertEquals("vds.v1", ws.subprotocol)
        assertEquals("vds.v1", up!!.requests.poll(5, TimeUnit.SECONDS)!!["sec-websocket-protocol"])
    }

    @Test fun `fermeture amont propagee avec son code`() {
        val port = start()
        val c = Client()
        val ws = c.connect(port, "/ws/player").get(5, TimeUnit.SECONDS)
        ws.sendText("bye", true).get(5, TimeUnit.SECONDS)
        assertEquals("text:bye", c.poll())
        assertEquals("close:3001:fin", c.poll())
    }

    @Test fun `refus amont 401 renvoye au client`() {
        val port = start(mode = "refuse")
        val f = Client().connect(port, "/ws/player")
        val e = runCatching { f.get(5, TimeUnit.SECONDS) }.exceptionOrNull()
        assertEquals(401, (e?.cause as java.net.http.WebSocketHandshakeException).response.statusCode())
    }

    @Test fun `chemin hors ws player et Origin etranger refuses`() {
        val port = start()
        for (path in listOf("/ws/other", "/api/x")) {
            assertTrue(runCatching { Client().connect(port, path).get(5, TimeUnit.SECONDS) }.isFailure, path)
        }
        assertTrue(runCatching { Client().connect(port, "/ws/player", "Origin" to "http://evil.example").get(5, TimeUnit.SECONDS) }.isFailure)
        assertTrue(up!!.requests.isEmpty())
    }

    @Test fun `trame trop grosse du client ferme en 1009`() {
        val port = start(max = 1000)
        val c = Client()
        val ws = c.connect(port, "/ws/player").get(5, TimeUnit.SECONDS)
        ws.sendText("a".repeat(5000), true)
        val ev = c.poll()
        assertTrue(ev!!.startsWith("close:1009") || ev == "error", "got $ev")
        assertTrue(up!!.received.isEmpty())
    }

    @Test fun `HTTP ordinaire toujours relaye a cote`() {
        val port = start()
        val r = java.net.http.HttpClient.newHttpClient().send(
            java.net.http.HttpRequest.newBuilder(URI("http://127.0.0.1:$port/ws/player")).GET().build(),
            java.net.http.HttpResponse.BodyHandlers.ofString())
        assertEquals(400, r.statusCode()) // GET sans Upgrade : relayé comme HTTP (le faux amont répond 400)
    }
}
