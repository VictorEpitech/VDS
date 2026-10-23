package app.vds.player

import android.app.Activity
import android.content.Intent
import android.os.Bundle
import app.vds.relay.RelayConfig

/**
 * Provisionnement par ADB : `adb shell am start -n app.vds.player/.ProvisionActivity --es server_url <url>`.
 * Exportée mais protégée par la permission signature `DUMP` (manifeste) : seul l'utilisateur shell peut la
 * lancer, aucune app tierce. Sans effet visible.
 */
class ProvisionActivity : Activity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        RelayConfig.parse(intent?.getStringExtra("server_url"), BuildConfig.ALLOW_LAN_HTTP)?.let {
            Prefs(this).serverUrl = it.url
        }
        startActivity(Intent(this, MainActivity::class.java).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_CLEAR_TOP))
        finish()
    }
}
