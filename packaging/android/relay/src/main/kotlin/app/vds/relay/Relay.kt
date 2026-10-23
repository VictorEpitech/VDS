package app.vds.relay

import io.ktor.http.ContentType
import io.ktor.http.HttpMethod
import io.ktor.http.HttpStatusCode
import io.ktor.http.content.OutgoingContent
import io.ktor.http.withCharset
import io.ktor.server.application.ApplicationCall
import io.ktor.server.application.ApplicationCallPipeline
import io.ktor.server.application.call
import io.ktor.server.cio.CIO
import io.ktor.server.engine.ApplicationEngine
import io.ktor.server.engine.embeddedServer
import io.ktor.server.request.httpMethod
import io.ktor.server.request.receiveChannel
import io.ktor.server.request.uri
import io.ktor.server.response.respond
import io.ktor.utils.io.ByteWriteChannel
import io.ktor.utils.io.jvm.javaio.toInputStream
import io.ktor.utils.io.writeFully
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import okhttp3.HttpUrl.Companion.toHttpUrlOrNull
import okhttp3.MediaType.Companion.toMediaTypeOrNull
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody
import okhttp3.Response
import okio.BufferedSink
import okio.source
import java.io.IOException
import java.net.InetAddress
import java.net.ServerSocket
import java.util.concurrent.TimeUnit

/** Port de boucle occupé après toutes les tentatives : l'app affiche l'écran natif `E_RELAY_PORT`. */
class RelayPortException(val port: Int, cause: Throwable? = null) : IOException("relay port $port unavailable", cause)

/**
 * Relais HTTP en boucle (127.0.0.1) vers le serveur VDS. Voir `docs/architecture.md` ›
 * « Lecture hors ligne par plateforme ». Fidèle et sans état : méthodes, corps, `Range`/`206`,
 * `ETag`/`304` intacts, flux sans mise en mémoire. Ne transmet ni cookie, ni `Origin`/`Referer`/`Sec-Fetch-*` ;
 * n'ajoute aucun jeton. Ne relaie que `<base>` et ses sous-chemins. Erreur amont : 502 sans détail.
 * (WebSocket `<base>/ws/player` : lot 3, la bibliothèque Ktor CIO le permet.)
 */
