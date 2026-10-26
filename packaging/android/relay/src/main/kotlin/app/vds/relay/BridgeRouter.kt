package app.vds.relay

/**
 * Routage des messages du pont `VdsNative` (reçus par `addWebMessageListener`). Seule la frame
 * principale de l'origine du relais peut agir : une iframe (`content.url`) n'a pas le pont, et un
 * message qui lui échapperait serait refusé ici ([isMainFrame] faux ou page non fiable).
 */
class BridgeRouter(
    private val trusted: () -> Boolean,
    private val onPing: () -> Unit,
    private val onReload: () -> Unit,
    private val cec: CecDispatcher = CecDispatcher(null),
    private val kiosk: KioskDispatcher? = null,
    private val tickets: PairingTicketVault? = null,
    /** URL serveur courante (le ticket n'est remis que si elle est celle pour laquelle il a été émis). */
    private val serverUrl: () -> String? = { null },
    /** Les ordres CEC peuvent bloquer : jamais sur le thread UI. */
    private val background: (() -> Unit) -> Unit = { it() },
) {
    fun onMessage(isMainFrame: Boolean, data: String?, reply: (String) -> Unit = {}) {
        if (!isMainFrame || !trusted() || data == null) return
        when {
            data == MSG_PING -> onPing()
            data == MSG_RELOAD -> onReload()
            data.startsWith("cec:") -> background { cec.handle(data)?.let(reply) }
            data.startsWith("ticket:") -> ticketReply(data)?.let(reply)
            data.startsWith("kiosk:") -> kiosk?.handle(data)?.let(reply)
        }
    }

    /** `ticket:<id>:claim` -> `ticket:<id>:{"ticket":…}` ; toute autre forme est ignorée. */
    private fun ticketReply(data: String): String? {
        val parts = data.split(':')
        if (parts.size != 3 || parts[2] != "claim" || !ID.matches(parts[1])) return null
        val v = tickets ?: return "ticket:${parts[1]}:{}"
        return "ticket:${parts[1]}:${v.claimReply(serverUrl())}"
    }

    companion object {
        private val ID = Regex("^[A-Za-z0-9_-]+$")
        const val MSG_PING = "ping"
        const val MSG_RELOAD = "reload"
    }
}
