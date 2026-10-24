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
    /** Les ordres CEC peuvent bloquer : jamais sur le thread UI. */
    private val background: (() -> Unit) -> Unit = { it() },
) {
    fun onMessage(isMainFrame: Boolean, data: String?, reply: (String) -> Unit = {}) {
        if (!isMainFrame || !trusted() || data == null) return
        when {
            data == MSG_PING -> onPing()
            data == MSG_RELOAD -> onReload()
            data.startsWith("cec:") -> background { cec.handle(data)?.let(reply) }
            data.startsWith("kiosk:") -> kiosk?.handle(data)?.let(reply)
        }
    }

    companion object {
        const val MSG_PING = "ping"
        const val MSG_RELOAD = "reload"
    }
}
