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
    /** Amont en clair (HTTP) vers un hôte non local : toléré uniquement par l'option LAN explicite. */
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
         * HTTPS obligatoire. HTTP n'est accepté que vers la boucle locale, ou vers un autre hôte si
         * [allowInsecureLan] (option LAN explicite du build) ; la config est alors marquée [insecureUpstream].
         */
        fun parse(raw: String?, allowInsecureLan: Boolean = false): RelayConfig? {
            val text = raw?.trim()?.trimEnd('/') ?: return null
            if (text.isEmpty() || text.any { it.isWhitespace() }) return null
            val uri = try {
                URI(text)
            } catch (_: Exception) {
                return null
            }
            val scheme = uri.scheme?.lowercase() ?: return null
            if (scheme != "http" && scheme != "https") return null
            val host = uri.host ?: return null
            val insecure = scheme == "http" && !isLoopback(host)
            if (insecure && !allowInsecureLan) return null
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

    }
}
