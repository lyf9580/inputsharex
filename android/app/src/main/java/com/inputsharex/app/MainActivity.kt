package com.inputsharex.app

import android.content.Intent
import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.core.content.ContextCompat
import com.inputsharex.app.data.SettingsStore
import com.inputsharex.app.service.LinkService
import com.inputsharex.app.ui.AppRoot
import com.inputsharex.app.ui.InputShareXTheme

class MainActivity : ComponentActivity() {
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

    companion object {
        /** Must match `androidapp.EXTRA_CONNECT` on the desktop side. */
        const val EXTRA_CONNECT = "inputsharex_connect"

        private val FLOAT_EXTRAS = arrayOf("rate", "alpha")
        private val INT_EXTRAS = arrayOf("compat", "strategy", "mode", "w", "h", "gravity")
        private val BOOL_EXTRAS = arrayOf("draw")
    }
}
