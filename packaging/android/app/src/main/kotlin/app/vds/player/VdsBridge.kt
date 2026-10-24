package app.vds.player

import android.os.Build
import android.os.SystemClock
import app.vds.relay.BridgeRouter
import app.vds.relay.CecDispatcher
import app.vds.relay.KioskDispatcher
import org.json.JSONObject
import java.util.concurrent.Executors

/**
 * Pont `window.VdsNative`, réservé à la frame principale d'origine locale (le player).
 *
 * Il n'utilise plus `addJavascriptInterface` (exposé à toutes les frames, iframes `content.url` comprises) :
 * `WebViewCompat.addWebMessageListener` + `addDocumentStartJavaScript` avec une règle d'origine
 * `http://127.0.0.1:<port>` n'injectent le pont que dans les frames de cette origine, et [onMessage]
 * refuse en plus tout message qui ne vient pas de la frame principale. Une iframe d'une autre origine
 * n'a donc ni `ping`, ni `reload`, ni `info`.
 * `info()` reste synchrone côté page : la réponse est figée dans le script injecté.
 * `cec(action)` retourne une Promise<string JSON> : la réponse revient par `replyProxy.postMessage`.
 */
class VdsBridge(
    trusted: () -> Boolean,
    onReload: () -> Unit,
    cec: CecDispatcher = CecDispatcher(null),
    kiosk: KioskDispatcher? = null,
) {
    // Les ordres CEC peuvent bloquer (attente de la réponse du bus) : jamais sur le thread UI.
    private val cecExecutor = Executors.newSingleThreadExecutor { r -> Thread(r, "vds-cec").apply { isDaemon = true } }

    @Volatile
    var lastPing: Long = SystemClock.elapsedRealtime()
        private set

    private val router = BridgeRouter(
        trusted = trusted,
        onPing = { lastPing = SystemClock.elapsedRealtime() },
        onReload = onReload,
        cec = cec,
        kiosk = kiosk,
        background = { cecExecutor.execute(it) },
    )

    fun resetPing() {
        lastPing = SystemClock.elapsedRealtime()
    }

    /** Appelé sur le thread principal par `addWebMessageListener` (le kiosque s'applique donc sur l'UI). */
    fun onMessage(isMainFrame: Boolean, data: String?, reply: (String) -> Unit = {}) =
        router.onMessage(isMainFrame, data, reply)

    companion object {
        const val LISTENER_NAME = "VdsBridgeMsg"
        const val MSG_PING = BridgeRouter.MSG_PING
        const val MSG_RELOAD = BridgeRouter.MSG_RELOAD

        /** JSON renvoyé par `info()` ; `insecure_upstream` est lu par le player (heartbeat, jamais affiché). */
        fun infoJson(insecureUpstream: Boolean): String = JSONObject()
            .put("platform", "android")
            .put("model", Build.MODEL ?: "")
            .put("app_version", BuildConfig.VERSION_NAME)
            .put("os_version", Build.VERSION.RELEASE ?: "")
            .put("insecure_upstream", insecureUpstream)
            .toString()

        /** Script exécuté au début de chaque document des origines autorisées. */
        fun script(infoJson: String, kioskToken: String = ""): String = """
            (function(){
              var t=window.$LISTENER_NAME;
              if(!t)return;
              var info=${JSONObject.quote(infoJson)},n=0,w={};
              function req(p,body,fail){
                return new Promise(function(res){
                  var id=(++n).toString(36),k=p+":"+id;
                  var to=setTimeout(function(){delete w[k];res(fail)},15000);
                  w[k]=function(j){clearTimeout(to);delete w[k];res(j)};
                  t.postMessage(k+":"+body);
                });
              }
              var kioskToken=${JSONObject.quote(kioskToken)},claimed=false;
              window.VdsNative={
                ping:function(){t.postMessage("$MSG_PING")},
                reload:function(){t.postMessage("$MSG_RELOAD")},
                info:function(){return info},
                cec:function(a){
                  return req("cec",a,'{"ok":false,"state":"unknown","verified":false,"code":"power.cec.timeout"}');
                },
                // Remise UNIQUE du pont kiosque : le contrôleur du player l'appelle au démarrage, avant tout
                // content-type ; ensuite plus rien n'expose le jeton à un autre script de la page.
                claimKiosk:function(){
                  if(claimed||!kioskToken)return null;
                  claimed=true;
                  var tk=kioskToken;kioskToken="";
                  return function(m,r,u){
                    return req("kiosk",m+":"+Math.max(0,Math.floor(r))+":"+Math.max(0,Math.floor(u||0))+":"+tk,
                      '{"ok":false,"enforcement":"soft","mode":"unknown"}');
                  };
                }
              };
              t.onmessage=function(e){
                var d=String(e.data),m=/^(cec|kiosk):([A-Za-z0-9_-]+):(.*)${'$'}/.exec(d);
                if(m&&w[m[1]+":"+m[2]])w[m[1]+":"+m[2]](m[3]);
              };
            })();
        """.trimIndent()
    }
}
