package com.inputsharex.app.service

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.Context
import android.content.Intent
import android.content.pm.ServiceInfo
import android.net.wifi.WifiManager
import android.os.Build
import android.os.Handler
import android.os.IBinder
import android.os.Looper
import android.os.PowerManager
import android.util.Log
import androidx.core.app.NotificationCompat
import androidx.core.content.ContextCompat
import com.inputsharex.app.MainActivity
import com.inputsharex.app.R
import com.inputsharex.app.data.Edge
import com.inputsharex.app.data.SettingsStore
import com.inputsharex.app.link.DesktopConfig
import com.inputsharex.app.link.LinkClient
import com.inputsharex.app.link.LinkState
import com.inputsharex.app.link.LinkStatus
import com.inputsharex.app.overlay.RefreshConfig
import com.inputsharex.app.overlay.RefreshLockOverlay
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.launch
import java.util.Locale

/**
 * Foreground service that owns the link to the desktop.
 *
 * Holds a Wi-Fi performance lock and a CPU wake lock. Android 14+ can convert
 * HIGH_PERF into a foreground-only LOW_LATENCY lock; desktop compatibility setup
 * restores HIGH_PERF and requests reacquisition through wifi_lock_generation.
 */
class LinkService : Service() {

    companion object {
        private const val TAG = "LinkService"
        private const val CHANNEL_ID = "link_service"
        private const val NOTIFICATION_ID = 1001
        private const val WIFI_LOCK_TAG = "inputsharex:wifi"
        private const val WAKE_LOCK_TAG = "inputsharex:cpu"

        /** Experiment harness: start/stop the refresh-lock overlay with explicit parameters. */
        const val ACTION_EXPERIMENT_START = "com.inputsharex.app.action.EXPERIMENT_START"
        const val ACTION_EXPERIMENT_STOP = "com.inputsharex.app.action.EXPERIMENT_STOP"

        /** Sent by the desktop (through MainActivity) to ask the app to connect once. */
        const val ACTION_CONNECT = "com.inputsharex.app.action.CONNECT"

        private val _running = MutableStateFlow(false)
        val running: StateFlow<Boolean> = _running.asStateFlow()

        private val _state = MutableStateFlow(LinkState())
        val state: StateFlow<LinkState> = _state.asStateFlow()

        private val _desktopConfig = MutableStateFlow<DesktopConfig?>(null)
        private var activeService: LinkService? = null
        val desktopConfig: StateFlow<DesktopConfig?> = _desktopConfig.asStateFlow()

        fun start(context: Context) {
            ContextCompat.startForegroundService(
                context,
                Intent(context, LinkService::class.java),
            )
        }

        fun stop(context: Context) {
            context.stopService(Intent(context, LinkService::class.java))
        }

        fun requestSharing(enabled: Boolean) {
            activeService?.client?.requestSharing(enabled)
        }
    }

    private lateinit var scope: CoroutineScope
    private var cursor: com.inputsharex.app.overlay.CursorOverlayServer? = null
    private var client: LinkClient? = null
    private var wifiLock: WifiManager.WifiLock? = null
    private var wakeLock: PowerManager.WakeLock? = null
    private val jobs = mutableListOf<Job>()
    private val mainHandler: Handler by lazy { Handler(Looper.getMainLooper()) }
    private var wifiLockGeneration = 0L

    /** When true the overlay is driven by the adb experiment harness, not by settings. */
    @Volatile
    private var experimentActive = false

