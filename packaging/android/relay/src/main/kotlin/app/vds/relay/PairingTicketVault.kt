package app.vds.relay

/** Enregistrement persistant : le ticket n'y figure que scellé (chiffré), lié à l'URL serveur d'origine. */
data class SealedTicket(val sealed: String, val serverUrl: String, val atMs: Long)

/** Persistance du ticket scellé (SharedPreferences privées dans l'app ; mémoire dans les tests JVM). */
interface TicketStore {
    fun read(): SealedTicket?
    fun write(t: SealedTicket)
    fun clear()
}

/** Chiffrement du ticket au repos (AES-GCM, clé Android Keystore non exportable dans l'app). */
interface TicketSealer {
    fun seal(plain: String): String
    fun open(sealed: String): String?
}

/**
 * Ticket d'appairage injecté par `ProvisionActivity` (ADB) : stocké chiffré, lié à l'URL serveur,
 * remis UNE seule fois au player web (`ticket:claim`), puis effacé. Jamais journalisé ni affiché.
 * Périmé après [TTL_MS] (le serveur l'invalide à 30 min) ou si l'URL serveur a changé.
 * Pur JVM : testé par `:relay:test`.
 */
class PairingTicketVault(
    private val store: TicketStore,
    private val sealer: TicketSealer,
    private val clock: () -> Long = System::currentTimeMillis,
) {
    /** Retourne faux (rien de stocké) si le ticket n'est pas un base64url de 16 à 64 caractères. */
    fun put(ticket: String?, serverUrl: String): Boolean {
        store.clear()
        if (ticket == null || !FORMAT.matches(ticket)) return false
        val sealed = try {
            sealer.seal(ticket)
        } catch (_: Exception) {
            return false
        }
        store.write(SealedTicket(sealed, serverUrl, clock()))
        return true
    }

    fun hasPending(): Boolean = store.read() != null

    /** Remet le ticket une seule fois : il est effacé dans tous les cas (valide, périmé, autre URL). */
    fun claim(currentServerUrl: String?): String? {
        val t = store.read() ?: return null
        store.clear()
        val age = clock() - t.atMs
        if (currentServerUrl == null || t.serverUrl != currentServerUrl || age < 0 || age > TTL_MS) return null
        return try {
            sealer.open(t.sealed)
        } catch (_: Exception) {
            null
        }
    }

    fun clear() = store.clear()

    /** Réponse du pont : `{"ticket":"…"}` ou `{}` ; le ticket étant base64url, aucun échappement n'est requis. */
    fun claimReply(currentServerUrl: String?): String =
        claim(currentServerUrl)?.let { "{\"ticket\":\"$it\"}" } ?: "{}"

    companion object {
        const val TTL_MS = 30 * 60_000L
        val FORMAT = Regex("^[A-Za-z0-9_-]{16,64}$")
    }
}
