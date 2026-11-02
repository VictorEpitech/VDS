package app.vds.relay

import java.net.URI

/** Rectangle en pixels CSS (le natif multiplie par la densité). */
data class WebRect(val x: Int, val y: Int, val w: Int, val h: Int)

/** Vues natives (implémentée dans l'app par `WebOverlay` ; fausse dans les tests JVM). Thread UI. */
interface WebHost {
    fun count(): Int
    fun has(id: String): Boolean

    /** [done] reçoit (ok, code) une seule fois : code parmi url_native_error | url_timeout | url_refused. */
    fun prepare(id: String, url: String, rect: WebRect, done: (Boolean, String?) -> Unit)
    fun show(id: String)
    fun rect(id: String, rect: WebRect)
    fun hide(id: String)
}

/** Filtre des URL de la WebView native : https seul, jamais le relais local ni une adresse de boucle. */
object WebUrlFilter {
    private const val MAX_LEN = 2048

    fun allowed(url: String?): Boolean {
        if (url == null || url.length > MAX_LEN) return false
        val u = try { URI(url) } catch (_: Exception) { return false }
        if (!"https".equals(u.scheme, ignoreCase = true) || u.userInfo != null) return false
        val host = (u.host ?: return false).removePrefix("[").removeSuffix("]").lowercase()
        if (host.isEmpty() || host == "localhost" || host.endsWith(".localhost")) return false
        if (host == "::1" || host == "0.0.0.0" || host == "::" || host.startsWith("127.")) return false
        return !isPrivateLiteral(host)
    }

    /** Hôte littéral (décimal, hexa, octal, IPv6 dont IPv4-mappé) : refusé s'il est local ou privé ; non analysable = refusé. */
    private fun isPrivateLiteral(host: String): Boolean {
        val addr: java.net.InetAddress = when {
            host.contains(':') -> try { java.net.InetAddress.getByName(host) } catch (_: Exception) { return true }
            NUMERIC.matches(host) || host.startsWith("0x") -> ipv4(host) ?: return true
            else -> return false
        }
        val b = addr.address
        val ula = b.size == 16 && (b[0].toInt() and 0xfe) == 0xfc
        return addr.isLoopbackAddress || addr.isAnyLocalAddress || addr.isSiteLocalAddress ||
            addr.isLinkLocalAddress || addr.isMulticastAddress || ula
    }

    private val NUMERIC = Regex("^[0-9.]+$")

    private fun ipv4(host: String): java.net.InetAddress? {
        val parts = host.split('.')
        if (parts.size !in 1..4 || parts.any { it.isEmpty() }) return null
        val nums = parts.map {
            when {
                it.startsWith("0x") -> it.substring(2).toLongOrNull(16)
                it.length > 1 && it.startsWith("0") -> it.toLongOrNull(8)
                else -> it.toLongOrNull()
            } ?: return null
        }
        var v = 0L
        for ((i, n) in nums.withIndex()) {
            if (i < nums.size - 1) {
                if (n > 255) return null
                v = v or (n shl (24 - 8 * i))
            } else {
                if (n >= 1L shl (8 * (5 - nums.size))) return null
                v = v or n
            }
        }
        return java.net.InetAddress.getByAddress(byteArrayOf((v shr 24).toByte(), (v shr 16).toByte(), (v shr 8).toByte(), v.toByte()))
    }
}

/**
 * Protocole du pont `VdsNative.web` :
 * `web:<req>:prepare:<id>:<json {url,rect}>` -> réponse `web:<req>:{"ok":…,"code":…}` ;
 * `web:0:show:<id>`, `web:0:rect:<id>:<json rect>`, `web:0:hide:<id>` sans réponse.
 * Au plus [MAX_VIEWS] vues simultanées. Tout message invalide est ignoré (ou refusé `url_refused`).
 */
class WebDispatcher(private val host: WebHost) {
    fun handle(data: String?, reply: (String) -> Unit) {
        val m = REQUEST.matchEntire(data ?: return) ?: return
        val (req, op, id, arg) = m.destructured
        when (op) {
            "prepare" -> prepare(req, id, arg, reply)
            "show" -> if (host.has(id)) host.show(id)
            "rect" -> parseRect(arg)?.let { if (host.has(id)) host.rect(id, it) }
            "hide" -> host.hide(id)
        }
    }

    private fun prepare(req: String, id: String, json: String, reply: (String) -> Unit) {
        fun answer(ok: Boolean, code: String?) =
            reply("web:$req:" + if (ok) "{\"ok\":true}" else "{\"ok\":false,\"code\":\"${code ?: "url_native_error"}\"}")
        val url = URL_FIELD.find(json)?.groupValues?.get(1)?.replace("\\/", "/")
        val rect = parseRect(RECT_FIELD.find(json)?.groupValues?.get(1))
        if (url == null || rect == null || !WebUrlFilter.allowed(url)) return answer(false, "url_refused")
        if (!host.has(id) && host.count() >= MAX_VIEWS) return answer(false, "url_refused")
        host.prepare(id, url, rect, ::answer)
    }

    private fun parseRect(json: String?): WebRect? {
        if (json == null) return null
        fun n(k: String) = Regex("\"$k\"\\s*:\\s*(-?\\d{1,7})(?:\\.\\d+)?").find(json)?.groupValues?.get(1)?.toIntOrNull()
        val (x, y, w, h) = listOf(n("x"), n("y"), n("w"), n("h")).map { it ?: return null }
        return WebRect(x, y, w, h)
    }

    companion object {
        const val MAX_VIEWS = 2
        private val REQUEST = Regex(
            "web:([A-Za-z0-9]{1,12}):(prepare|show|rect|hide):([A-Za-z0-9_-]{1,32})(?::(.*))?",
            RegexOption.DOT_MATCHES_ALL,
        )
        // JSON produit par JSON.stringify : la chaîne d'URL n'a ni guillemet ni antislash hors « \/ ».
        private val URL_FIELD = Regex("\"url\"\\s*:\\s*\"((?:[^\"\\\\]|\\\\/)*)\"")
        private val RECT_FIELD = Regex("\"rect\"\\s*:\\s*(\\{[^{}]*\\})")
    }
}