    override fun onCreate() {
        super.onCreate()
        activeService = this
        scope = CoroutineScope(SupervisorJob() + Dispatchers.IO)
        cursor = com.inputsharex.app.overlay.CursorOverlayServer(this) { x, y, w, h ->
            client?.reportCursor(x, y, w, h)
        }
        val link = LinkClient(scope, { SettingsStore.settings.value }, { cursor?.authorize(it) }, {
            val point = android.graphics.Point()
            @Suppress("DEPRECATION")
            getSystemService(android.view.WindowManager::class.java).defaultDisplay.getRealSize(point)
            point.x to point.y
        })
        client = link
        jobs += scope.launch {
            link.state.collect { st ->
                if (st.status == LinkStatus.DISCONNECTED) cursor?.revoke()
                _state.value = st
                updateNotification(st)
            }
        }
        jobs += scope.launch {
            link.desktopConfig.collect { cfg ->
                _desktopConfig.value = cfg
                if (cfg != null && cfg.wifiLockGeneration > 0L) {
                    mainHandler.post {
                        if (wifiLockGeneration != cfg.wifiLockGeneration) {
                            wifiLockGeneration = cfg.wifiLockGeneration
                            runCatching { wifiLock?.takeIf { it.isHeld }?.release() }
                            wifiLock = null
                            acquireLocks()
                            Log.i(TAG, "wifi lock reacquired after desktop compatibility setup")
                        }
                    }
                }
                // 电脑端同步过来的设置（null = 旧桌面未下发该字段）。方向字段使用电脑端语义，需翻转回本设备的边。
                if (cfg != null) {
                    val current = SettingsStore.settings.value
                    val next = current.copy(
                        edge = cfg.placement?.let { Edge.values().getOrNull(it xor 1) } ?: current.edge,
                        keepAwake = cfg.keepAwake ?: current.keepAwake,
                        lockRefresh = cfg.lockRefresh ?: current.lockRefresh,
                    )
                    if (next != current) {
                        SettingsStore.update { next }
                        if (next.keepAwake != current.keepAwake) syncWakeLock()
                        Log.i(TAG, "desktop config applied: edge=${next.edge} keepAwake=${next.keepAwake} lockRefresh=${next.lockRefresh}")
                    }
                }
            }
        }
        jobs += scope.launch {
            SettingsStore.settings.collect { s ->
                link.pushConfig(s)
                syncRefreshLock()
                syncWakeLock()
            }
        }
        createChannel()
        _running.value = true
        // 看门狗：覆盖层可能被系统省电/权限变化移除，定期自愈，保证「1 像素点」常驻。
        val watchdog = object : Runnable {
            override fun run() {
                runCatching { syncRefreshLock() }
                mainHandler.postDelayed(this, 5000L)
            }
        }
        mainHandler.postDelayed(watchdog, 3000L)
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        startInForeground(_state.value)
        acquireLocks()
        // 连接由电脑端发起：只有带 ACTION_CONNECT 的启动（电脑通过 adb 拉起 App）
        // 才会尝试连接；普通启动只保持前台服务，不会自动重连。
        when (intent?.action) {
            ACTION_CONNECT -> client?.requestConnect()
            ACTION_EXPERIMENT_START -> startExperiment(intent)
            ACTION_EXPERIMENT_STOP -> stopExperiment()
            else -> syncRefreshLock()
        }
        return START_STICKY
    }

    /** Keep the experimental 120Hz overlay in sync with the user setting (unless overridden). */
    private fun syncRefreshLock() {
        if (experimentActive) return
        val enabled = SettingsStore.settings.value.lockRefresh
        mainHandler.post {
            if (enabled) {
                if (!RefreshLockOverlay.showing) {
                    val cfg = RefreshLockOverlay.defaultConfig(this)
                    val ok = RefreshLockOverlay.show(this, cfg)
                    Log.i(TAG, "refresh lock overlay enabled (settings) shown=$ok")
                }
            } else if (RefreshLockOverlay.showing) {
                RefreshLockOverlay.stop()
                Log.i(TAG, "refresh lock overlay disabled (settings)")
            }
        }
    }

    private fun startExperiment(intent: Intent) {
        val rate = intent.getFloatExtra("rate", 120f)
        // mode: -1 = do not set preferredDisplayModeId, -2 = auto-pick the ~rate Hz mode id.
        val rawMode = intent.getIntExtra("mode", -1)
        val modeId = when (rawMode) {
            -2 -> RefreshLockOverlay.pickModeId(this, rate)
            else -> rawMode
        }
        val cfg = RefreshConfig(
            frameRate = rate,
            compatibility = intent.getIntExtra("compat", 0),
            strategy = intent.getIntExtra("strategy", 1),
            preferredModeId = modeId,
            width = intent.getIntExtra("w", 1),
            height = intent.getIntExtra("h", 1),
            alpha = intent.getFloatExtra("alpha", 0.01f),
            drawLoop = intent.getBooleanExtra("draw", true),
            gravity = intent.getIntExtra("gravity", android.view.Gravity.TOP or android.view.Gravity.START),
        )
        experimentActive = true
        Log.i(TAG, "EXPERIMENT start ${cfg.describe()}")
        Log.i(TAG, "EXPERIMENT display ${RefreshLockOverlay.describeDisplay(this)}")
        mainHandler.post {
            val ok = RefreshLockOverlay.show(this, cfg)
            Log.i(TAG, "EXPERIMENT overlay shown=$ok")
        }
    }

    private fun stopExperiment() {
        experimentActive = false
        Log.i(TAG, "EXPERIMENT stop")
        mainHandler.post {
            RefreshLockOverlay.stop()
            syncRefreshLock()
        }
    }

