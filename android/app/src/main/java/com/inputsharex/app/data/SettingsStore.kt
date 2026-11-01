package com.inputsharex.app.data

import android.content.Context
import android.content.SharedPreferences
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow

/** 设备的哪一侧与电脑相邻（电脑位于设备的这一侧），用于绘制跨越边线与电脑端同步。 */
enum class Edge(val label: String) {
    RIGHT("右侧"),
    LEFT("左侧"),
    BOTTOM("底部"),
    TOP("顶部"),
}

data class Settings(
    val edge: Edge = Edge.RIGHT,
    val keepAwake: Boolean = true,
    val autoShare: Boolean = true,
    val edgeHint: Boolean = false,
    /** EXPERIMENTAL: keep a tiny overlay voting for 120Hz so the panel does not drop to 50Hz. */
    val lockRefresh: Boolean = false,
)

/** Simple SharedPreferences-backed store exposed as a StateFlow. */
object SettingsStore {
    private const val PREF_NAME = "inputsharex_settings"

    private var prefs: SharedPreferences? = null

    private val _settings = MutableStateFlow(Settings())
    val settings: StateFlow<Settings> = _settings.asStateFlow()

    fun init(context: Context) {
        if (prefs != null) return
        val p = context.applicationContext.getSharedPreferences(PREF_NAME, Context.MODE_PRIVATE)
        prefs = p
        _settings.value = Settings(
            edge = Edge.values().getOrElse(p.getInt(KEY_EDGE, 0)) { Edge.RIGHT },
            keepAwake = p.getBoolean(KEY_KEEP_AWAKE, true),
            autoShare = p.getBoolean("auto_share", true),
            edgeHint = p.getBoolean(KEY_EDGE_HINT, false),
            lockRefresh = p.getBoolean(KEY_LOCK_REFRESH, false),
        )
    }

    fun update(transform: (Settings) -> Settings) {
        val next = transform(_settings.value)
        _settings.value = next
        prefs?.edit()?.apply {
            putInt(KEY_EDGE, next.edge.ordinal)
            putBoolean(KEY_KEEP_AWAKE, next.keepAwake)
            putBoolean("auto_share", next.autoShare)
            putBoolean(KEY_EDGE_HINT, next.edgeHint)
            putBoolean(KEY_LOCK_REFRESH, next.lockRefresh)
            apply()
        }
    }

    private const val KEY_EDGE = "edge"
    private const val KEY_KEEP_AWAKE = "keep_awake"
    private const val KEY_EDGE_HINT = "edge_hint"
    private const val KEY_LOCK_REFRESH = "lock_refresh"
}
