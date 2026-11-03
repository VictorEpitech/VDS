package app.vds.player

import android.app.Activity
import android.app.admin.DevicePolicyManager
import android.content.ComponentName
import android.graphics.Bitmap
import android.os.Handler
import android.view.PixelCopy
import app.vds.relay.CmdActuator
import app.vds.relay.CmdScheduler
import java.io.ByteArrayOutputStream
import java.util.concurrent.Executors

/**
 * Commandes player côté Android (adaptateur `player.android`).
 * - Capture : `PixelCopy` de la fenêtre de l'activité (inclut la vidéo en `SurfaceView`, ce que `View.draw`
 *   ne rend pas). Rien n'est affiché au public. Fenêtre sans focus ou surface invalide : échec silencieux.
 * - `restart.app` : `Activity.recreate()` dans la même tâche, donc lock task conservé ; le verrouillage est
 *   de toute façon réappliqué par `onResume`/`enforceQuietly`.
 * - `restart.device` : `DevicePolicyManager.reboot()` seulement si l'app est Device Owner ; sinon la
 *   capacité n'est pas annoncée.
 */
class AndroidCmd(private val activity: Activity, private val ui: Handler) : CmdActuator {
    private val encoder = Executors.newSingleThreadExecutor { r -> Thread(r, "vds-shot").apply { isDaemon = true } }
    private val admin get() = ComponentName(activity, AdminReceiver::class.java)

    private fun deviceOwner(): Boolean =
        activity.getSystemService(DevicePolicyManager::class.java)?.isDeviceOwnerApp(activity.packageName) == true

    override val canScreenshot: Boolean get() = true // minSdk 28 >= 26 (PixelCopy.request(Window,...))
    override val canRestartApp: Boolean get() = true
    override val canRestartDevice: Boolean get() = deviceOwner()

    override fun screenshot(maxEdge: Int, quality: Int, done: (Result<ByteArray>) -> Unit) {
        ui.post {
            try {
                val w = activity.window
                val v = w.decorView
                if (v.width <= 0 || v.height <= 0 || !activity.hasWindowFocus()) {
                    return@post done(Result.failure(IllegalStateException("window")))
                }
                val scale = minOf(1f, maxEdge.toFloat() / maxOf(v.width, v.height))
                val bmp = Bitmap.createBitmap(
                    maxOf(1, (v.width * scale).toInt()), maxOf(1, (v.height * scale).toInt()), Bitmap.Config.ARGB_8888,
                )
                PixelCopy.request(w, bmp, { code ->
                    if (code != PixelCopy.SUCCESS) {
                        bmp.recycle()
                        return@request done(Result.failure(IllegalStateException("pixelcopy")))
                    }
                    encoder.execute {
                        done(runCatching {
                            ByteArrayOutputStream().use { out ->
                                check(bmp.compress(Bitmap.CompressFormat.JPEG, quality, out))
                                out.toByteArray()
                            }
                        })
                        bmp.recycle()
                    }
                }, ui)
            } catch (e: Exception) {
                done(Result.failure(e))
            }
        }
    }

    override fun restartApp() {
        ui.post { if (!activity.isFinishing && !activity.isDestroyed) activity.recreate() }
    }

    override fun restartDevice() {
        val dpm = activity.getSystemService(DevicePolicyManager::class.java)
        check(dpm != null && dpm.isDeviceOwnerApp(activity.packageName)) { "not device owner" }
        dpm.reboot(admin)
    }
}

class HandlerCmdScheduler(private val handler: Handler) : CmdScheduler {
    override fun postDelayed(delayMs: Long, task: () -> Unit) {
        handler.postDelayed(task, delayMs)
    }
}
