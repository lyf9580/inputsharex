package com.inputsharex.app.link

import android.os.Build
import android.util.Log
import com.inputsharex.app.data.Settings
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.currentCoroutineContext
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import org.json.JSONObject
import java.io.BufferedReader
import java.io.BufferedWriter
import java.io.InputStreamReader
import java.io.OutputStreamWriter
import java.net.InetSocketAddress
import java.net.Socket
import java.util.concurrent.atomic.AtomicLong

enum class LinkStatus { DISCONNECTED, CONNECTING, CONNECTED }

data class LinkState(
    val status: LinkStatus = LinkStatus.DISCONNECTED,
    val desktopName: String = "",
    val desktopVersion: Int = 0,
    val rttMs: Int = -1,
    val bytesPerSec: Int = 0,
    val sharing: Boolean = false,
    val inputArmed: Boolean = false,
    val sharingPhase: String = "connected",
    val transportLabel: String = "",
    val captureGeneration: Long = 0L,
    val placement: Int = 0,
) {
    val connected: Boolean get() = status == LinkStatus.CONNECTED
}

/**
 * TCP client for the desktop host (adb reverse tcp:27184 tcp:27184).
 *
 * 连接由电脑端发起：电脑通过 adb 拉起 MainActivity 并携带 inputsharex_connect 标记，
 * App 收到后调用 [requestConnect] 做一次连接尝试。尝试失败不会自动重试，
 * 界面保持「等待电脑连接」，避免没有电脑时不断重连并刷出红字错误。
 */
