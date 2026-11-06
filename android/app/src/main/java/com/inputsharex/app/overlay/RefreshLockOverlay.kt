package com.inputsharex.app.overlay

import android.content.Context
import android.graphics.Canvas
import android.graphics.Color
import android.graphics.PixelFormat
import android.hardware.display.DisplayManager
import android.os.Build
import android.os.SystemClock
import android.util.Log
import android.view.Display
import android.view.Gravity
import android.view.Surface
import android.view.SurfaceHolder
import android.view.SurfaceView
import android.view.WindowManager
import java.util.concurrent.atomic.AtomicBoolean

/**
 * Tunable parameters for the "lock the panel at a high refresh rate" experiment.
 *
 * Kept data-only so the desktop can later drive it through the link config and so the
 * experiment harness (adb intents) can sweep combinations without rebuilding the APK.
 */
data class RefreshConfig(
    val frameRate: Float = 120f,
    /** Surface.FRAME_RATE_COMPATIBILITY_DEFAULT(0) / NONE(1) / FIXED(2). */
    val compatibility: Int = 0,
    /** Surface.CHANGE_FRAME_RATE_ONLY_IF_SEAMLESS(0) / CHANGE_FRAME_RATE_ALWAYS(1). */
    val strategy: Int = 1,
    /** Optional WindowManager.LayoutParams.preferredDisplayModeId (-1 = do not set). */
    val preferredModeId: Int = -1,
    val width: Int = 1,
    val height: Int = 1,
    /** Window alpha; must stay > 0, some compositors skip fully transparent overlays. */
    val alpha: Float = 0.01f,
    /** Keep posting frames; setFrameRate alone is sometimes ignored without a buffer producer. */
    val drawLoop: Boolean = true,
    val gravity: Int = Gravity.TOP or Gravity.START,
    val x: Int = 0,
    val y: Int = 0,
) {
    fun describe(): String =
        "size=${width}x${height} alpha=$alpha rate=$frameRate compat=$compatibility " +
            "strategy=$strategy mode=$preferredModeId draw=$drawLoop gravity=$gravity"
}

/**
 * EXPERIMENTAL: a 1x1 TYPE_APPLICATION_OVERLAY window holding a SurfaceView whose Surface
 * votes for a high frame rate via [Surface.setFrameRate] while it keeps posting buffers.
 *
 * The idea: SurfaceFlinger composes the mouse cursor at the panel refresh rate, so if an
 * app (e.g. a 50fps video app) drags the display down to 50Hz the pointer feels laggy.
 * A surface asking for 120Hz "always" may pull the whole display back up.
 */
object RefreshLockOverlay {
    private const val TAG = "RefreshLock"

    @Volatile
    var showing: Boolean = false
        private set

    @Volatile
    var lastConfig: RefreshConfig? = null
        private set

    private var windowManager: WindowManager? = null
    private var surfaceView: SurfaceView? = null
    private var drawThread: Thread? = null
    private val running = AtomicBoolean(false)
    private var appliedRate: Float = 0f
    private var framesPosted: Long = 0

