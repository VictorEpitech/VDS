package app.vds.relay

import io.ktor.http.HttpStatusCode
import io.ktor.http.content.OutgoingContent
import io.ktor.server.application.ApplicationCall
import io.ktor.server.response.respond
import io.ktor.util.cio.KtorDefaultPool
import io.ktor.utils.io.ByteReadChannel
import io.ktor.utils.io.ByteWriteChannel
import io.ktor.websocket.CloseReason
import io.ktor.websocket.Frame
import io.ktor.websocket.FrameTooBigException
import io.ktor.websocket.RawWebSocket
import io.ktor.websocket.readReason
import io.ktor.websocket.readText
import kotlinx.coroutines.cancel
import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.channels.Channel
import kotlinx.coroutines.launch
import kotlinx.coroutines.withTimeoutOrNull
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.Response
import okhttp3.WebSocket
import okhttp3.WebSocketListener
import okio.ByteString
import okio.ByteString.Companion.toByteString
import java.security.MessageDigest
import java.util.Base64
import java.util.concurrent.atomic.AtomicBoolean
import java.util.concurrent.atomic.AtomicInteger
import kotlin.coroutines.CoroutineContext

/**
 * Pont WebSocket bidirectionnel : WebView (boucle) <-> serveur amont. `ktor-server-websockets` n'étant pas
 * disponible hors ligne, la montée de protocole est faite à la main (`OutgoingContent.ProtocolUpgrade` de
 * Ktor CIO + `RawWebSocket` de `ktor-websockets` pour la trame) et l'amont est un `WebSocket` OkHttp.
 * L'amont est ouvert AVANT de répondre 101 : un refus amont (401, 403...) revient tel quel au client.
 */
