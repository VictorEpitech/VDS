package app.vds.player

import android.annotation.SuppressLint
import android.app.Activity
import android.app.admin.DevicePolicyManager
import android.content.ComponentName
import android.content.Intent
import android.content.IntentFilter
import android.graphics.Color
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.os.SystemClock
import android.view.View
import android.view.WindowManager
import android.webkit.CookieManager
import android.webkit.RenderProcessGoneDetail
import android.webkit.WebResourceError
import android.webkit.WebResourceRequest
import android.webkit.WebSettings
import android.webkit.WebStorage
import android.webkit.WebView
import android.webkit.WebViewClient
import android.content.RestrictionsManager
import androidx.webkit.WebViewCompat
import androidx.webkit.WebViewFeature
import app.vds.relay.CecDispatcher
import app.vds.relay.Relay
import app.vds.relay.RelayConfig
import kotlin.concurrent.thread

/**
 * Kiosque : une WebView plein écran sur le player servi par le relais en boucle. Aucune sortie :
 * Retour neutralisé, navigation limitée à l'origine locale, aucun message technique (hors écran
 * natif de maintenance `E_RELAY_PORT`).
 */
class MainActivity : Activity() {
    private val ui = Handler(Looper.getMainLooper())
    private lateinit var prefs: Prefs
    private var cfg: RelayConfig? = null
    private var web: WebView? = null
    private var purgePending = false
    private var bridgeInstalled = false

    @Volatile
    private var currentUrl: String = ""

    private val bridge = VdsBridge(
        trusted = { cfg?.let { currentUrl.startsWith("${it.localOrigin(Relay.DEFAULT_PORT)}${it.basePath}/player/") } == true },
        onReload = { runOnUiThread { reloadPlayer() } },
        cec = CecDispatcher(AndroidCec(this)),
    )

    private val watchdog = object : Runnable {
        override fun run() {
            val c = cfg
            if (bridgeInstalled && web != null && c != null && currentUrl.startsWith(c.playerUrl(Relay.DEFAULT_PORT)) &&
                SystemClock.elapsedRealtime() - bridge.lastPing > PING_TIMEOUT_MS
            ) {
                reloadPlayer()
            }
            ui.postDelayed(this, WATCHDOG_MS)
        }
    }

