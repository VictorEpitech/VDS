package app.vds.player

import android.content.Context
import app.vds.relay.CecController
import app.vds.relay.CecResult
import java.lang.reflect.InvocationTargetException
import java.lang.reflect.Proxy
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit

/**
 * CEC par réflexion sur `HdmiControlManager` (API système, non publique).
 *
 * Contrainte plateforme : `getPlaybackClient()`, `oneTouchPlay` et `standby()` exigent la permission
 * `HDMI_CEC` (signature|privileged). Une APK installée normalement (même Device Owner) est refusée :
 * on répond alors `power.cec.denied`, et le player retombe sur `power.blank`. Le pilote CEC n'est donc
 * effectif que sur un boîtier où l'app est privilégiée (image système / priv-app). Non vérifié sur matériel
 * à ce stade ; tout échec est un code d'erreur, jamais une exception remontée au WebView.
 */
class AndroidCec(private val context: Context) : CecController {
    private val manager: Any? by lazy { context.applicationContext.getSystemService("hdmi_control") }

    private fun playbackClient(): Any? = manager?.let {
        try {
            it.javaClass.getMethod("getPlaybackClient").invoke(it)
        } catch (e: Exception) {
            null
        }
    }

    override fun available(): Boolean = playbackClient() != null

    override fun power(on: Boolean): CecResult {
        val m = manager ?: return CecResult(false, "unknown", false, "power.cec.unsupported")
        return try {
            if (on) oneTouchPlay(playbackClient() ?: return CecResult(false, "unknown", false, "power.cec.unsupported"))
            else {
                m.javaClass.getMethod("standby").invoke(m)
                CecResult(true, "standby", false)
            }
        } catch (e: InvocationTargetException) {
            CecResult(false, "unknown", false, if (e.cause is SecurityException) "power.cec.denied" else "power.cec.failed")
        } catch (e: NoSuchMethodException) {
            CecResult(false, "unknown", false, "power.cec.unsupported")
        } catch (e: SecurityException) {
            CecResult(false, "unknown", false, "power.cec.denied")
        }
    }

    private fun oneTouchPlay(client: Any): CecResult {
        val cbType = Class.forName("android.hardware.hdmi.HdmiPlaybackClient\$OneTouchPlayCallback")
        val latch = CountDownLatch(1)
        var code = -1
        val cb = Proxy.newProxyInstance(cbType.classLoader, arrayOf(cbType)) { _, method, args ->
            if (method.name == "onComplete") {
                code = (args?.get(0) as? Int) ?: -1
                latch.countDown()
            }
            null
        }
        client.javaClass.getMethod("oneTouchPlay", cbType).invoke(client, cb)
        val done = latch.await(5, TimeUnit.SECONDS)
        return if (done && code == 0) CecResult(true, "on", true) else CecResult(false, "unknown", false, "power.cec.failed")
    }

    override fun state(): String = "unknown"
}
