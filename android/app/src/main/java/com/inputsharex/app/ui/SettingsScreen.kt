package com.inputsharex.app.ui

import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.FilterChip
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Switch
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import com.inputsharex.app.data.Edge
import com.inputsharex.app.data.Settings
import com.inputsharex.app.data.SettingsStore

@Composable
fun SettingsScreen(settings: Settings) {
    Column(
        modifier = Modifier
            .fillMaxSize()
            .verticalScroll(rememberScrollState())
            .padding(horizontal = 20.dp, vertical = 20.dp),
        verticalArrangement = Arrangement.spacedBy(16.dp),
    ) {
        Text(
            text = "设置",
            style = MaterialTheme.typography.headlineMedium,
            fontWeight = FontWeight.Bold,
        )

        Group(title = "鼠标与触控") {
            Column(Modifier.padding(horizontal = 18.dp, vertical = 14.dp)) {
                Text("放置方向", style = MaterialTheme.typography.titleMedium)
                Text(
                    "电脑在这台平板的哪一侧，用于判断跨越边界",
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
                Spacer(Modifier.height(10.dp))
                Row(
                    modifier = Modifier.horizontalScroll(rememberScrollState()),
                    horizontalArrangement = Arrangement.spacedBy(10.dp),
                ) {
                    Edge.values().forEach { edge ->
                        FilterChip(
                            selected = settings.edge == edge,
                            onClick = { SettingsStore.update { it.copy(edge = edge) } },
                            label = { Text(edge.label) },
                        )
                    }
                }
            }
        }

        Group(title = "连接与后台") {
            SwitchRow(
                title = "保持唤醒",
                subtitle = "共享期间保持 CPU 与 Wifi 高性能，避免息屏后断开",
                checked = settings.keepAwake,
                onCheckedChange = { on -> SettingsStore.update { it.copy(keepAwake = on) } },
            )
        }

        Group(title = "实验功能") {
            SwitchRow(
                title = "锁定高刷新率（实验）",
                subtitle = "用 1×1 覆盖层请求 120Hz，尽量让抖音等 50Hz 应用里鼠标指针仍然跟手；会略增耗电",
                checked = settings.lockRefresh,
                onCheckedChange = { on -> SettingsStore.update { it.copy(lockRefresh = on) } },
            )
        }

        Text(
            "放置方向、保持唤醒、锁定高刷新率会与电脑端双向同步：改这一边，另一边自动更新；连接新设备时以该设备的设置为准。",
            style = MaterialTheme.typography.bodySmall,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
        )
    }
}

@Composable
private fun Group(title: String, content: @Composable () -> Unit) {
    Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
        Text(
            text = title,
            style = MaterialTheme.typography.labelLarge,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
            modifier = Modifier.padding(start = 4.dp),
        )
        Card(
            modifier = Modifier.fillMaxWidth(),
            colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surface),
            shape = MaterialTheme.shapes.large,
        ) {
            Column { content() }
        }
    }
}

@Composable
private fun SwitchRow(
    title: String,
    subtitle: String,
    checked: Boolean,
    onCheckedChange: (Boolean) -> Unit,
) {
    Row(
        modifier = Modifier.padding(horizontal = 18.dp, vertical = 14.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Column(Modifier.weight(1f)) {
            Text(title, style = MaterialTheme.typography.titleMedium)
            Text(
                subtitle,
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
        }
        Switch(checked = checked, onCheckedChange = onCheckedChange)
    }
}
