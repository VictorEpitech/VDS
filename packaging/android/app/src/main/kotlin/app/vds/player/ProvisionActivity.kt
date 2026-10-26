package app.vds.player

import android.app.Activity
import android.content.Intent
import android.os.Bundle
import app.vds.relay.RelayConfig

/**
 * Provisionnement par ADB :
 * `adb shell am start -n app.vds.player/.ProvisionActivity --es server_url <url> [--es pairing_ticket <t>]`.
 *
 * Garde-fous : exportée mais protégée par la permission signature `DUMP` (manifeste) : seul l'utilisateur
 * shell peut la lancer, aucune app tierce. L'URL doit passer `RelayConfig.parse` (HTTPS, sauf build LAN) ;
 * le ticket doit être un base64url de 16 à 64 caractères, n'est accepté qu'avec une URL valide, est stocké
 * chiffré et lié à cette URL, puis remis une seule fois au player. Aucun journal, aucun écran, aucun Toast :
 * ni l'URL ni le ticket ne sont jamais affichés ni écrits dans logcat.
 */
class ProvisionActivity : Activity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val vault = PairingTickets.vault { this }
        val cfg = RelayConfig.parse(intent?.getStringExtra("server_url"), BuildConfig.ALLOW_LAN_HTTP)
        if (cfg != null) {
            Prefs(this).serverUrl = cfg.url
            // Absent ou invalide : tout ticket précédent est purgé, le player retombe sur le code d'appairage.
            vault.put(intent?.getStringExtra("pairing_ticket"), cfg.url)
        } else {
            vault.clear()
        }
        // L'intent reçu n'est pas transmis : MainActivity relit Prefs et le coffre.
        startActivity(Intent(this, MainActivity::class.java).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_CLEAR_TOP))
        finish()
    }
}