    private val relaunch = Runnable {
        startActivity(
            Intent(this, MainActivity::class.java)
                .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_REORDER_TO_FRONT),
        )
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        window.addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)
        prefs = Prefs(this)
        applyRestrictions()
        bootstrap()
        ui.postDelayed(watchdog, WATCHDOG_MS)
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        setIntent(intent)
        // L'URL ne vient jamais de l'intent reçu (une app tierce pourrait le forger) : ProvisionActivity
        // (ADB) ou la configuration gérée l'écrivent en Prefs, on relit simplement.
        applyRestrictions()
        if (prefs.config()?.url != cfg?.url) bootstrap()
    }

    /** Configuration gérée (Device Owner / MDM) : clé `server_url`, prioritaire sur la saisie locale. */
    private fun applyRestrictions() {
        val rm = getSystemService(RestrictionsManager::class.java) ?: return
        val raw = rm.applicationRestrictions?.getString(EXTRA_SERVER_URL) ?: return
        val parsed = RelayConfig.parse(raw, BuildConfig.ALLOW_LAN_HTTP) ?: return
        if (parsed.url != prefs.serverUrl) prefs.serverUrl = parsed.url
    }

    private fun bootstrap() {
        ui.removeCallbacks(retryBootstrap)
        val c = prefs.config()
        if (c == null) {
            startActivity(Intent(this, SetupActivity::class.java))
            return
        }
        cfg = c
        enterKiosk()
        setContentView(Ui.column(this).apply { addView(Ui.text(this@MainActivity, getString(R.string.loading))) })
        web = null
        thread {
            val ok = (application as VdsApp).ensureRelay(c)
            runOnUiThread {
                if (isDestroyed) return@runOnUiThread
                if (ok) {
                    showWeb(c)
                } else {
                    setContentView(Ui.maintenance(this, (application as VdsApp).relayError ?: VdsApp.ERR_RELAY_PORT))
                    ui.postDelayed(retryBootstrap, RETRY_RELAY_MS)
                }
            }
        }
    }

    private val retryBootstrap = Runnable { bootstrap() }

    @SuppressLint("SetJavaScriptEnabled")
    private fun showWeb(c: RelayConfig) {
        web?.destroy()
        val view = WebView(this)
        web = view
        view.apply {
            setBackgroundColor(Color.BLACK)
            isLongClickable = false
            setOnLongClickListener { true }
            isHapticFeedbackEnabled = false
            overScrollMode = View.OVER_SCROLL_NEVER
            isVerticalScrollBarEnabled = false
            isHorizontalScrollBarEnabled = false
            settings.apply {
                javaScriptEnabled = true
                domStorageEnabled = true
                mediaPlaybackRequiresUserGesture = false
                allowFileAccess = false
                allowContentAccess = false
                javaScriptCanOpenWindowsAutomatically = false
                setSupportMultipleWindows(false)
                setSupportZoom(false)
                builtInZoomControls = false
                cacheMode = WebSettings.LOAD_DEFAULT
                safeBrowsingEnabled = false
            }
            installBridge(this, c)
            webViewClient = KioskClient(c)
        }
        setContentView(view)
        bridge.resetPing()
        if (prefs.needsPurge(c)) {
            // Même origine locale pour toute URL serveur : on efface le stockage de l'ancienne.
            purgePending = true
            WebStorage.getInstance().deleteAllData()
            CookieManager.getInstance().removeAllCookies(null)
            view.clearCache(true)
            view.loadUrl("${c.localOrigin(Relay.DEFAULT_PORT)}${Relay.PURGE_PATH}")
        } else {
            view.loadUrl(c.playerUrl(Relay.DEFAULT_PORT))
        }
    }

    /** Pont limité à la frame principale d'origine locale ; sans les API WebView requises, pas de pont (et pas de watchdog). */
    private fun installBridge(view: WebView, c: RelayConfig) {
        bridgeInstalled = false
        if (!WebViewFeature.isFeatureSupported(WebViewFeature.WEB_MESSAGE_LISTENER) ||
            !WebViewFeature.isFeatureSupported(WebViewFeature.DOCUMENT_START_SCRIPT)
        ) return
        val rules = setOf(c.localOrigin(Relay.DEFAULT_PORT))
        WebViewCompat.addWebMessageListener(view, VdsBridge.LISTENER_NAME, rules) { _, message, _, isMainFrame, replyProxy ->
            bridge.onMessage(isMainFrame, message.data) { replyProxy.postMessage(it) }
        }
        WebViewCompat.addDocumentStartJavaScript(view, VdsBridge.script(VdsBridge.infoJson(c.insecureUpstream)), rules)
        bridgeInstalled = true
    }

    private fun reloadPlayer() {
        val c = cfg ?: return
        web?.loadUrl(c.playerUrl(Relay.DEFAULT_PORT))
        bridge.resetPing()
    }

    private inner class KioskClient(private val c: RelayConfig) : WebViewClient() {
        private val port = Relay.DEFAULT_PORT

        override fun shouldOverrideUrlLoading(view: WebView, request: WebResourceRequest): Boolean {
            if (!request.isForMainFrame) return false
            val url = request.url.toString()
            val allowed = url.startsWith(c.allowedPrefix(port)) ||
                url == "${c.localOrigin(port)}${c.basePath}" ||
                url.startsWith("${c.localOrigin(port)}${Relay.PURGE_PATH}")
            return !allowed
        }

        override fun onPageStarted(view: WebView, url: String, favicon: android.graphics.Bitmap?) {
            currentUrl = url
            if (purgePending && url.startsWith(c.playerUrl(port))) {
                purgePending = false
                prefs.webStorageFor = c.url
            }
            bridge.resetPing()
        }

        override fun onReceivedError(view: WebView, request: WebResourceRequest, error: WebResourceError) {
            if (request.isForMainFrame) {
                currentUrl = ""
                ui.postDelayed({ if (web === view) reloadPlayer() }, RETRY_PAGE_MS)
            }
        }

        override fun onRenderProcessGone(view: WebView, detail: RenderProcessGoneDetail): Boolean {
            if (web === view) showWeb(c)
            return true
        }
    }

    private fun enterKiosk() {
        val dpm = getSystemService(DevicePolicyManager::class.java) ?: return
        if (!dpm.isDeviceOwnerApp(packageName)) return // kiosque doux : voir manifeste (HOME) et onStop
        try {
            val admin = ComponentName(this, AdminReceiver::class.java)
            dpm.setLockTaskPackages(admin, arrayOf(packageName))
            dpm.setLockTaskFeatures(admin, DevicePolicyManager.LOCK_TASK_FEATURE_NONE)
            dpm.addPersistentPreferredActivity(
                admin,
                IntentFilter(Intent.ACTION_MAIN).apply {
                    addCategory(Intent.CATEGORY_HOME)
                    addCategory(Intent.CATEGORY_DEFAULT)
                },
                ComponentName(this, MainActivity::class.java),
            )
            startLockTask()
        } catch (_: SecurityException) {
        }
    }

    @Suppress("DEPRECATION")
    override fun onWindowFocusChanged(hasFocus: Boolean) {
        super.onWindowFocusChanged(hasFocus)
        if (hasFocus) {
            window.decorView.systemUiVisibility = View.SYSTEM_UI_FLAG_IMMERSIVE_STICKY or
                View.SYSTEM_UI_FLAG_FULLSCREEN or View.SYSTEM_UI_FLAG_HIDE_NAVIGATION or
                View.SYSTEM_UI_FLAG_LAYOUT_STABLE or View.SYSTEM_UI_FLAG_LAYOUT_FULLSCREEN or
                View.SYSTEM_UI_FLAG_LAYOUT_HIDE_NAVIGATION
        }
    }

    @Deprecated("Retour neutralisé : aucune sortie du kiosque")
    override fun onBackPressed() {
    }

    override fun onStart() {
        super.onStart()
        ui.removeCallbacks(relaunch)
    }

    /** Kiosque doux : si l'app quitte le premier plan (sans que ce soit l'écran de saisie), on la ramène. */
    override fun onStop() {
        super.onStop()
        if (!isFinishing && prefs.config() != null) ui.postDelayed(relaunch, RELAUNCH_MS)
    }

    override fun onDestroy() {
        ui.removeCallbacksAndMessages(null)
        web?.destroy()
        web = null
        super.onDestroy()
    }

    private companion object {
        const val EXTRA_SERVER_URL = "server_url"
        const val WATCHDOG_MS = 15_000L
        const val PING_TIMEOUT_MS = 90_000L
        const val RETRY_PAGE_MS = 5_000L
        const val RETRY_RELAY_MS = 30_000L
        const val RELAUNCH_MS = 1_500L
    }
}
