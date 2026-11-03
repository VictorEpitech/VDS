package app.vds.relay

import java.security.MessageDigest
import java.util.Base64

/** Actionneur de la plateforme pour les commandes player (implémenté dans l'app ; faux dans les tests JVM). */
interface CmdActuator {
    val canScreenshot: Boolean
    val canRestartApp: Boolean
    val canRestartDevice: Boolean

    /** Capture le rendu de l'app (vidéo comprise) ; [done] reçoit un JPEG ou une erreur, sur n'importe quel thread. */
    fun screenshot(maxEdge: Int, quality: Int, done: (Result<ByteArray>) -> Unit)

    /** Relance l'activité en restant en lock task. */
    fun restartApp()

    /** Redémarre l'appareil ; lève si impossible (pas Device Owner). */
    fun restartDevice()
}

/** Planification différée (le redémarrage part APRÈS la réponse au player). */
interface CmdScheduler {
    fun postDelayed(delayMs: Long, task: () -> Unit)
}

/**
 * Protocole du pont `VdsNative.claimCmd()` : message `cmd:<id>:<op>:<jeton>` avec `op` =
 * `caps` | `shot-<bord max>-<qualité>` | `restart-app` | `restart-device` ; réponse `cmd:<id>:<json>`.
 * Le jeton est remis une seule fois au contrôleur de commandes du player (comme le kiosque) ; un message
 * mal formé, hors bornes ou au jeton faux est ignoré en silence (aucune réponse, aucune info).
 * Le JSON ne contient que `ok`, un code court et, pour la capture, `jpeg` (base64) : rien de technique
 * n'est jamais affiché, et aucun secret n'est renvoyé.
 * Plafonds : capture une à la fois, 1 toutes les [MIN_SHOT_GAP_MS] ; redémarrages au plus 1 / [MIN_RESTART_GAP_MS]
 * (anti-boucle de redémarrage). Pur JVM : testé par `:relay:test`.
 */
class CmdDispatcher(
    private val actuator: CmdActuator,
    private val scheduler: CmdScheduler,
    private val token: String,
    private val clock: () -> Long = { System.nanoTime() / 1_000_000 },
) {
    private var lastShot = Long.MIN_VALUE / 2
    private var lastRestart = Long.MIN_VALUE / 2
    private var shotBusy = false

    /** [reply] peut être appelé plus tard (capture asynchrone). Retourne sans rien faire si [data] est invalide. */
    @Synchronized
    fun handle(data: String?, reply: (String) -> Unit) {
        val m = REQUEST.matchEntire(data ?: return) ?: return
        val (id, op, tok) = m.destructured
        if (token.length < MIN_TOKEN || !MessageDigest.isEqual(tok.toByteArray(), token.toByteArray())) return
        fun out(json: String) = reply("cmd:$id:$json")
        when {
            op == "caps" -> out(caps())
            op.startsWith("shot-") -> shot(op, ::out)
            op == "restart-app" -> restart(actuator.canRestartApp, actuator::restartApp, ::out)
            op == "restart-device" -> restart(actuator.canRestartDevice, actuator::restartDevice, ::out)
        }
    }

    private fun caps() =
        "{\"ok\":true,\"screenshot\":${actuator.canScreenshot},\"restart_app\":${actuator.canRestartApp}," +
            "\"restart_device\":${actuator.canRestartDevice}}"

    private fun shot(op: String, out: (String) -> Unit) {
        val p = op.split('-')
        val edge = p.getOrNull(1)?.toIntOrNull()
        val quality = p.getOrNull(2)?.toIntOrNull()
        if (p.size != 3 || edge == null || quality == null || edge !in MIN_EDGE..MAX_EDGE || quality !in 1..100) return
        val now = clock()
        if (!actuator.canScreenshot) return out(fail("unsupported"))
        if (shotBusy || now - lastShot < MIN_SHOT_GAP_MS) return out(fail("busy"))
        shotBusy = true
        lastShot = now
        try {
            actuator.screenshot(edge, quality) { r ->
                synchronized(this) { shotBusy = false }
                val bytes = r.getOrNull()
                when {
                    bytes == null || bytes.isEmpty() -> out(fail("capture_failed"))
                    bytes.size > MAX_JPEG_BYTES -> out(fail("too_large"))
                    else -> out("{\"ok\":true,\"jpeg\":\"${Base64.getEncoder().encodeToString(bytes)}\"}")
                }
            }
        } catch (_: Exception) {
            shotBusy = false
            out(fail("capture_failed"))
        }
    }

    private fun restart(supported: Boolean, action: () -> Unit, out: (String) -> Unit) {
        if (!supported) return out(fail("unsupported"))
        val now = clock()
        if (now - lastRestart < MIN_RESTART_GAP_MS) return out(fail("busy"))
        lastRestart = now
        out("{\"ok\":true}") // la réponse part avant : le redémarrage peut tuer la page
        scheduler.postDelayed(RESTART_DELAY_MS) {
            try {
                action()
            } catch (_: Exception) {
                // sans effet : le player constate l'absence de redémarrage (cmd.restart_not_observed)
            }
        }
    }

    private fun fail(code: String) = "{\"ok\":false,\"code\":\"$code\"}"

    companion object {
        const val MIN_TOKEN = 16
        const val MIN_EDGE = 64
        const val MAX_EDGE = 2048
        const val MAX_JPEG_BYTES = 2 * 1024 * 1024
        const val MIN_SHOT_GAP_MS = 2_000L
        const val MIN_RESTART_GAP_MS = 30_000L
        const val RESTART_DELAY_MS = 500L
        private val REQUEST = Regex(
            """cmd:([A-Za-z0-9_-]{1,32}):(caps|shot-\d{1,4}-\d{1,3}|restart-app|restart-device):([A-Za-z0-9]{16,64})""",
        )
    }
}