class LinkClient(
    private val scope: CoroutineScope,
    private val settingsProvider: () -> Settings,
    private val cursorSetup: (String) -> Unit = {},
    private val displaySize: () -> Pair<Int, Int> = { 0 to 0 },
) {
    companion object {
        private const val TAG = "LinkClient"
        private const val CONNECT_TIMEOUT_MS = 2500
        private const val PING_INTERVAL_MS = 500L
    }

    private val _state = MutableStateFlow(LinkState())
    val state: StateFlow<LinkState> = _state.asStateFlow()

    private val _desktopConfig = MutableStateFlow<DesktopConfig?>(null)
    val desktopConfig: StateFlow<DesktopConfig?> = _desktopConfig.asStateFlow()

    private var connectJob: Job? = null
    private var socket: Socket? = null
    private var writer: BufferedWriter? = null
    private val rxBytes = AtomicLong(0L)

    /** 电脑端请求连接时调用；同一时间只允许一个尝试在跑，失败后不重试。 */
    fun requestConnect() {
        if (connectJob?.isActive == true) return
        connectJob = scope.launch(Dispatchers.IO) { connectAttempt() }
    }

    fun stop() {
        connectJob?.cancel()
        connectJob = null
        closeSocket()
        _state.value = LinkState()
    }

    fun pushConfig(settings: Settings) {
        sendFrame(Frames.config(settings.edge.ordinal xor 1, settings.keepAwake, settings.lockRefresh, settings.autoShare))
    }

    fun requestSharing(enabled: Boolean) {
        scope.launch(Dispatchers.IO) {
            sendFrame(JSONObject().put("type", "sharing_request").put("enabled", enabled).toString())
        }
    }


    private suspend fun connectAttempt() {
        _state.update { it.copy(status = LinkStatus.CONNECTING, rttMs = -1) }
        try {
            connectOnce()
        } catch (t: Throwable) {
            if (currentCoroutineContext().isActive) {
                Log.w(TAG, "link failed: ${t.message}")
            }
        } finally {
            closeSocket()
            if (currentCoroutineContext().isActive) {
                _state.update { it.copy(
                    status = LinkStatus.DISCONNECTED,
                    rttMs = -1,
                    sharing = false,
                    inputArmed = false,
                ) }
            }
        }
    }

    private suspend fun connectOnce() {
        val s = Socket()
        s.tcpNoDelay = true
        s.connect(InetSocketAddress(LINK_HOST, LINK_PORT), CONNECT_TIMEOUT_MS)
        socket = s
        val out = BufferedWriter(OutputStreamWriter(s.getOutputStream(), Charsets.UTF_8))
        writer = out

        sendFrame(Frames.hello(Build.MODEL ?: "Android", Build.VERSION.SDK_INT, settingsProvider().autoShare))
        pushDisplay()
        val settings = settingsProvider()
        sendFrame(Frames.config(settings.edge.ordinal xor 1, settings.keepAwake, settings.lockRefresh, settings.autoShare))

        _state.update { it.copy(
            status = LinkStatus.CONNECTED,
            rttMs = -1,
        ) }

        val pingJob = scope.launch(Dispatchers.IO) {
            while (isActive && socket === s) {
                sendFrame(Frames.ping(System.currentTimeMillis()))
                delay(PING_INTERVAL_MS)
            }
        }
        val rateJob = scope.launch(Dispatchers.IO) {
            var last = rxBytes.get()
            while (isActive && socket === s) {
                delay(1000)
                pushDisplay()
                val now = rxBytes.get()
                _state.update { it.copy(bytesPerSec = (now - last).toInt().coerceAtLeast(0)) }
                last = now
            }
        }
        try {
            val reader = BufferedReader(InputStreamReader(s.getInputStream(), Charsets.UTF_8))
            while (true) {
                val line = reader.readLine() ?: break
                rxBytes.addAndGet(line.length.toLong() + 1L)
                handleFrame(line)
            }
        } finally {
            pingJob.cancel()
            rateJob.cancel()
        }
    }

    private fun handleFrame(line: String) {
        val obj = try {
            JSONObject(line)
        } catch (t: Throwable) {
            return
        }
        when (obj.optString("type")) {
            "cursor_setup" -> cursorSetup(obj.optString("token"))
            "welcome" -> _state.update { it.copy(
                desktopName = obj.optString("desktop", ""),
                desktopVersion = obj.optInt("version", 0),
            ) }

            "pong" -> {
                val t = obj.optLong("t", -1L)
                if (t > 0L) {
                    val rtt = (System.currentTimeMillis() - t).toInt().coerceIn(0, 9999)
                    _state.update { it.copy(rttMs = rtt) }
                }
            }

            // Desktop-initiated ping: echo the same `t` back so the PC can measure RTT
            // on a single clock. Unknown fields/types are tolerated and ignored.
            "ping" -> {
                val t = obj.optLong("t", -1L)
                if (t >= 0L) {
                    sendFrame(Frames.pong(t))
                    Log.d(TAG, "echo pong t=$t")
                }
            }

            "config" -> Frames.parseConfig(obj)?.let { _desktopConfig.value = it }
            "sharing_state" -> _state.update { it.copy(
                sharing = obj.optBoolean("sharing", false),
                inputArmed = obj.optBoolean("input_armed", false),
                sharingPhase = obj.optString("phase", "connected"),
                transportLabel = obj.optString("transport_label", ""),
                captureGeneration = obj.optLong("capture_generation", 0L),
                placement = obj.optInt("placement", 0),
            ) }
            else -> Unit
        }
    }

    fun reportCursor(x: Int, y: Int, width: Int, height: Int) {
        scope.launch(Dispatchers.IO) {
            sendFrame(JSONObject().put("type", "cursor_position").put("x", x).put("y", y)
                .put("width", width).put("height", height).toString())
        }
    }

    private fun pushDisplay() {
        val (w, h) = displaySize()
        sendFrame(JSONObject().put("type", "display").put("width", w).put("height", h).toString())
    }

    private fun sendFrame(frame: String) {
        val w = writer ?: return
        try {
            synchronized(w) {
                w.write(frame)
                w.newLine()
                w.flush()
            }
        } catch (t: Throwable) {
            Log.w(TAG, "send failed: ${t.message}")
        }
    }

    private fun closeSocket() {
        writer = null
        val s = socket
        socket = null
        if (s != null) {
            runCatching { s.close() }
        }
    }
}
