package app.vds.relay

/** Résultat d'un ordre CEC, sérialisé tel quel pour le player (`power.cec`). */
data class CecResult(val ok: Boolean, val state: String, val verified: Boolean, val code: String? = null)

/** Accès CEC de la plateforme (implémenté dans l'app ; faux dans les tests JVM). */
interface CecController {
    fun available(): Boolean
    fun power(on: Boolean): CecResult
    fun state(): String
}

/**
 * Protocole du pont `VdsNative.cec` : message `cec:<id>:<action>` (action = probe | on | off | state),
 * réponse `cec:<id>:<json>`. Les entrées sont validées strictement ; toute autre chose est ignorée.
 * Pur JVM : testé par `:relay:test` sans SDK Android.
 */
class CecDispatcher(private val controller: CecController?) {
    /** Retourne la réponse, ou null si [data] n'est pas un message CEC valide. */
    fun handle(data: String?): String? {
        val m = REQUEST.matchEntire(data ?: return null) ?: return null
        val (id, action) = m.destructured
        return "cec:$id:" + run(action)
    }

    private fun run(action: String): String {
        val c = controller ?: return json(CecResult(false, "unknown", false, "power.cec.unavailable"))
        return try {
            when (action) {
                "probe" -> json(CecResult(c.available(), "unknown", false, if (c.available()) null else "power.cec.unavailable"))
                "on" -> json(c.power(true))
                "off" -> json(c.power(false))
                else -> json(CecResult(true, c.state(), false))
            }
        } catch (e: Exception) {
            json(CecResult(false, "unknown", false, "power.cec.failed"))
        }
    }

    companion object {
        private val REQUEST = Regex("""cec:([A-Za-z0-9_-]{1,32}):(probe|on|off|state)""")
        private val STATES = setOf("on", "off", "standby", "unknown")

        fun json(r: CecResult): String {
            val state = if (r.state in STATES) r.state else "unknown"
            val code = r.code?.takeIf { it.matches(Regex("[a-z0-9_.]{1,48}")) }
            return buildString {
                append("{\"ok\":${r.ok},\"state\":\"$state\",\"verified\":${r.verified}")
                if (code != null) append(",\"code\":\"$code\"")
                append('}')
            }
        }
    }
}
