package com.inputsharex.app.ui

import android.Manifest
import android.os.Build
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Button
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Switch
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import com.inputsharex.app.data.Settings
import com.inputsharex.app.data.SettingsStore
import com.inputsharex.app.link.DesktopConfig
import com.inputsharex.app.link.LINK_HOST
import com.inputsharex.app.link.LINK_PORT
import com.inputsharex.app.link.LinkState
import com.inputsharex.app.link.LinkStatus
import com.inputsharex.app.overlay.EdgeOverlay
import com.inputsharex.app.service.LinkService
import com.inputsharex.app.util.SystemPerms
import java.util.Locale

@Composable
fun ConnectScreen(
    settings: Settings,
    link: LinkState,
    running: Boolean,
    desktopConfig: DesktopConfig?,
) {
    val context = LocalContext.current
    var overlayAllowed by remember { mutableStateOf(SystemPerms.canDrawOverlays(context)) }
    var batteryIgnored by remember { mutableStateOf(SystemPerms.isIgnoringBatteryOptimizations(context)) }

    val notificationLauncher = rememberLauncherForActivityResult(
        ActivityResultContracts.RequestPermission(),
    ) { }

    DisposableEffect(Unit) {
        onDispose { EdgeOverlay.hide(context) }
    }
    DisposableEffect(context) {
        val lifecycle = (context as? androidx.lifecycle.LifecycleOwner)?.lifecycle
        val observer = androidx.lifecycle.LifecycleEventObserver { _, event ->
            if (event == androidx.lifecycle.Lifecycle.Event.ON_RESUME) {
                overlayAllowed = SystemPerms.canDrawOverlays(context)
                batteryIgnored = SystemPerms.isIgnoringBatteryOptimizations(context)
            }
        }
        lifecycle?.addObserver(observer)
        onDispose { lifecycle?.removeObserver(observer) }
    }
    LaunchedEffect(settings.edgeHint, settings.edge, overlayAllowed) {
        EdgeOverlay.refresh(context, settings.edge, settings.edgeHint && overlayAllowed)
    }

    Column(
        modifier = Modifier
            .fillMaxSize()
            .verticalScroll(rememberScrollState())
            .padding(horizontal = 20.dp, vertical = 20.dp),
        verticalArrangement = Arrangement.spacedBy(16.dp),
    ) {
        Column {
            Text(
                text = "输入流转X",
                style = MaterialTheme.typography.headlineMedium,
                fontWeight = FontWeight.Bold,
            )
            Spacer(Modifier.height(4.dp))
            Text(
                text = "把电脑的键盘鼠标流转到这台平板",
                style = MaterialTheme.typography.bodyMedium,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
        }

        StatusCard(link = link, running = running)

        Button(
            onClick = { LinkService.requestSharing(!link.sharing) },
            enabled = link.connected && link.sharingPhase != "connecting",
            modifier = Modifier.fillMaxWidth().height(54.dp),
        ) {
            Text(if (link.sharingPhase == "connecting") "正在开启共享…" else if (link.sharing) "停止共享" else "开始共享",
                fontWeight = FontWeight.SemiBold)
        }

        Card(colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surface)) {
            Row(Modifier.padding(18.dp), verticalAlignment = Alignment.CenterVertically) {
                Column(Modifier.weight(1f)) {
                    Text("下次连接自动开启共享", style = MaterialTheme.typography.titleMedium)
                    Text("仅影响下次连接，不会改变当前共享状态", style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant)
                }
                Switch(checked = settings.autoShare,
                    onCheckedChange = { value -> SettingsStore.update { it.copy(autoShare = value) } })
            }
        }


        Card(
            colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surface),
            shape = MaterialTheme.shapes.large,
        ) {
            Column(Modifier.padding(18.dp)) {
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Column(Modifier.weight(1f)) {
                        Text("边缘提示", style = MaterialTheme.typography.titleMedium)
                        Text(
                            "在屏幕边缘显示一条提示线，标出当前的跨越边界",
                            style = MaterialTheme.typography.bodySmall,
                            color = MaterialTheme.colorScheme.onSurfaceVariant,
                        )
                    }
                    Switch(
                        checked = settings.edgeHint,
                        onCheckedChange = { on ->
                            if (on) {
                                overlayAllowed = SystemPerms.canDrawOverlays(context)
                                if (!overlayAllowed) {
                                    SystemPerms.requestOverlayPermission(context)
                                    return@Switch
                                }
                            }
                            SettingsStore.update { it.copy(edgeHint = on) }
                        },
                    )
                }
            }
        }

        Card(
            colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surface),
            shape = MaterialTheme.shapes.large,
        ) {
            Row(
                modifier = Modifier.padding(18.dp),
                verticalAlignment = Alignment.CenterVertically,
            ) {
                Column(Modifier.weight(1f)) {
                    Text("电池优化", style = MaterialTheme.typography.titleMedium)
                    Text(
                        if (batteryIgnored) "已关闭电池优化，后台连接更稳定" else "建议关闭电池优化，避免息屏后连接被系统中断",
                        style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                }
                TextButton(
                    onClick = {
                        batteryIgnored = SystemPerms.isIgnoringBatteryOptimizations(context)
                        if (!batteryIgnored) {
                            SystemPerms.requestIgnoreBatteryOptimizations(context)
                        }
                    },
                ) {
                    Text(if (batteryIgnored) "刷新" else "去设置")
                }
            }
        }

        if (desktopConfig != null) {
            Card(
                colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surfaceVariant),
                shape = MaterialTheme.shapes.large,
            ) {
                Column(Modifier.padding(18.dp)) {
                    Text("共享设置", style = MaterialTheme.typography.titleSmall)
                    Spacer(Modifier.height(8.dp))
                    Text(
                        "方向 " + settings.edge.label +
                            " · 保持唤醒 " + (if (settings.keepAwake) "开" else "关") +
                            " · 锁定高刷新率 " + (if (settings.lockRefresh) "开" else "关"),
                        style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                }
            }
        }

        Text(
            "电脑和安卓端的共享状态自动同步",
            style = MaterialTheme.typography.bodySmall,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
        )
    }
}

