package app.vds.player

import android.app.Activity
import android.app.admin.DevicePolicyManager
import android.content.ComponentName
import android.content.Intent
import android.content.IntentFilter
import android.os.Handler
import app.vds.relay.Enforcement
import app.vds.relay.KioskActuator
import app.vds.relay.KioskMode
import app.vds.relay.KioskTimer

/**
 * Applique le kiosque demandé par le player (via [app.vds.relay.KioskDispatcher]).
 *
 * Device Owner : `LOCKED` = lock task + écran d'accueil persistant (`lock_task`) ; déverrouillé =
 * `stopLockTask` et HOME persistant retiré. Sinon kiosque doux (`soft`) : [locked] pilote le
 * Retour neutralisé et la relance automatique de [MainActivity]. Démarre verrouillé.
 */
class AndroidKiosk(private val activity: Activity, private val onRelocked: () -> Unit) : KioskActuator {
    @Volatile
    var locked: Boolean = true
        private set

    override fun apply(mode: KioskMode): Enforcement {
        val wasLocked = locked
        locked = mode == KioskMode.LOCKED
        val enforcement = enforce()
        if (locked && !wasLocked) onRelocked()
        return enforcement
    }

    /** Réapplique l'état courant (recréation de la WebView, retour au premier plan). */
    fun enforce(): Enforcement {
        val dpm = activity.getSystemService(DevicePolicyManager::class.java) ?: return Enforcement.SOFT
        if (!dpm.isDeviceOwnerApp(activity.packageName)) return Enforcement.SOFT
        val admin = ComponentName(activity, AdminReceiver::class.java)
        try {
            if (locked) {
                dpm.setLockTaskPackages(admin, arrayOf(activity.packageName))
                dpm.setLockTaskFeatures(admin, DevicePolicyManager.LOCK_TASK_FEATURE_NONE)
                dpm.addPersistentPreferredActivity(
                    admin,
                    IntentFilter(Intent.ACTION_MAIN).apply {
                        addCategory(Intent.CATEGORY_HOME)
                        addCategory(Intent.CATEGORY_DEFAULT)
                    },
                    ComponentName(activity, MainActivity::class.java),
                )
                activity.startLockTask()
            } else {
                dpm.clearPackagePersistentPreferredActivities(admin, activity.packageName)
                try {
                    activity.stopLockTask()
                } catch (_: IllegalStateException) {
                    // déjà hors lock task
                }
            }
        } catch (e: SecurityException) {
            if (!locked) throw e // un déverrouillage qui échoue est remonté (le dispatcher reste verrouillé)
        }
        return Enforcement.LOCK_TASK
    }
}

/** Reverrouillage autonome : minuterie sur le thread principal, indépendante de la page. */
class HandlerKioskTimer(private val handler: Handler) : KioskTimer {
    private var task: Runnable? = null

    override fun schedule(delayMs: Long, task: () -> Unit) {
        cancel()
        val r = Runnable { this.task = null; task() }
        this.task = r
        handler.postDelayed(r, delayMs)
    }

    override fun cancel() {
        task?.let(handler::removeCallbacks)
        task = null
    }
}
