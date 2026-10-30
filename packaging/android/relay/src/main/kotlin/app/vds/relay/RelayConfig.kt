package app.vds.relay

import java.net.URI

/**
 * URL du serveur VDS saisie à l'installation, ex. `https://vds.client.fr` ou
 * `https://allia-innovation.fr/espace/affichage`. `basePath` vaut "" (racine) ou "/espace/affichage".
 */
class RelayConfig private constructor(
    val url: String,
    val upstreamOrigin: String,
    val basePath: String,
    /** Amont en clair (HTTP) vers un hôte non local : toléré uniquement vers un hôte du réseau local. */
    val insecureUpstream: Boolean = false,
) {
    /** Chemin (sans requête) relayé : exactement `<base>` ou sous `<base>/`. Racine : tout. */
    fun inScope(path: String): Boolean =
        basePath.isEmpty() || path == basePath || path.startsWith("$basePath/")

    fun localOrigin(port: Int): String = "http://$LOOPBACK:$port"

    /** Page du player vue par la WebView. */
    fun playerUrl(port: Int): String = "${localOrigin(port)}$basePath/player/"

    /** Préfixe des seules URL de premier niveau autorisées dans la WebView. */
    fun allowedPrefix(port: Int): String = "${localOrigin(port)}$basePath/"

    companion object {
        const val LOOPBACK = "127.0.0.1"

        /**
         * HTTPS obligatoire, sauf HTTP vers la boucle locale ou un hôte du réseau local (IP privée, lien local,
         * `.local`) : la config est alors marquée [insecureUpstream] pour l'admin. Sans schéma, `https://` est
         * supposé, ou `http://` si l'hôte est local (`192.168.1.182:8002`).
         */
        fun parse(raw: String?): RelayConfig? {
            var text = raw?.trim()?.trimEnd('/') ?: return null
            if (text.isEmpty() || text.any { it.isWhitespace() }) return null
            if (!text.contains("://")) {
                val h = try { URI("//$text").host } catch (_: Exception) { null } ?: return null
                text = (if (isLocalHost(h)) "http://" else "https://") + text
            }
            val uri = try {
                URI(text)
            } catch (_: Exception) {
                return null
            }
            val scheme = uri.scheme?.lowercase() ?: return null
            if (scheme != "http" && scheme != "https") return null
            val host = uri.host ?: return null
            val insecure = scheme == "http" && !isLoopback(host)
            if (insecure && !isLocalHost(host)) return null
            if (uri.userInfo != null || uri.rawQuery != null || uri.rawFragment != null) return null
            val path = uri.rawPath.orEmpty().trimEnd('/')
            if (path.split('/').any { it == "." || it == ".." }) return null
            val hostPart = if (host.contains(':')) "[$host]" else host
            val origin = buildString {
                append(scheme).append("://").append(hostPart)
                if (uri.port != -1) append(':').append(uri.port)
            }
            return RelayConfig(origin + path, origin, path, insecure)
        }

        private fun isLoopback(host: String): Boolean =
            host == LOOPBACK || host.equals("localhost", ignoreCase = true) || host == "::1" || host == "[::1]"
        /** IP privée/lien local/boucle (RFC 1918, 169.254/16, fc00::/7, fe80::/10) ou nom `.local` / `localhost`. */
        internal fun isLocalHost(host: String): Boolean {
            val h = host.removePrefix("[").removeSuffix("]").lowercase()
            if (isLoopback(h) || h.endsWith(".local") && h.length > 6) return true
            val p = h.split('.')
            if (p.size == 4 && p.all { it.isNotEmpty() && it.length <= 3 && it.all(Char::isDigit) }) {
                val n = p.map { it.toInt() }
                if (n.any { it > 255 }) return false
                return n[0] == 10 || n[0] == 127 || (n[0] == 172 && n[1] in 16..31) ||
                    (n[0] == 192 && n[1] == 168) || (n[0] == 169 && n[1] == 254)
            }
            if (h.contains(':') && h.all { it.isDigit() || it in 'a'..'f' || it == ':' }) {
                val first = h.substringBefore(':').ifEmpty { "0" }.toIntOrNull(16) ?: return false
                return first and 0xfe00 == 0xfc00 || first and 0xffc0 == 0xfe80
            }
            return false
        }
    }
}
