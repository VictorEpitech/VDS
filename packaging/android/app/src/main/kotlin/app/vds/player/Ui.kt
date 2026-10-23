package app.vds.player

import android.content.Context
import android.graphics.Typeface
import android.view.Gravity
import android.view.ViewGroup
import android.widget.LinearLayout
import android.widget.TextView

/** Écrans natifs sombres (charte V-Tech : fond sombre, pas d'ombre, mono réservée aux données techniques). */
object Ui {
    const val BG = 0xFF0E1116.toInt()
    const val FG = 0xFFE2E2E9.toInt()
    const val ACCENT = 0xFFB4C5FF.toInt()

    fun column(context: Context): LinearLayout = LinearLayout(context).apply {
        orientation = LinearLayout.VERTICAL
        gravity = Gravity.CENTER
        setBackgroundColor(BG)
        setPadding(64, 64, 64, 64)
        layoutParams = ViewGroup.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT)
    }

    fun text(context: Context, value: CharSequence, sizeSp: Float = 22f, mono: Boolean = false): TextView =
        TextView(context).apply {
            text = value
            textSize = sizeSp
            setTextColor(FG)
            gravity = Gravity.CENTER
            if (mono) typeface = Typeface.MONOSPACE
        }

    /** Écran de maintenance natif : titre localisé et code seul. */
    fun maintenance(context: Context, code: String): LinearLayout = column(context).apply {
        addView(text(context, context.getString(R.string.maintenance_title), 28f))
        addView(
            text(context, "${context.getString(R.string.maintenance_code_label)} $code", 20f, mono = true).apply {
                setTextColor(ACCENT)
                setPadding(0, 24, 0, 0)
            },
        )
    }
}
