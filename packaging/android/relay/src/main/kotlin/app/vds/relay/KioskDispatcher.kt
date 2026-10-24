package app.vds.relay

import java.security.MessageDigest

enum class KioskMode(val wire: String) {
    LOCKED("locked"),
    UNLOCKED("unlocked"),
    MAINTENANCE("maintenance"),
}

/** Verrouillage effectivement possible sur cet appareil (remonté à l'admin par le heartbeat). */
enum class Enforcement(val wire: String) {
    LOCK_TASK("lock_task"),
    SOFT("soft"),
}

/** Actionneur de la plateforme (implémenté dans l'app ; faux dans les tests JVM). */
interface KioskActuator {
    /**
     * Applique [mode] (`LOCKED`, `UNLOCKED` persistant ou `MAINTENANCE` bornée). Lève tant que l'état
     * demandé n'est pas CONFIRMÉ (lock task non actif compris) : le dispatcher réessaie alors.
     */
    fun apply(mode: KioskMode): Enforcement
}

/** Minuterie du reverrouillage autonome (Handler principal dans l'app). Un seul délai en attente. */
interface KioskTimer {
    fun schedule(delayMs: Long, task: () -> Unit)
    fun cancel()
}

/**
 * Protocole du pont `VdsNative.kiosk` : message `kiosk:<id>:<mode>:<restant_ms>:<fenetre>:<jeton>`
 * (mode = locked | unlocked | maintenance ; fenêtre = échéance `until` en secondes, 0 sinon ; jeton =
 * secret remis une seule fois au contrôleur kiosque du player, voir [VdsBridge]), réponse
 * `kiosk:<id>:{"ok":…,"enforcement":…,"mode":<mode APPLIQUÉ>}`. Un jeton faux est ignoré en silence.
 *
 * Le player demande, le natif borne et tient seul son échéance :
 * - une fenêtre de maintenance a une échéance absolue sur [clock] (horloge monotone), fixée à la première
 *   demande, jamais allongée ni réarmée (rechargement, redémarrage de page, recul de l'horloge murale) ;
 * - `unlocked` est un bail de [UNLOCK_LEASE_MS] que le player renouvelle tant qu'il vit ;
 * - un reverrouillage qui échoue est retenté toutes les [RETRY_MS] (et à [resume]) jusqu'à confirmation ;
 *   une demande `locked` n'annule jamais ce minuteur avant un succès réel.
 * Pur JVM : testé par `:relay:test`.
 */
class KioskDispatcher(
    private val actuator: KioskActuator,
    private val timer: KioskTimer,
    private val token: String,
    private val clock: () -> Long = { System.nanoTime() / 1_000_000 },
) {
    /** Mode APPLIQUÉ (confirmé par l'actionneur), pas le mode demandé. */
    @Volatile
    var mode: KioskMode = KioskMode.LOCKED
        private set

    private var relockPending = false
    private var windowId = -1L
    private var windowDeadline = 0L

    /** Retourne la réponse, ou null si [data] n'est pas un message kiosque valide et authentifié. */
    fun handle(data: String?): String? {
        val m = REQUEST.matchEntire(data ?: return null) ?: return null
        val (id, wire, remaining, window, tok) = m.destructured
        if (token.length < MIN_TOKEN || !MessageDigest.isEqual(tok.toByteArray(), token.toByteArray())) return null
        val asked = KioskMode.entries.first { it.wire == wire }
        val ms = remaining.toLongOrNull() ?: return null
        val win = window.toLongOrNull() ?: return null
        return "kiosk:$id:" + apply(asked, ms, win)
    }

    /** Retour au premier plan : retente le verrouillage s'il n'est pas confirmé. */
    fun resume() {
        if (mode == KioskMode.LOCKED || relockPending) lock()
    }

    private fun apply(asked: KioskMode, remainingMs: Long, win: Long): String {
        val now = clock()
        val bounded = remainingMs.coerceIn(0, MAX_MAINTENANCE_MS)
        var target = KioskMode.LOCKED
        if (asked == KioskMode.MAINTENANCE && bounded > 0) {
            // Échéance monotone ancrée à la première demande de cette fenêtre : on ne l'allonge jamais.
            windowDeadline = if (win == windowId) minOf(windowDeadline, now + bounded) else now + bounded
            windowId = win
            if (now < windowDeadline) target = KioskMode.MAINTENANCE
        } else if (asked == KioskMode.UNLOCKED) {
            target = KioskMode.UNLOCKED
        }
        if (target == KioskMode.LOCKED) return answer(lock())
        return try {
            val enforcement = actuator.apply(target)
            mode = target
            relockPending = false
            val delay = if (target == KioskMode.MAINTENANCE) windowDeadline - now else UNLOCK_LEASE_MS
            timer.schedule(delay) { lock() }
            json(true, enforcement, mode)
        } catch (_: Exception) {
            // Échec : on ne laisse jamais un déverrouillage à moitié appliqué.
            lock()
            json(false, Enforcement.SOFT, mode)
        }
    }

    /** Tente de verrouiller. Succès = minuteur annulé ; échec = nouvel essai programmé. */
    private fun lock(): Enforcement? = try {
        val e = actuator.apply(KioskMode.LOCKED)
        mode = KioskMode.LOCKED
        relockPending = false
        timer.cancel()
        e
    } catch (_: Exception) {
        relockPending = true
        timer.schedule(RETRY_MS) { lock() }
        null
    }

    private fun answer(e: Enforcement?) = if (e != null) json(true, e, mode) else json(false, Enforcement.SOFT, mode)

    companion object {
        const val MAX_MAINTENANCE_MS = 240L * 60_000
        const val UNLOCK_LEASE_MS = 15L * 60_000
        const val RETRY_MS = 5_000L
        const val MIN_TOKEN = 16
        private val REQUEST = Regex(
            """kiosk:([A-Za-z0-9_-]{1,32}):(locked|unlocked|maintenance):(\d{1,12}):(\d{1,12}):([A-Za-z0-9]{16,64})""",
        )

        fun json(ok: Boolean, e: Enforcement, mode: KioskMode) =
            "{\"ok\":$ok,\"enforcement\":\"${e.wire}\",\"mode\":\"${mode.wire}\"}"
    }
}
