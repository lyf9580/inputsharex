package com.inputsharex.app

import android.app.AlertDialog
import android.app.DownloadManager
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.provider.Settings
import android.widget.Toast
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.core.content.ContextCompat
import com.inputsharex.app.data.SettingsStore
import com.inputsharex.app.service.LinkService
import com.inputsharex.app.ui.AppRoot
import com.inputsharex.app.ui.InputShareXTheme
import com.inputsharex.app.update.UpdateChecker
import com.inputsharex.app.update.UpdateInfo

class MainActivity : ComponentActivity() {
    private var pendingDownloadId: Long = -1L
    private var downloadReceiver: BroadcastReceiver? = null
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        SettingsStore.init(this)
        // 打开 App 即拉起前台服务：链路与「保持 120Hz」覆盖层在后台持续生效，
        // 不需要用户手动点「开始共享」。连接只由电脑端发起：电脑通过 adb 拉起本页
        // 并携带 inputsharex_connect 标记，App 收到后才做一次连接尝试，失败不重连。
        runCatching { LinkService.start(this) }
            .onFailure { android.util.Log.w("MainActivity", "start link service failed", it) }
        enableEdgeToEdge()
        setContent {
            InputShareXTheme {
                AppRoot()
            }
        }
        handleExperimentIntent(intent)
        handleConnectIntent(intent)
        maybeCheckForUpdates()
    }

    override fun onDestroy() {
        downloadReceiver?.let { runCatching { unregisterReceiver(it) } }
        downloadReceiver = null
        super.onDestroy()
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        handleExperimentIntent(intent)
        handleConnectIntent(intent)
    }

    /** 电脑端（adb）拉起本页时带 inputsharex_connect 标记，转发给服务触发一次连接。 */
    private fun handleConnectIntent(intent: Intent?) {
        if (intent?.getBooleanExtra(EXTRA_CONNECT, false) != true) return
        startServiceAction(LinkService.ACTION_CONNECT, null)
    }

    /**
     * Experiment harness: the link service is deliberately not exported, so the adb-driven
     * refresh-rate experiments are triggered through this (launcher) activity instead.
     * Extras are copied verbatim into the service intent; see RefreshLockOverlay.
     */
    private fun handleExperimentIntent(intent: Intent?) {
        if (intent == null) return
        if (intent.getBooleanExtra("refresh_stop", false)) {
            startServiceAction(LinkService.ACTION_EXPERIMENT_STOP, null)
            finish()
            return
        }
        if (!intent.getBooleanExtra("refresh_exp", false)) return
        val extras = Bundle()
        for (key in FLOAT_EXTRAS) {
            if (intent.hasExtra(key)) extras.putFloat(key, intent.getFloatExtra(key, 0f))
        }
        for (key in INT_EXTRAS) {
            if (intent.hasExtra(key)) extras.putInt(key, intent.getIntExtra(key, 0))
        }
        for (key in BOOL_EXTRAS) {
            if (intent.hasExtra(key)) extras.putBoolean(key, intent.getBooleanExtra(key, false))
        }
        startServiceAction(LinkService.ACTION_EXPERIMENT_START, extras)
        finish()
    }

    private fun startServiceAction(action: String, extras: Bundle?) {
        val serviceIntent = Intent(this, LinkService::class.java).setAction(action)
        extras?.let { serviceIntent.putExtras(it) }
        ContextCompat.startForegroundService(this, serviceIntent)
    }

    /** 启动时静默检查 GitHub 是否有新版安卓端，有则弹窗提示下载。 */
    private fun maybeCheckForUpdates() {
        UpdateChecker.check { info ->
            if (info == null) return@check
            runOnUiThread {
                if (!isFinishing && !isDestroyed) showUpdateDialog(info)
            }
        }
    }

    private fun showUpdateDialog(info: UpdateInfo) {
        val notes = info.notes.trim().take(600)
        val message = buildString {
            append("发现新版本 ").append(info.version)
            append("（当前 ").append(BuildConfig.VERSION_NAME).append("）。")
            if (notes.isNotEmpty()) {
                append("\n\n").append(notes)
            }
            append("\n\n更新包会下载到系统的「下载」目录，完成后点按通知即可安装。")
        }
        AlertDialog.Builder(this)
            .setTitle("发现新版本")
            .setMessage(message)
            .setPositiveButton("下载更新") { _, _ -> startUpdateDownload(info) }
            .setNegativeButton("稍后", null)
            .show()
    }

    private fun startUpdateDownload(info: UpdateInfo) {
        if (info.assetUrl.isBlank()) {
            Toast.makeText(this, "该版本没有安卓安装包", Toast.LENGTH_SHORT).show()
            return
        }
        runCatching { UpdateChecker.download(this, info) }
            .onSuccess { id ->
                pendingDownloadId = id
                registerDownloadReceiver()
                Toast.makeText(this, "已开始下载到「下载」目录", Toast.LENGTH_SHORT).show()
            }
            .onFailure {
                Toast.makeText(this, "下载失败：${it.message}", Toast.LENGTH_LONG).show()
            }
    }

    private fun registerDownloadReceiver() {
        if (downloadReceiver != null) return
        val receiver = object : BroadcastReceiver() {
            override fun onReceive(context: Context, intent: Intent) {
                if (intent.action != DownloadManager.ACTION_DOWNLOAD_COMPLETE) return
                val id = intent.getLongExtra(DownloadManager.EXTRA_DOWNLOAD_ID, -1L)
                if (id != pendingDownloadId) return
                pendingDownloadId = -1L
                installDownloaded(id)
            }
        }
        downloadReceiver = receiver
        val filter = IntentFilter(DownloadManager.ACTION_DOWNLOAD_COMPLETE)
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
            registerReceiver(receiver, filter, Context.RECEIVER_EXPORTED)
        } else {
            @Suppress("DEPRECATION")
            registerReceiver(receiver, filter)
        }
    }

    private fun installDownloaded(downloadId: Long) {
        val uri = UpdateChecker.downloadedUri(this, downloadId)
        if (uri == null) {
            Toast.makeText(this, "下载完成，请到「下载」目录手动安装", Toast.LENGTH_LONG).show()
            return
        }
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O &&
            !packageManager.canRequestPackageInstalls()
        ) {
            Toast.makeText(this, "请允许安装未知来源应用后再安装", Toast.LENGTH_LONG).show()
            runCatching {
                startActivity(
                    Intent(
                        Settings.ACTION_MANAGE_UNKNOWN_APP_SOURCES,
                        Uri.parse("package:$packageName"),
                    )
                )
            }
            return
        }
        val install = Intent(Intent.ACTION_VIEW)
            .setDataAndType(uri, "application/vnd.android.package-archive")
            .addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION or Intent.FLAG_ACTIVITY_NEW_TASK)
        runCatching { startActivity(install) }
            .onFailure { Toast.makeText(this, "无法打开安装器：${it.message}", Toast.LENGTH_LONG).show() }
    }

    companion object {
        /** Must match `androidapp.EXTRA_CONNECT` on the desktop side. */
        const val EXTRA_CONNECT = "inputsharex_connect"

        private val FLOAT_EXTRAS = arrayOf("rate", "alpha")
        private val INT_EXTRAS = arrayOf("compat", "strategy", "mode", "w", "h", "gravity")
        private val BOOL_EXTRAS = arrayOf("draw")
    }
}
