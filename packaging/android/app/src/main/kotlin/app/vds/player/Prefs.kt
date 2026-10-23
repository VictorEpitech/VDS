package app.vds.player

import android.content.Context
import app.vds.relay.RelayConfig

/** Réglages locaux : URL serveur, et URL à laquelle appartient le stockage web actuel. */
class Prefs(context: Context) {
    private val sp = context.applicationContext.getSharedPreferences("vds", Context.MODE_PRIVATE)

    var serverUrl: String?
        get() = sp.getString(KEY_URL, null)
        set(v) = sp.edit().putString(KEY_URL, v).apply()

    /** URL pour laquelle le stockage web (IndexedDB, caches, Service Worker) a été initialisé. */
    var webStorageFor: String?
        get() = sp.getString(KEY_WEB, null)
        set(v) = sp.edit().putString(KEY_WEB, v).apply()

    fun config(): RelayConfig? = RelayConfig.parse(serverUrl, BuildConfig.ALLOW_LAN_HTTP)

    /** Vrai si le stockage web doit être purgé avant de charger le player. */
    fun needsPurge(cfg: RelayConfig): Boolean = webStorageFor != cfg.url

    private companion object {
        const val KEY_URL = "server_url"
        const val KEY_WEB = "web_storage_for"
    }
}
