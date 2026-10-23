package app.vds.player

import android.app.Activity
import android.content.Intent
import android.os.Bundle
import android.text.InputType
import android.view.inputmethod.EditorInfo
import android.widget.Button
import android.widget.EditText
import android.widget.TextView
import app.vds.relay.RelayConfig
import java.net.HttpURLConnection
import java.net.URL
import kotlin.concurrent.thread

/** Premier lancement : saisie de l'URL serveur, vérifiée par `GET <url>/health`. */
class SetupActivity : Activity() {
    private lateinit var input: EditText
    private lateinit var status: TextView
    private lateinit var button: Button

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val root = Ui.column(this)
        root.addView(Ui.text(this, getString(R.string.setup_title), 28f))
        input = EditText(this).apply {
            hint = getString(R.string.setup_hint)
            setTextColor(Ui.FG)
            setHintTextColor(0x88E2E2E9.toInt())
            inputType = InputType.TYPE_TEXT_VARIATION_URI or InputType.TYPE_CLASS_TEXT
            imeOptions = EditorInfo.IME_ACTION_DONE
            setSingleLine()
            setOnEditorActionListener { _, _, _ -> submit(); true }
        }
        button = Button(this).apply {
            text = getString(R.string.setup_validate)
            setOnClickListener { submit() }
        }
        status = Ui.text(this, "", 18f)
        root.addView(input)
        root.addView(button)
        root.addView(status)
        setContentView(root)
        input.requestFocus()
    }

    @Deprecated("Retour neutralisé : aucune sortie du kiosque")
    override fun onBackPressed() {
    }

    private fun submit() {
        val cfg = RelayConfig.parse(input.text.toString(), BuildConfig.ALLOW_LAN_HTTP)
        if (cfg == null) {
            status.setText(R.string.setup_error_url)
            return
        }
        button.isEnabled = false
        status.setText(R.string.setup_checking)
        thread {
            val ok = reachable(cfg.url)
            runOnUiThread {
                if (ok) {
                    Prefs(this).serverUrl = cfg.url
                    startActivity(Intent(this, MainActivity::class.java).addFlags(Intent.FLAG_ACTIVITY_CLEAR_TOP))
                    finish()
                } else {
                    button.isEnabled = true
                    status.setText(R.string.setup_error_unreachable)
                }
            }
        }
    }

    private fun reachable(base: String): Boolean = try {
        (URL("$base/health").openConnection() as HttpURLConnection).run {
            connectTimeout = 8000
            readTimeout = 8000
            instanceFollowRedirects = false
            responseCode in 200..299
        }
    } catch (_: Exception) {
        false
    }
}