class Relay(
    private val config: RelayConfig,
    val port: Int = DEFAULT_PORT,
    private val client: OkHttpClient = defaultClient(),
    private val bindAttempts: Int = 3,
    private val bindDelayMs: Long = 500,
    private val log: (String) -> Unit = {},
) {
    private var server: ApplicationEngine? = null

    val running: Boolean get() = server != null

    /** Bloquant (jusqu'à `bindAttempts` essais). Lève [RelayPortException] si le port reste occupé. */
    @Synchronized
    fun start() {
        if (server != null) return
        var last: Throwable? = null
        repeat(bindAttempts) { i ->
            try {
                // Contrôle préalable : CIO signale mal un port occupé au démarrage asynchrone.
                ServerSocket().use { s ->
                    s.reuseAddress = true
                    s.bind(java.net.InetSocketAddress(InetAddress.getByName(RelayConfig.LOOPBACK), port))
                }
                val engine = embeddedServer(CIO, port = port, host = RelayConfig.LOOPBACK) {
                    intercept(ApplicationCallPipeline.Call) { handle(call) }
                }
                engine.start(wait = false)
                server = engine
                return
            } catch (e: Exception) {
                last = e
                log("bind attempt ${i + 1} failed")
                if (i < bindAttempts - 1) Thread.sleep(bindDelayMs)
            }
        }
        throw RelayPortException(port, last)
    }

    @Synchronized
    fun stop() {
        server?.stop(200, 1000)
        server = null
    }

    private suspend fun handle(call: ApplicationCall) {
        val uri = call.request.uri
        val path = uri.substringBefore('?')
        if (path == PURGE_PATH && call.request.httpMethod == HttpMethod.Get) {
            call.respond(purgePage())
            return
        }
        if (!config.inScope(path) || hasDotSegments(path)) {
            call.respond(HttpStatusCode.NotFound)
            return
        }
        val url = (config.upstreamOrigin + uri).toHttpUrlOrNull()
        if (url == null) {
            call.respond(HttpStatusCode.BadRequest)
            return
        }
        val method = call.request.httpMethod.value
        val headers = call.request.headers
        val builder = Request.Builder().url(url)
        for (name in headers.names()) {
            if (name.lowercase() in DROP_REQUEST || name.lowercase().startsWith("sec-fetch-") ||
                name.lowercase().startsWith("proxy-")
            ) continue
            for (v in headers.getAll(name).orEmpty()) builder.addHeader(name, v)
        }
        // Sans cet en-tête OkHttp décompresserait à la volée et fausserait Content-Length / Range.
        if (headers["Accept-Encoding"] == null) builder.header("Accept-Encoding", "identity")

        val length = headers["Content-Length"]?.toLongOrNull()
        val chunked = headers["Transfer-Encoding"]?.contains("chunked", ignoreCase = true) == true
        val hasBody = method !in NO_BODY_METHODS && (chunked || (length ?: 0) > 0 || method in BODY_REQUIRED)
        val body: RequestBody? = if (!hasBody) null else {
            val input = if ((length ?: 0) > 0 || chunked) call.receiveChannel().toInputStream() else null
            val mediaType = headers["Content-Type"]?.toMediaTypeOrNull()
            object : RequestBody() {
                override fun contentType() = mediaType
                override fun contentLength(): Long = if (input == null) 0 else (length ?: -1)
                override fun isOneShot() = true
                override fun writeTo(sink: BufferedSink) {
                    input?.use { sink.writeAll(it.source()) }
                }
            }
        }
        builder.method(method, body)

        val response: Response = try {
            withContext(Dispatchers.IO) { client.newCall(builder.build()).execute() }
        } catch (e: CancellationException) {
            throw e
        } catch (e: Exception) {
            log("upstream error: ${e.javaClass.simpleName}")
            call.respond(HttpStatusCode.BadGateway)
            return
        }
        response.use { relayResponse(call, it, method) }
    }

    private suspend fun relayResponse(call: ApplicationCall, resp: Response, method: String) {
        val localOrigin = config.localOrigin(port)
        for ((name, value) in resp.headers) {
            val n = name.lowercase()
            if (n in DROP_RESPONSE || n.startsWith("proxy-")) continue
            val v = if (n == "location" && value.startsWith(config.upstreamOrigin)) {
                localOrigin + value.removePrefix(config.upstreamOrigin)
            } else value
            call.response.headers.append(name, v, safeOnly = false)
        }
        val code = resp.code
        val len = resp.header("Content-Length")?.toLongOrNull()
        val type = resp.header("Content-Type")?.let { runCatching { ContentType.parse(it) }.getOrNull() }
        val status = HttpStatusCode(code, resp.message.ifEmpty { "-" })
        val noBody = method == "HEAD" || code in 100..199 || code == 204 || code == 205 || code == 304
        if (noBody) {
            call.respond(object : OutgoingContent.NoContent() {
                override val status = status
                override val contentType = type
                override val contentLength = if (code == 204 || code == 304) null else len
            })
            return
        }
        val source = resp.body ?: return call.respond(status)
        call.respond(object : OutgoingContent.WriteChannelContent() {
            override val status = status
            override val contentType = type
            override val contentLength = len
            override suspend fun writeTo(channel: ByteWriteChannel) {
                withContext(Dispatchers.IO) {
                    val buf = ByteArray(BUFFER)
                    source.byteStream().use { input ->
                        while (true) {
                            val n = input.read(buf)
                            if (n < 0) break
                            channel.writeFully(buf, 0, n)
                        }
                    }
                }
            }
        })
    }

    /**
     * Page locale (jamais relayée) qui efface Service Worker, caches, IndexedDB et stockage de l'origine
     * puis renvoie vers le player. Chargée par l'app quand l'URL serveur change.
     */
    private fun purgePage(): OutgoingContent {
        val target = "${config.basePath}/player/"
        val html = """<!doctype html><meta charset="utf-8"><body style="background:#000"><script>
(async()=>{try{
for(const r of await navigator.serviceWorker.getRegistrations())await r.unregister();
for(const k of await caches.keys())await caches.delete(k);
for(const d of await indexedDB.databases())indexedDB.deleteDatabase(d.name);
localStorage.clear();sessionStorage.clear();
}catch(e){}
location.replace(${jsString(target)});})();
</script>"""
        return object : OutgoingContent.ByteArrayContent() {
            private val bytes = html.toByteArray()
            override val contentType = ContentType.Text.Html.withCharset(Charsets.UTF_8)
            override val contentLength = bytes.size.toLong()
            override fun bytes() = bytes
        }
    }

    companion object {
        const val DEFAULT_PORT = 47800
        const val PURGE_PATH = "/__vds/purge"
        private const val BUFFER = 64 * 1024
        private val NO_BODY_METHODS = setOf("GET", "HEAD")
        private val BODY_REQUIRED = setOf("POST", "PUT", "PATCH")
        private val DROP_REQUEST = setOf(
            "host", "connection", "keep-alive", "te", "trailer", "transfer-encoding", "upgrade",
            "content-length", "content-type", "expect",
            "cookie", "origin", "referer",
        )
        private val DROP_RESPONSE = setOf(
            "connection", "keep-alive", "te", "trailer", "transfer-encoding", "upgrade",
            "content-length", "content-type", "set-cookie", "set-cookie2", "strict-transport-security",
            "alt-svc",
        )

        fun defaultClient(): OkHttpClient = OkHttpClient.Builder()
            .followRedirects(false)
            .followSslRedirects(false)
            .connectTimeout(10, TimeUnit.SECONDS)
            .readTimeout(60, TimeUnit.SECONDS)
            .writeTimeout(60, TimeUnit.SECONDS)
            .build()

        internal fun hasDotSegments(path: String): Boolean {
            val p = path.lowercase().replace("%2e", ".").replace("%2f", "/").replace("%5c", "/").replace('\\', '/')
            return p.split('/').any { it == ".." || it == "." }
        }

        private fun jsString(s: String): String =
            "\"" + s.replace("\\", "\\\\").replace("\"", "\\\"").replace("<", "\\u003c") + "\""
    }
}