@Composable
private fun StatusCard(link: LinkState, running: Boolean) {
    val statusColor = when (link.status) {
        LinkStatus.CONNECTED -> StatusGreen
        LinkStatus.CONNECTING -> StatusAmber
        LinkStatus.DISCONNECTED -> StatusGrey
    }
    val statusText = when (link.status) {
        LinkStatus.CONNECTED -> "已连接"
        LinkStatus.CONNECTING -> "正在连接…"
        LinkStatus.DISCONNECTED -> "等待电脑连接"
    }
    Card(
        colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surface),
        shape = MaterialTheme.shapes.large,
    ) {
        Column(Modifier.padding(20.dp), verticalArrangement = Arrangement.spacedBy(14.dp)) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Box(
                    Modifier
                        .size(10.dp)
                        .background(statusColor, CircleShape),
                )
                Spacer(Modifier.size(8.dp))
                Text(statusText, style = MaterialTheme.typography.titleLarge, fontWeight = FontWeight.SemiBold)
                Spacer(Modifier.weight(1f))
                Text(
                    if (running) "服务运行中" else "服务未启动",
                    style = MaterialTheme.typography.labelMedium,
                    color = if (running) Accent else MaterialTheme.colorScheme.onSurfaceVariant,
                )
            }
            Row(verticalAlignment = Alignment.CenterVertically) {
                Text(
                    "电脑",
                    style = MaterialTheme.typography.bodyMedium,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
                Spacer(Modifier.weight(1f))
                Text(
                    link.desktopName.ifBlank { if (link.connected) "未知电脑" else "—" },
                    style = MaterialTheme.typography.bodyLarge,
                    fontWeight = FontWeight.Medium,
                )
            }
            Text(if (!link.connected) "共享状态：等待电脑连接" else if (link.sharing) "共享已开启" else "共享已停止",
                style = MaterialTheme.typography.titleMedium)
            if (link.transportLabel.isNotBlank()) Text(link.transportLabel, style = MaterialTheme.typography.bodyMedium)
            Text(if (link.inputArmed) "鼠标已接管" else "鼠标未接管", style = MaterialTheme.typography.bodySmall)
            Row {
                Metric("延迟", if (link.rttMs >= 0) "${link.rttMs} ms" else "—", Modifier.weight(1f))
                Metric("速率", formatRate(link.bytesPerSec), Modifier.weight(1f))
            }
        }
    }
}

@Composable
private fun Metric(label: String, value: String, modifier: Modifier = Modifier) {
    Column(modifier) {
        Text(
            label,
            style = MaterialTheme.typography.labelMedium,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
        )
        Spacer(Modifier.height(2.dp))
        Text(value, style = MaterialTheme.typography.titleMedium, fontWeight = FontWeight.SemiBold)
    }
}

internal fun formatRate(bytesPerSec: Int): String = when {
    bytesPerSec <= 0 -> "—"
    bytesPerSec < 1024 -> "$bytesPerSec B/s"
    bytesPerSec < 1024 * 1024 -> String.format(Locale.US, "%.1f KB/s", bytesPerSec / 1024.0)
    else -> String.format(Locale.US, "%.2f MB/s", bytesPerSec / (1024.0 * 1024.0))
}

internal val UnusedColor: Color = Color.Transparent
