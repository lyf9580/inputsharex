package com.inputsharex.app.ui

import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.NavigationBar
import androidx.compose.material3.NavigationBarItem
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.res.painterResource
import androidx.compose.ui.unit.dp
import com.inputsharex.app.R
import com.inputsharex.app.data.SettingsStore
import com.inputsharex.app.service.LinkService

private data class Tab(val label: String, val icon: Int)

@Composable
fun AppRoot() {
    val settings by SettingsStore.settings.collectAsState()
    val link by LinkService.state.collectAsState()
    val running by LinkService.running.collectAsState()
    val desktopConfig by LinkService.desktopConfig.collectAsState()

    var index by rememberSaveable { mutableIntStateOf(0) }
    val tabs = listOf(
        Tab("连接", R.drawable.ic_tab_link),
        Tab("设置", R.drawable.ic_tab_settings),
        Tab("关于", R.drawable.ic_tab_info),
    )

    Scaffold(
        containerColor = MaterialTheme.colorScheme.background,
        bottomBar = {
            NavigationBar(containerColor = MaterialTheme.colorScheme.surface) {
                tabs.forEachIndexed { i, tab ->
                    NavigationBarItem(
                        selected = index == i,
                        onClick = { index = i },
                        icon = {
                            Icon(
                                painter = painterResource(tab.icon),
                                contentDescription = tab.label,
                                modifier = Modifier.size(22.dp),
                            )
                        },
                        label = { Text(tab.label) },
                    )
                }
            }
        },
    ) { padding ->
        Box(Modifier.fillMaxSize().padding(padding)) {
            when (index) {
                0 -> ConnectScreen(settings, link, running, desktopConfig)
                1 -> SettingsScreen(settings)
                else -> AboutScreen()
            }
        }
    }
}
