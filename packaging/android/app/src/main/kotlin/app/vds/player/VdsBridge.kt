package app.vds.player

import android.os.Build
import android.os.SystemClock
import org.json.JSONObject

/**
 * Pont `window.VdsNative`, réservé à la frame principale d'origine locale (le player).
 *
 * Il n'utilise plus `addJavascriptInterface` (exposé à toutes les frames, iframes `content.url` comprises) :
 * `WebViewCompat.addWebMessageListener` + `addDocumentStartJavaScript` avec une règle d'origine
 * `http://127.0.0.1:<port>` n'injectent le pont que dans les frames de cette origine, et [onMessage]
 * refuse en plus tout message qui ne vient pas de la frame principale. Une iframe d'une autre origine
 * n'a donc ni `ping`, ni `reload`, ni `info`.
 * `info()` reste synchrone côté page : la réponse est figée dans le script injecté.
 */
class VdsBridge(
    private val trusted: () -> Boolean,
    private val onReload: () -> Unit,
) {
    @Volatile
    var lastPing: Long = SystemClock.elapsedRealtime()
        private set

    fun resetPing() {
        lastPing = SystemClock.elapsedRealtime()
    }

    fun onMessage(isMainFrame: Boolean, data: String?) {
        if (!isMainFrame || !trusted()) return
        when (data) {
            MSG_PING -> lastPing = SystemClock.elapsedRealtime()
            MSG_RELOAD -> onReload()
        }
    }

    companion object {
        const val LISTENER_NAME = "VdsBridgeMsg"
        const val MSG_PING = "ping"
        const val MSG_RELOAD = "reload"

        /** JSON renvoyé par `info()` ; `insecure_upstream` est lu par le player (heartbeat, jamais affiché). */
        fun infoJson(insecureUpstream: Boolean): String = JSONObject()
            .put("platform", "android")
            .put("model", Build.MODEL ?: "")
            .put("app_version", BuildConfig.VERSION_NAME)
            .put("os_version", Build.VERSION.RELEASE ?: "")
            .put("insecure_upstream", insecureUpstream)
            .toString()

        /** Script exécuté au début de chaque document des origines autorisées. */
        fun script(infoJson: String): String = """
            (function(){
              var t=window.$LISTENER_NAME;
              if(!t)return;
              var info=${JSONObject.quote(infoJson)};
              window.VdsNative={
                ping:function(){t.postMessage("$MSG_PING")},
                reload:function(){t.postMessage("$MSG_RELOAD")},
                info:function(){return info}
              };
            })();
        """.trimIndent()
    }
}
