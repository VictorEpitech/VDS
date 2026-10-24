package app.vds.relay

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
    /** Applique [mode] (jamais `MAINTENANCE` : le dispatcher passe `UNLOCKED`). Lève en cas d'échec. */
    fun apply(mode: KioskMode): Enforcement
}

/** Minuterie du reverrouillage autonome (Handler principal dans l'app). Un seul délai en attente. */
interface KioskTimer {
    fun schedule(delayMs: Long, task: () -> Unit)
    fun cancel()
}

/**
 * Protocole du pont `VdsNative.kiosk` : message `kiosk:<id>:<mode>:<restant_ms>`
 * (mode = locked | unlocked | maintenance), réponse `kiosk:<id>:{"ok":…,"enforcement":…}`.
 *
 * Le player demande, le natif borne : une maintenance dure au plus [MAX_MAINTENANCE_MS] à compter
 * de la réception et le natif reverrouille seul à l'échéance, même si la page est morte. Échec de
 * déverrouillage = reste verrouillé ; échec de verrouillage = réponse `ok:false`, nouvel essai à
 * l'échéance suivante. Pur JVM : testé par `:relay:test`.
 */
class KioskDispatcher(private val actuator: KioskActuator, private val timer: KioskTimer) {
    @Volatile
    var mode: KioskMode = KioskMode.LOCKED
        private set

    /** Retourne la réponse, ou null si [data] n'est pas un message kiosque valide. */
    fun handle(data: String?): String? {
        val m = REQUEST.matchEntire(data ?: return null) ?: return null
        val (id, wire, remaining) = m.destructured
        val asked = KioskMode.entries.first { it.wire == wire }
        val ms = remaining.toLongOrNull() ?: return null
        return "kiosk:$id:" + apply(asked, ms)
    }

    private fun apply(asked: KioskMode, remainingMs: Long): String {
        timer.cancel()
        val bounded = remainingMs.coerceIn(0, MAX_MAINTENANCE_MS)
        val target = when {
            asked == KioskMode.MAINTENANCE && bounded > 0 -> KioskMode.MAINTENANCE
            asked == KioskMode.UNLOCKED -> KioskMode.UNLOCKED
            else -> KioskMode.LOCKED
        }
        return try {
            val enforcement = actuator.apply(if (target == KioskMode.LOCKED) KioskMode.LOCKED else KioskMode.UNLOCKED)
            mode = target
            if (target == KioskMode.MAINTENANCE) timer.schedule(bounded) { relock() }
            json(true, enforcement)
        } catch (_: Exception) {
            // Échec : on ne laisse jamais un déverrouillage à moitié appliqué.
            mode = try {
                actuator.apply(KioskMode.LOCKED)
                KioskMode.LOCKED
            } catch (_: Exception) {
                mode
            }
            json(false, Enforcement.SOFT)
        }
    }

    private fun relock() {
        try {
            actuator.apply(KioskMode.LOCKED)
            mode = KioskMode.LOCKED
        } catch (_: Exception) {
            timer.schedule(RETRY_MS) { relock() }
        }
    }

    companion object {
        const val MAX_MAINTENANCE_MS = 240L * 60_000
        const val RETRY_MS = 5_000L
        private val REQUEST = Regex("""kiosk:([A-Za-z0-9_-]{1,32}):(locked|unlocked|maintenance):(\d{1,12})""")

        fun json(ok: Boolean, e: Enforcement) = "{\"ok\":$ok,\"enforcement\":\"${e.wire}\"}"
    }
}