    /** Every supported display mode of the default display, for the experiment log. */
    fun describeDisplay(context: Context): String {
        val dm = context.getSystemService(DisplayManager::class.java) ?: return "no display service"
        val display = dm.getDisplay(Display.DEFAULT_DISPLAY) ?: return "no default display"
        val current = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.M) display.mode else null
        val modes = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.M) {
            display.supportedModes.joinToString(separator = ", ") { m ->
                "id=${m.modeId}:${m.refreshRate}Hz"
            }
        } else {
            "unknown"
        }
        return "current=${current?.modeId}:${current?.refreshRate}Hz modes=[$modes]"
    }

    /** Highest mode id with a refresh rate close to [target] (e.g. 120f). 0 when unknown. */
    fun pickModeId(context: Context, target: Float): Int {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.M) return 0
        val dm = context.getSystemService(DisplayManager::class.java) ?: return 0
        val display = dm.getDisplay(Display.DEFAULT_DISPLAY) ?: return 0
        val best = display.supportedModes
            .filter { Math.abs(it.refreshRate - target) < 1f }
            .maxByOrNull { it.modeId }
        return best?.modeId ?: 0
    }

    /** Keep a live surface vote without producing frames faster than the requested rate. */
    fun defaultConfig(context: Context): RefreshConfig = RefreshConfig(
        frameRate = 120f,
        preferredModeId = -1,
        // 2026-11-06 实测（Xiaomi Pad 7 Pro / MIUI）：只要有一个有效的 Surface 调用
        // Surface.setFrameRate(120, CHANGED_ALWAYS)，面板空闲时也会保持 120Hz，
        // SurfaceFlinger 约 1–2% CPU；而每帧 lockCanvas 投帧会让整屏走 GPU 合成，
        // SurfaceFlinger 升到 60%，整机反而卡顿。因此默认只投票、不产帧。
        drawLoop = false,
    )

    fun show(context: Context, config: RefreshConfig): Boolean {
        stop()
        if (!android.provider.Settings.canDrawOverlays(context)) {
            Log.w(TAG, "SYSTEM_ALERT_WINDOW not granted, overlay skipped")
            return false
        }
        val wm = context.getSystemService(WindowManager::class.java)
        if (wm == null) {
            Log.e(TAG, "no WindowManager")
            return false
        }
        val view = SurfaceView(context)
        val params = WindowManager.LayoutParams(
            config.width.coerceAtLeast(1),
            config.height.coerceAtLeast(1),
            WindowManager.LayoutParams.TYPE_APPLICATION_OVERLAY,
            WindowManager.LayoutParams.FLAG_NOT_FOCUSABLE or
                WindowManager.LayoutParams.FLAG_NOT_TOUCHABLE or
                WindowManager.LayoutParams.FLAG_LAYOUT_IN_SCREEN or
                WindowManager.LayoutParams.FLAG_LAYOUT_NO_LIMITS,
            PixelFormat.TRANSLUCENT,
        ).apply {
            gravity = config.gravity
            x = config.x
            y = config.y
            alpha = config.alpha.coerceIn(0.01f, 1f)
            if (config.preferredModeId >= 0 && Build.VERSION.SDK_INT >= Build.VERSION_CODES.M) {
                preferredDisplayModeId = config.preferredModeId
            }
        }
        view.holder.addCallback(object : SurfaceHolder.Callback {
            override fun surfaceCreated(holder: SurfaceHolder) {
                Log.i(TAG, "surface created ${holder.surface}")
                applyFrameRate(holder.surface, config)
            }

            override fun surfaceChanged(holder: SurfaceHolder, format: Int, width: Int, height: Int) = Unit

            override fun surfaceDestroyed(holder: SurfaceHolder) {
                Log.i(TAG, "surface destroyed")
            }
        })
        return try {
            wm.addView(view, params)
            windowManager = wm
            surfaceView = view
            lastConfig = config
            showing = true
            framesPosted = 0
            Log.i(TAG, "overlay added ${config.describe()}")
            Log.i(TAG, "display before ${describeDisplay(context)}")
            startDrawLoop(view, config)
            true
        } catch (t: Throwable) {
            Log.e(TAG, "addView failed: $t")
            runCatching { wm.removeView(view) }
            false
        }
    }

    fun stop() {
        running.set(false)
        drawThread?.let { t ->
            runCatching { t.join(500) }
        }
        drawThread = null
        surfaceView?.let { view ->
            runCatching { windowManager?.removeView(view) }
        }
        surfaceView = null
        windowManager = null
        if (showing) {
            Log.i(TAG, "overlay removed frames=$framesPosted rate=$appliedRate")
        }
        showing = false
        lastConfig = null
        framesPosted = 0
    }

    private fun applyFrameRate(surface: Surface, config: RefreshConfig) {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.R) {
            Log.w(TAG, "setFrameRate requires API 30, device is ${Build.VERSION.SDK_INT}")
            return
        }
        try {
            surface.setFrameRate(config.frameRate, config.compatibility, config.strategy)
            appliedRate = config.frameRate
            Log.i(
                TAG,
                "setFrameRate(${config.frameRate}, compat=${config.compatibility}, " +
                    "strategy=${config.strategy}) ok",
            )
        } catch (t: Throwable) {
            Log.e(TAG, "setFrameRate failed: $t")
        }
    }

    private fun startDrawLoop(view: SurfaceView, config: RefreshConfig) {
        running.set(true)
        val thread = Thread({
            val holder = view.holder
            var lastApply = 0L
            // 不投帧时也先画一帧，确保 Surface 有有效 buffer（部分设备上无 buffer 的
            // 图层不会参与帧率投票）。
            var pendingFirstFrame = !config.drawLoop
            val frameInterval = (1000f / config.frameRate.coerceIn(1f, 144f)).toLong().coerceAtLeast(1L)
            while (running.get()) {
                val surface = runCatching { holder.surface }.getOrNull()
                if (surface == null || !surface.isValid) {
                    SystemClock.sleep(20)
                    continue
                }
                val now = SystemClock.uptimeMillis()
                if (now - lastApply >= 1000L) {
                    applyFrameRate(surface, config)
                    lastApply = now
                }
                if (config.drawLoop || pendingFirstFrame) {
                    pendingFirstFrame = false
                    var canvas: Canvas? = null
                    try {
                        canvas = holder.lockCanvas()
                        if (canvas != null) {
                            canvas.drawColor(Color.BLACK)
                        }
                    } catch (t: Throwable) {
                        // surface teardown race, ignore
                    } finally {
                        if (canvas != null) {
                            runCatching { holder.unlockCanvasAndPost(canvas) }
                            framesPosted++
                        }
                    }
                }
                // Without a draw loop there is nothing to do between the 1 s re-applies.
                val elapsed = SystemClock.uptimeMillis() - now
                SystemClock.sleep(if (config.drawLoop) (frameInterval - elapsed).coerceAtLeast(1L) else 250)
            }
            Log.i(TAG, "draw loop ended frames=$framesPosted")
        }, "refresh-overlay")
        thread.isDaemon = true
        thread.start()
        drawThread = thread
    }
}
