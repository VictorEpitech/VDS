package app.vds.player

import android.annotation.SuppressLint
import android.content.Context
import android.graphics.Color
import android.os.Handler
import android.view.KeyEvent
import android.view.MotionEvent
import android.view.View
import android.view.ViewGroup
import android.webkit.CookieManager
import android.webkit.GeolocationPermissions
import android.webkit.PermissionRequest
import android.webkit.RenderProcessGoneDetail
import android.webkit.ValueCallback
import android.webkit.WebChromeClient
import android.webkit.WebResourceError
import android.webkit.WebResourceRequest
import android.webkit.WebResourceResponse
import android.webkit.WebSettings
import android.webkit.WebView
import android.webkit.WebViewClient
import android.widget.FrameLayout
import app.vds.relay.WebHost
import app.vds.relay.WebRect
import app.vds.relay.WebUrlFilter

/**
 * WebViews natives de `content.url` (voir docs/architecture-content-url-natif.md), superposées à la WebView du
 * player. Aucune n'a de pont JS. Non focusables et sourdes au toucher/clavier (le player garde les touches).
 * Invisibles tant que la page principale n'est pas chargée sans erreur. Thread UI uniquement.
 */
class WebOverlay(private val context: Context, private val ui: Handler) : WebHost {
    private class Entry(val view: WebView) {
        var done: ((Boolean, String?) -> Unit)? = null
        var wantShow = false
        var ready = false
        var failed = false
        var timeout: Runnable? = null
        var lease: Runnable? = null
    }

    private var root: FrameLayout? = null
    private val entries = LinkedHashMap<String, Entry>()
    private var suppressed = false

    /** Appelé quand la racine change (null = plus de conteneur) : toute vue existante est détruite. */
    fun attach(container: FrameLayout?) {
        destroyAll()
        root = container
    }

    /** Kiosque déverrouillé / maintenance : l'interface d'administration du player doit rester visible. */
    fun setSuppressed(value: Boolean) {
        suppressed = value
        entries.values.forEach { refresh(it) }
    }

    override fun count() = entries.size
    override fun has(id: String) = entries.containsKey(id)

    @SuppressLint("SetJavaScriptEnabled")
    override fun prepare(id: String, url: String, rect: WebRect, done: (Boolean, String?) -> Unit) {
        val container = root
        if (container == null || !WebUrlFilter.allowed(url)) return done(false, "url_refused")
        val existing = entries[id]
        if (existing == null && entries.size >= MAX_VIEWS) return done(false, "url_refused")
        val e = existing ?: Entry(newView()).also {
            entries[id] = it
            container.addView(it.view, FrameLayout.LayoutParams(1, 1))
            it.view.visibility = View.INVISIBLE
        }
        e.done?.invoke(false, "url_native_error")
        e.done = done
        e.ready = false
        e.failed = false
        refresh(e)
        place(e, rect)
        renewLease(id, e)
        e.timeout?.let(ui::removeCallbacks)
        e.timeout = Runnable { fail(id, e, "url_timeout") }.also { ui.postDelayed(it, LOAD_TIMEOUT_MS) }
        e.view.webViewClient = client(id, e)
        e.view.clearCache(true)
        e.view.loadUrl(url)
    }

    override fun show(id: String) {
        val e = entries[id] ?: return
        e.wantShow = true
        renewLease(id, e)
        refresh(e)
    }

    override fun rect(id: String, rect: WebRect) {
        val e = entries[id] ?: return
        place(e, rect)
        renewLease(id, e)
    }

    override fun hide(id: String) = destroy(id)

    fun destroyAll() {
        entries.keys.toList().forEach(::destroy)
    }

    private fun refresh(e: Entry) {
        e.view.visibility = if (e.ready && e.wantShow && !suppressed) View.VISIBLE else View.INVISIBLE
    }

    private fun place(e: Entry, r: WebRect) {
        val d = context.resources.displayMetrics.density
        fun px(v: Int, min: Int) = (v * d).toInt().coerceIn(min, MAX_PX)
        e.view.layoutParams = FrameLayout.LayoutParams(px(r.w, 1), px(r.h, 1)).apply {
            leftMargin = px(r.x, -MAX_PX)
            topMargin = px(r.y, -MAX_PX)
        }
    }

    private fun renewLease(id: String, e: Entry) {
        e.lease?.let(ui::removeCallbacks)
        e.lease = Runnable { destroy(id) }.also { ui.postDelayed(it, LEASE_MS) }
    }

    private fun fail(id: String, e: Entry, code: String) {
        val cb = e.done
        e.done = null
        destroy(id)
        cb?.invoke(false, code)
    }

    private fun destroy(id: String) {
        val e = entries.remove(id) ?: return
        e.timeout?.let(ui::removeCallbacks)
        e.lease?.let(ui::removeCallbacks)
        val cb = e.done
        e.done = null
        e.view.apply {
            visibility = View.INVISIBLE
            webViewClient = WebViewClient()
            stopLoading()
            loadUrl("about:blank")
            (parent as? ViewGroup)?.removeView(this)
            destroy()
        }
        cb?.invoke(false, "url_native_error")
    }

