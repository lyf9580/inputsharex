package com.inputsharex.app.overlay

import android.content.Context
import android.graphics.Color
import android.graphics.PixelFormat
import android.provider.Settings
import android.view.Gravity
import android.view.View
import android.view.WindowManager
import com.inputsharex.app.data.Edge

/**
 * A one-side thin accent line drawn through TYPE_APPLICATION_OVERLAY so the user can see which
 * edge of the tablet currently acts as the crossing border for keyboard/mouse sharing.
 */
object EdgeOverlay {
    private const val LINE_COLOR = 0xCC3B82F6.toInt()
    private var view: View? = null

    fun isAllowed(context: Context): Boolean = Settings.canDrawOverlays(context)

    fun show(context: Context, edge: Edge) {
        if (!isAllowed(context)) return
        val app = context.applicationContext
        hide(app)
        val wm = app.getSystemService(Context.WINDOW_SERVICE) as WindowManager
        val density = app.resources.displayMetrics.density
        val thickness = (5 * density).toInt().coerceAtLeast(3)
        val line = View(app).apply { setBackgroundColor(LINE_COLOR) }
        val flags = WindowManager.LayoutParams.FLAG_NOT_FOCUSABLE or
            WindowManager.LayoutParams.FLAG_NOT_TOUCHABLE or
            WindowManager.LayoutParams.FLAG_LAYOUT_NO_LIMITS
        val params = WindowManager.LayoutParams(
            thickness,
            thickness,
            WindowManager.LayoutParams.TYPE_APPLICATION_OVERLAY,
            flags,
            PixelFormat.TRANSLUCENT,
        )
        when (edge) {
            Edge.RIGHT -> {
                params.gravity = Gravity.END or Gravity.CENTER_VERTICAL
                params.height = WindowManager.LayoutParams.MATCH_PARENT
            }

            Edge.LEFT -> {
                params.gravity = Gravity.START or Gravity.CENTER_VERTICAL
                params.height = WindowManager.LayoutParams.MATCH_PARENT
            }

            Edge.TOP -> {
                params.gravity = Gravity.TOP or Gravity.CENTER_HORIZONTAL
                params.width = WindowManager.LayoutParams.MATCH_PARENT
            }

            Edge.BOTTOM -> {
                params.gravity = Gravity.BOTTOM or Gravity.CENTER_HORIZONTAL
                params.width = WindowManager.LayoutParams.MATCH_PARENT
            }
        }
        runCatching {
            wm.addView(line, params)
            view = line
        }
    }

    fun hide(context: Context) {
        val current = view ?: return
        val wm = context.applicationContext.getSystemService(Context.WINDOW_SERVICE) as WindowManager
        runCatching { wm.removeView(current) }
        view = null
    }

    fun refresh(context: Context, edge: Edge, enabled: Boolean) {
        if (enabled && isAllowed(context)) {
            show(context, edge)
        } else {
            hide(context)
        }
    }
}
