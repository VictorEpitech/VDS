package app.vds.player

import android.app.Activity
import android.app.ActivityManager
import android.app.admin.DevicePolicyManager
import android.content.ComponentName
import android.content.Intent
import android.content.IntentFilter
import android.os.Handler
import android.os.UserManager
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
 *
 * Restrictions Device Owner (surface ADB et horloge) : `DISALLOW_DEBUGGING_FEATURES`,
 * `DISALLOW_CONFIG_DATE_TIME` (+ date automatique), `DISALLOW_SAFE_BOOT`, `DISALLOW_FACTORY_RESET`
 * posées dès que l'état n'est pas `MAINTENANCE` ; les deux premières ne sont levées que pendant une
 * maintenance bornée et rétablies au reverrouillage. [relaxed] (build `-Pvds.kioskRelaxed=true`)
 * ne pose jamais les restrictions de débogage et d'horloge (poste de recette uniquement).
 *
 * [enforce] LÈVE tant que l'état verrouillé n'est pas confirmé (lock task actif) : le dispatcher
 * retente, et ne renvoie `lock_task` qu'après confirmation par `ActivityManager.lockTaskModeState`.
 */
class AndroidKiosk(
    private val activity: Activity,
    private val relaxed: Boolean = false,
    private val onRelocked: () -> Unit,
) : KioskActuator {
    @Volatile
    var locked: Boolean = true
        private set

    @Volatile
    private var maintenance: Boolean = false

    override fun apply(mode: KioskMode): Enforcement {
        val wasLocked = locked
        locked = mode == KioskMode.LOCKED
        maintenance = mode == KioskMode.MAINTENANCE
        try {
            return enforce()
        } finally {
            if (locked && !wasLocked) onRelocked()
        }
    }

    /** Comme [enforce] sans lever (démarrage, retour au premier plan) : le dispatcher retentera. */
    fun enforceQuietly() {
        try {
            enforce()
        } catch (_: Exception) {
            // non confirmé : nouvel essai par le minuteur du dispatcher et au prochain retour au premier plan
        }
    }

    private fun lockTaskActive(): Boolean {
        val am = activity.getSystemService(ActivityManager::class.java) ?: return false
        return am.lockTaskModeState == ActivityManager.LOCK_TASK_MODE_LOCKED
    }

    /** Réapplique l'état courant (recréation de la WebView, retour au premier plan). */
    @Suppress("DEPRECATION")
    fun enforce(): Enforcement {
        val dpm = activity.getSystemService(DevicePolicyManager::class.java) ?: return Enforcement.SOFT
        if (!dpm.isDeviceOwnerApp(activity.packageName)) return Enforcement.SOFT
        val admin = ComponentName(activity, AdminReceiver::class.java)
        restrict(dpm, admin, DISALLOW_ALWAYS, true)
        restrict(dpm, admin, DISALLOW_OUTSIDE_MAINTENANCE, !maintenance && !relaxed)
        if (!relaxed) dpm.setAutoTimeRequired(admin, !maintenance)
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
            if (!lockTaskActive()) {
                activity.startLockTask() // hors premier plan : IllegalArgument/IllegalState, remonté (retenté)
                check(lockTaskActive()) { "lock task not confirmed" }
            }
        } else {
            dpm.clearPackagePersistentPreferredActivities(admin, activity.packageName)
            try {
                activity.stopLockTask()
            } catch (_: IllegalStateException) {
                // déjà hors lock task
            }
        }
        return Enforcement.LOCK_TASK
    }

    private fun restrict(dpm: DevicePolicyManager, admin: ComponentName, keys: List<String>, on: Boolean) {
        for (k in keys) if (on) dpm.addUserRestriction(admin, k) else dpm.clearUserRestriction(admin, k)
    }

    private companion object {
        val DISALLOW_ALWAYS = listOf(UserManager.DISALLOW_SAFE_BOOT, UserManager.DISALLOW_FACTORY_RESET)
        val DISALLOW_OUTSIDE_MAINTENANCE = listOf(UserManager.DISALLOW_DEBUGGING_FEATURES, UserManager.DISALLOW_CONFIG_DATE_TIME)
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