    private fun client(id: String, e: Entry) = object : WebViewClient() {
        override fun shouldOverrideUrlLoading(view: WebView, request: WebResourceRequest): Boolean {
            if (!request.isForMainFrame) return false
            if (WebUrlFilter.allowed(request.url.toString())) return false
            ui.post { if (entries[id] === e) fail(id, e, "url_refused") }
            return true
        }

        override fun shouldInterceptRequest(view: WebView, request: WebResourceRequest): WebResourceResponse? =
            if (WebUrlFilter.allowed(request.url.toString())) null
            else WebResourceResponse("text/plain", "utf-8", 403, "Forbidden", emptyMap(), java.io.ByteArrayInputStream(ByteArray(0)))

        override fun onPageStarted(view: WebView, url: String, favicon: android.graphics.Bitmap?) {
            if (!WebUrlFilter.allowed(url)) ui.post { if (entries[id] === e) fail(id, e, "url_refused") }
        }

        override fun onPageFinished(view: WebView, url: String?) {
            val cb = e.done ?: return
            if (e.failed || entries[id] !== e || !WebUrlFilter.allowed(url)) return
            e.done = null
            e.timeout?.let(ui::removeCallbacks)
            e.ready = true
            refresh(e)
            cb(true, null)
        }

        override fun onReceivedError(view: WebView, request: WebResourceRequest, error: WebResourceError) {
            if (request.isForMainFrame) { e.failed = true; ui.post { if (entries[id] === e) fail(id, e, "url_native_error") } }
        }

        override fun onReceivedHttpError(view: WebView, request: WebResourceRequest, response: WebResourceResponse) {
            if (request.isForMainFrame) { e.failed = true; ui.post { if (entries[id] === e) fail(id, e, "url_native_error") } }
        }

        override fun onRenderProcessGone(view: WebView, detail: RenderProcessGoneDetail): Boolean {
            ui.post { destroyAll() }
            return true
        }
    }

    @SuppressLint("SetJavaScriptEnabled")
    private fun newView(): WebView {
        val view = object : WebView(context) {
            override fun dispatchTouchEvent(event: MotionEvent) = false
            override fun dispatchKeyEvent(event: KeyEvent) = false
            override fun onCheckIsTextEditor() = false
        }
        view.apply {
            setBackgroundColor(Color.BLACK)
            isFocusable = false
            isFocusableInTouchMode = false
            descendantFocusability = ViewGroup.FOCUS_BLOCK_DESCENDANTS
            isClickable = false
            isLongClickable = false
            setOnLongClickListener { true }
            isHapticFeedbackEnabled = false
            overScrollMode = View.OVER_SCROLL_NEVER
            isVerticalScrollBarEnabled = false
            isHorizontalScrollBarEnabled = false
            importantForAccessibility = View.IMPORTANT_FOR_ACCESSIBILITY_NO_HIDE_DESCENDANTS
            settings.apply {
                cacheMode = WebSettings.LOAD_NO_CACHE
                javaScriptEnabled = true
                domStorageEnabled = true
                allowFileAccess = false
                allowContentAccess = false
                mixedContentMode = WebSettings.MIXED_CONTENT_NEVER_ALLOW
                javaScriptCanOpenWindowsAutomatically = false
                setSupportMultipleWindows(false)
                mediaPlaybackRequiresUserGesture = true
                setGeolocationEnabled(false)
                setSupportZoom(false)
                builtInZoomControls = false
                safeBrowsingEnabled = false
            }
            setDownloadListener { _, _, _, _, _ -> }
            webChromeClient = object : WebChromeClient() {
                override fun onPermissionRequest(request: PermissionRequest) = request.deny()
                override fun onGeolocationPermissionsShowPrompt(origin: String?, callback: GeolocationPermissions.Callback) =
                    callback.invoke(origin, false, false)
                override fun onCreateWindow(view: WebView?, isDialog: Boolean, isUserGesture: Boolean, resultMsg: android.os.Message?) = false
                override fun onShowFileChooser(w: WebView?, cb: ValueCallback<Array<android.net.Uri>>?, p: FileChooserParams?): Boolean {
                    cb?.onReceiveValue(null)
                    return true
                }
                override fun onJsAlert(view: WebView?, url: String?, message: String?, result: android.webkit.JsResult): Boolean {
                    result.cancel()
                    return true
                }
                override fun onJsConfirm(view: WebView?, url: String?, message: String?, result: android.webkit.JsResult): Boolean {
                    result.cancel()
                    return true
                }
                override fun onJsPrompt(view: WebView?, url: String?, message: String?, defaultValue: String?, result: android.webkit.JsPromptResult): Boolean {
                    result.cancel()
                    return true
                }
            }
        }
        CookieManager.getInstance().setAcceptThirdPartyCookies(view, false)
        return view
    }

    companion object {
        const val MAX_VIEWS = 2
        const val LOAD_TIMEOUT_MS = 9_000L
        const val LEASE_MS = 30_000L
        private const val MAX_PX = 20_000
    }
}