    override fun onDestroy() {
        cursor?.close()
        cursor = null
        Log.i(TAG, "service destroyed")
        mainHandler.removeCallbacksAndMessages(null)
        activeService = null
        jobs.forEach { it.cancel() }
        jobs.clear()
        client?.stop()
        client = null
        mainHandler.post { RefreshLockOverlay.stop() }
        releaseLocks()
        scope.cancel()
        _running.value = false
        _state.value = LinkState()
        super.onDestroy()
    }

    override fun onBind(intent: Intent?): IBinder? = null


    private fun createChannel() {
        val nm = getSystemService(NotificationManager::class.java)
        val channel = NotificationChannel(
            CHANNEL_ID,
            "设备连接",
            NotificationManager.IMPORTANCE_LOW,
        ).apply {
            description = "保持与电脑的输入流转连接"
            setShowBadge(false)
        }
        nm.createNotificationChannel(channel)
    }

    private fun startInForeground(state: LinkState) {
        val notification = buildNotification(state)
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.UPSIDE_DOWN_CAKE) {
            startForeground(NOTIFICATION_ID, notification, ServiceInfo.FOREGROUND_SERVICE_TYPE_SPECIAL_USE)
        } else {
            startForeground(NOTIFICATION_ID, notification)
        }
    }

    private fun updateNotification(state: LinkState) {
        val nm = getSystemService(NotificationManager::class.java)
        runCatching { nm.notify(NOTIFICATION_ID, buildNotification(state)) }
    }

    private fun buildNotification(state: LinkState): Notification {
        val pi = PendingIntent.getActivity(
            this,
            0,
            Intent(this, MainActivity::class.java),
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE,
        )
        val title = when (state.status) {
            LinkStatus.CONNECTED -> "已连接 · " + (state.desktopName.ifBlank { "电脑" })
            LinkStatus.CONNECTING -> "正在连接电脑…"
            LinkStatus.DISCONNECTED -> "等待电脑连接"
        }
        val text = if (state.connected) {
            (if (state.sharing) "共享已开启" else "共享已停止") + " · ${state.transportLabel} · 延迟 ${state.rttMs.coerceAtLeast(0)} ms"
        } else {
            "输入流转X · 等待电脑发起连接"
        }
        return NotificationCompat.Builder(this, CHANNEL_ID)
            .setSmallIcon(R.drawable.ic_stat_link)
            .setContentTitle(title)
            .setContentText(text)
            .setOngoing(true)
            .setOnlyAlertOnce(true)
            .setShowWhen(false)
            .setCategory(NotificationCompat.CATEGORY_SERVICE)
            .setPriority(NotificationCompat.PRIORITY_LOW)
            .setContentIntent(pi)
            .build()
    }

    private fun acquireLocks() {
        if (wifiLock == null) {
            val wm = applicationContext.getSystemService(Context.WIFI_SERVICE) as? WifiManager
            val mode = WifiManager.WIFI_MODE_FULL_HIGH_PERF
            val lock = wm?.createWifiLock(mode, WIFI_LOCK_TAG)
            lock?.setReferenceCounted(false)
            runCatching { lock?.acquire() }
            wifiLock = lock
            Log.i(TAG, "wifi lock mode=WIFI_MODE_FULL_HIGH_PERF($mode) held=${lock?.isHeld}")
        }
        syncWakeLock()
    }

    /** 保持唤醒开关：按设置获取/释放 PARTIAL_WAKE_LOCK（与电脑端双向同步）。 */
    private fun syncWakeLock() {
        if (!SettingsStore.settings.value.keepAwake) {
            if (wakeLock != null) {
                runCatching { wakeLock?.takeIf { it.isHeld }?.release() }
                wakeLock = null
                Log.i(TAG, "wake lock released (setting off)")
            }
            return
        }
        if (wakeLock == null) {
            val pm = getSystemService(Context.POWER_SERVICE) as? PowerManager
            val lock = pm?.newWakeLock(PowerManager.PARTIAL_WAKE_LOCK, WAKE_LOCK_TAG)
            lock?.setReferenceCounted(false)
            runCatching { lock?.acquire() }
            wakeLock = lock
            Log.i(TAG, "wake lock held=${lock?.isHeld}")
        }
    }

    private fun releaseLocks() {
        runCatching { wifiLock?.takeIf { it.isHeld }?.release() }
        wifiLock = null
        runCatching { wakeLock?.takeIf { it.isHeld }?.release() }
        wakeLock = null
    }

    private fun formatRate(bytesPerSec: Int): String = when {
        bytesPerSec < 1024 -> "$bytesPerSec B/s"
        bytesPerSec < 1024 * 1024 -> String.format(Locale.US, "%.1f KB/s", bytesPerSec / 1024.0)
        else -> String.format(Locale.US, "%.2f MB/s", bytesPerSec / (1024.0 * 1024.0))
    }
}
