package app.vds.player

import android.app.Application
import app.vds.relay.Relay
import app.vds.relay.RelayConfig
import app.vds.relay.RelayPortException

/** Le relais vit avec le processus, pas avec l'activité : il survit aux recréations de la WebView. */
class VdsApp : Application() {
    private var relay: Relay? = null

    /** Code d'erreur natif à afficher (jamais de détail technique), ou null. */
    @Volatile
    var relayError: String? = null
        private set

    /** Bloquant : à appeler hors du fil principal. Idempotent pour une même URL. */
    @Synchronized
    fun ensureRelay(cfg: RelayConfig): Boolean {
        if (relay?.running == true && relayUrl == cfg.url) return true
        relay?.stop()
        val r = Relay(cfg, Relay.DEFAULT_PORT)
        return try {
            r.start()
            relay = r
            relayUrl = cfg.url
            relayError = null
            true
        } catch (_: RelayPortException) {
            relayError = ERR_RELAY_PORT
            false
        }
    }

    private var relayUrl: String? = null

    companion object {
        const val ERR_RELAY_PORT = "E_RELAY_PORT"
    }
}
