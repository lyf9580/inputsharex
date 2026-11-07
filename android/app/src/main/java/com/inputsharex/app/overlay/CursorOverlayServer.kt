package com.inputsharex.app.overlay

import android.content.Context
import android.graphics.Canvas
import android.graphics.Color
import android.graphics.Paint
import android.graphics.Path
import android.graphics.PixelFormat
import android.graphics.Point
import java.net.ServerSocket
import java.net.InetAddress
import java.net.Socket
import android.os.Handler
import android.os.Looper
import android.provider.Settings
import android.view.Gravity
import android.view.View
import android.view.WindowManager
import java.io.DataInputStream
import java.io.DataOutputStream
import java.security.MessageDigest

/** Shell injector and visible cursor share this logical display coordinate space. */
class CursorOverlayServer(private val context: Context,
    private val onShown: (Int, Int, Int, Int) -> Unit = { _, _, _, _ -> }) : AutoCloseable {
    private val main = Handler(Looper.getMainLooper())
    private val wm = context.getSystemService(WindowManager::class.java)
    @Volatile private var token = ""
    @Volatile private var closed = false
    @Volatile private var peer: Socket? = null
    private val server = ServerSocket(27185, 4, InetAddress.getByName("127.0.0.1"))
    private var view: CursorView? = null
    private var position = Point()
    private var visible = false
    private var posted = false

    init {
        Thread({
            while (!closed) {
                val socket = try { server.accept() } catch (_: Exception) { break }
                peer = socket
                try { serve(socket) } catch (_: Exception) { }
                finally {
                    runCatching { socket.close() }
                    peer = null
                    update(0, 0, false)
                }
            }
        }, "absolute-cursor").apply { isDaemon = true; start() }
    }

    fun authorize(value: String) {
        if (!value.matches(Regex("[0-9a-f]{64}"))) return
        token = value
        prepare()
        runCatching { peer?.close() }
    }

    fun revoke() {
        token = ""
        runCatching { peer?.close() }
        update(0, 0, false)
    }

    @Suppress("DEPRECATION")
    private fun size(): Point = Point().also { wm.defaultDisplay.getRealSize(it) }

    private fun serve(socket: Socket) {
        // Loopback only; a fresh per-session token authorizes the shell injector.
        socket.tcpNoDelay = true
        socket.soTimeout = 3000
        val input = DataInputStream(socket.inputStream)
        val output = DataOutputStream(socket.outputStream)
        val supplied = ByteArray(64).also { input.readFully(it) }
        val expected = token
        if (expected.length != 64 || !MessageDigest.isEqual(supplied, expected.toByteArray(Charsets.US_ASCII)) ||
            !Settings.canDrawOverlays(context)) {
            output.writeByte(0); output.flush(); return
        }
        output.writeByte(1); output.flush()
        socket.tcpNoDelay = true
        socket.soTimeout = 30000
        while (!closed) {
            val x = input.readInt()
            val y = input.readInt()
            val width = input.readInt()
            val height = input.readInt()
            val show = input.readBoolean()
            val display = size()
            val px = (x.toDouble() * (display.x - 1) / (width - 1).coerceAtLeast(1)).toInt().coerceIn(0, display.x - 1)
            val py = (y.toDouble() * (display.y - 1) / (height - 1).coerceAtLeast(1)).toInt().coerceIn(0, display.y - 1)
            update(px, py, show)
            output.writeInt(px); output.writeInt(py)
            output.writeInt(display.x); output.writeInt(display.y)
            output.flush()
        }
    }

    private fun params(x: Int, y: Int) = WindowManager.LayoutParams(36, 48,
        WindowManager.LayoutParams.TYPE_APPLICATION_OVERLAY,
        WindowManager.LayoutParams.FLAG_NOT_FOCUSABLE or
            WindowManager.LayoutParams.FLAG_NOT_TOUCHABLE or
            WindowManager.LayoutParams.FLAG_LAYOUT_IN_SCREEN or
            WindowManager.LayoutParams.FLAG_LAYOUT_NO_LIMITS,
        PixelFormat.TRANSLUCENT).apply {
        gravity = Gravity.TOP or Gravity.LEFT
        this.x = x; this.y = y
        alpha = 0.75f
        title = "InputShareX cursor"
        if (android.os.Build.VERSION.SDK_INT >= 30) setFitInsetsTypes(0)
        if (android.os.Build.VERSION.SDK_INT >= 28) layoutInDisplayCutoutMode = 3
    }

    private fun prepare() {
        main.post {
            if (view == null && !closed && Settings.canDrawOverlays(context)) {
                runCatching {
                    val cursor = CursorView(context).apply { visibility = View.INVISIBLE }
                    wm.addView(cursor, params(0, 0)); view = cursor
                }
            }
        }
    }

    @Synchronized private fun update(x: Int, y: Int, show: Boolean) {
        if (position.x == x && position.y == y && visible == show) return
        position = Point(x, y); visible = show
        if (posted) return
        posted = true
        main.post {
            synchronized(this) {
                posted = false
                runCatching {
                    var cursor = view
                    if (closed) {
                        cursor?.let { wm.removeView(it) }; view = null
                    } else if (!visible) {
                        cursor?.visibility = View.INVISIBLE
                    } else {
                        val wasHidden = cursor == null || cursor.visibility != View.VISIBLE
                        if (cursor == null) {
                            cursor = CursorView(context)
                            wm.addView(cursor, params(position.x, position.y)); view = cursor
                        } else {
                            // Move the hidden window first, then reveal it at the new entry point.
                            wm.updateViewLayout(cursor, params(position.x, position.y))
                            cursor.visibility = View.VISIBLE
                        }
                        if (wasHidden) {
                            val display = size()
                            onShown(position.x, position.y, display.x, display.y)
                        }
                    }
                }
            }
        }
    }

    override fun close() {
        closed = true; revoke(); runCatching { server.close() }
        main.post { view?.let { runCatching { wm.removeView(it) } }; view = null }
    }

    private class CursorView(context: Context) : View(context) {
        init { setLayerType(View.LAYER_TYPE_SOFTWARE, null) }
        private val paint = Paint(Paint.ANTI_ALIAS_FLAG)
        private val arrow = Path().apply {
            moveTo(1f, 1f); lineTo(1f, 31f); lineTo(9f, 24f)
            lineTo(16f, 39f); lineTo(22f, 36f); lineTo(15f, 22f)
            lineTo(26f, 22f); close()
        }
        override fun onDraw(canvas: Canvas) {
            paint.setShadowLayer(1.5f, 0.75f, 1.25f, 0x55000000)
            paint.style = Paint.Style.FILL; paint.color = Color.WHITE; canvas.drawPath(arrow, paint)
            paint.clearShadowLayer()
            paint.style = Paint.Style.STROKE; paint.strokeWidth = 0.8f
            paint.strokeJoin = Paint.Join.ROUND; paint.color = 0xff7a7a7a.toInt()
            canvas.drawPath(arrow, paint)
        }
    }
}