internal class WsBridge(
    private val client: OkHttpClient,
    private val maxMessage: Int,
    private val maxConnections: Int,
    private val log: (String) -> Unit,
) {
    private val active = AtomicInteger(0)
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.IO)

    fun close() = scope.coroutineContext[Job]?.cancel()

    suspend fun handle(call: ApplicationCall, upstream: Request.Builder, subprotocols: String?) {
        try { handle0(call, upstream, subprotocols) } catch (e: kotlinx.coroutines.CancellationException) { throw e } catch (e: Exception) { log("ws bridge error: " + e); throw e }
    }
    private suspend fun handle0(call: ApplicationCall, upstream: Request.Builder, subprotocols: String?) {
        val key = call.request.headers["Sec-WebSocket-Key"]
        if (key.isNullOrBlank() || call.request.headers["Sec-WebSocket-Version"] != "13") {
            call.respond(HttpStatusCode.BadRequest); return
        }
        if (active.incrementAndGet() > maxConnections) {
            active.decrementAndGet()
            call.respond(HttpStatusCode.ServiceUnavailable); return
        }
        var handedOver = false
        try {
            if (subprotocols != null) upstream.header("Sec-WebSocket-Protocol", subprotocols)
            val opened = CompletableDeferred<Response>()
            val pump = Channel<Frame>(PUMP_CAPACITY)
            val fromUpstream = Channel<Unit>(Channel.CONFLATED)
            val state = Upstream(pump, maxMessage, log)
            val ws = client.newWebSocket(upstream.build(), object : WebSocketListener() {
                override fun onOpen(webSocket: WebSocket, response: Response) { opened.complete(response) }
                override fun onMessage(webSocket: WebSocket, text: String) = state.text(webSocket, text)
                override fun onMessage(webSocket: WebSocket, bytes: ByteString) = state.binary(webSocket, bytes)
                override fun onClosing(webSocket: WebSocket, code: Int, reason: String) = state.closing(webSocket, code, reason)
                override fun onClosed(webSocket: WebSocket, code: Int, reason: String) = state.closing(webSocket, code, reason)
                override fun onFailure(webSocket: WebSocket, t: Throwable, response: Response?) {
                    if (!opened.isCompleted) opened.completeExceptionally(UpstreamRefused(response?.code))
                    else state.failed()
                }
            })
            val resp = try {
                opened.await()
            } catch (e: UpstreamRefused) {
                ws.cancel()
                val code = e.status
                if (code != null && code in 400..599) call.respond(HttpStatusCode(code, "-"))
                else call.respond(HttpStatusCode.BadGateway)
                return
            }
            val chosen = resp.header("Sec-WebSocket-Protocol")
            val upgradeHeaders = io.ktor.http.HeadersBuilder().apply {
                append("Upgrade", "websocket")
                append("Connection", "Upgrade")
                append("Sec-WebSocket-Accept", accept(key))
                if (chosen != null) append("Sec-WebSocket-Protocol", chosen)
            }.build()
            handedOver = true
            call.respond(object : OutgoingContent.ProtocolUpgrade() {
                override val headers = upgradeHeaders
                override suspend fun upgrade(
                    input: ByteReadChannel,
                    output: ByteWriteChannel,
                    engineContext: CoroutineContext,
                    userContext: CoroutineContext,
                ): Job = scope.launch {
                    try {
                        run(input, output, ws, pump, userContext)
                    } finally {
                        ws.cancel()
                        pump.close()
                        active.decrementAndGet()
                    }
                }
            })
        } finally {
            if (!handedOver) active.decrementAndGet()
        }
    }

    private suspend fun run(
        input: ByteReadChannel,
        output: ByteWriteChannel,
        ws: WebSocket,
        pump: Channel<Frame>,
        userContext: CoroutineContext,
    ) {
        val raw = RawWebSocket(input, output, maxMessage.toLong(), false, userContext + Job())
        val toClient = scope.launch {
            // Une seule écriture à la fois, dans l'ordre amont ; Close = dernière trame.
            for (f in pump) {
                raw.outgoing.send(f)
                if (f is Frame.Close) break
            }
        }
        val closeSent = AtomicBoolean(false)
        try {
            for (f in raw.incoming) {
                when (f) {
                    is Frame.Text -> if (!ws.send(f.readText())) break
                    is Frame.Binary -> if (!ws.send(f.data.toByteString())) break
                    is Frame.Ping -> pump.trySend(Frame.Pong(f.data))
                    is Frame.Pong -> Unit
                    is Frame.Close -> {
                        val r = f.readReason()
                        val code = r?.code?.toInt()?.takeIf { validUpstreamCode(it) } ?: 1000
                        ws.close(code, r?.message?.take(120) ?: "")
                        closeSent.set(true)
                        break
                    }
                }
            }
        } catch (_: FrameTooBigException) {
            pump.trySend(Frame.Close(CloseReason(CloseReason.Codes.TOO_BIG, "")))
            ws.close(1009, "")
        } catch (_: Exception) {
            ws.close(1001, "")
        }
        // Laisse l'amont répondre à la fermeture, puis la propage ; sinon coupe.
        withTimeoutOrNull(CLOSE_GRACE_MS) { toClient.join() }
        toClient.cancel()
        raw.cancel()
    }

    private class UpstreamRefused(val status: Int?) : Exception()

    private class Upstream(val pump: Channel<Frame>, val max: Int, val log: (String) -> Unit) {
        private fun offer(ws: WebSocket, f: Frame) {
            if (pump.trySend(f).isFailure) { // client trop lent : on coupe plutôt que de bufferiser
                log("ws client too slow")
                pump.trySend(Frame.Close(CloseReason(CloseReason.Codes.TRY_AGAIN_LATER, "")))
                ws.close(1013, "")
                pump.close()
            }
        }
        fun text(ws: WebSocket, s: String) {
            if (s.length > max) tooBig(ws) else offer(ws, Frame.Text(s))
        }
        fun binary(ws: WebSocket, b: ByteString) {
            if (b.size > max) tooBig(ws) else offer(ws, Frame.Binary(true, b.toByteArray()))
        }
        private fun tooBig(ws: WebSocket) {
            pump.trySend(Frame.Close(CloseReason(CloseReason.Codes.TOO_BIG, "")))
            ws.close(1009, "")
        }
        fun closing(ws: WebSocket, code: Int, reason: String) {
            ws.close(code.takeIf { validUpstreamCode(it) } ?: 1000, reason)
            pump.trySend(Frame.Close(CloseReason(code.toShort(), reason)))
        }
        fun failed() {
            pump.trySend(Frame.Close(CloseReason(CloseReason.Codes.INTERNAL_ERROR, "")))
        }
    }

    companion object {
        const val MAX_MESSAGE = 1 shl 20
        const val MAX_CONNECTIONS = 4
        private const val PUMP_CAPACITY = 256
        private const val CLOSE_GRACE_MS = 5_000L
        private const val GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"

        internal fun accept(key: String): String =
            Base64.getEncoder().encodeToString(MessageDigest.getInstance("SHA-1").digest((key.trim() + GUID).toByteArray()))

        /** Codes qu'une trame Close peut porter (RFC 6455 §7.4) ; 1005/1006/1015 sont réservés. */
        internal fun validUpstreamCode(c: Int) = c in 1000..1003 || c in 1007..1014 || c in 3000..4999
    }
}
