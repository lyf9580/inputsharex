package com.inputsharex.app.link

import org.json.JSONObject

/** Protocol constants, see docs/LINK_PROTOCOL.md in the desktop repository. */
const val APP_VERSION = "0.2.4"
const val PROTOCOL_VERSION = 1
const val LINK_HOST = "127.0.0.1"
const val LINK_PORT = 27184

/** Configuration pushed by the desktop. Null means the field was absent in the frame. */
data class DesktopConfig(
    /** 电脑端放置方向：0 右侧 / 1 左侧 / 2 下方 / 3 上方（电脑端语义）。 */
    val placement: Int? = null,
    val keepAwake: Boolean? = null,
    val lockRefresh: Boolean? = null,
    val wifiLockGeneration: Long = 0L,
)

/** Line-delimited JSON frames. One frame per line, UTF-8, '\n' terminated. */
object Frames {
    fun hello(device: String, android: Int, autoShare: Boolean = true): String = JSONObject()
        .put("type", "hello")
        .put("version", PROTOCOL_VERSION)
        .put("device", device)
        .put("android", android)
        .put("app", APP_VERSION)
        .put("auto_share", autoShare)
        .toString()

    fun ping(t: Long): String = JSONObject()
        .put("type", "ping")
        .put("t", t)
        .toString()

    /** Echo for a ping initiated by the desktop, so it can measure RTT on its own clock. */
    fun pong(t: Long): String = JSONObject()
        .put("type", "pong")
        .put("t", t)
        .toString()

    /** placement 使用电脑端语义（设备位于电脑的哪一侧）。 */
    fun config(placement: Int, keepAwake: Boolean, lockRefresh: Boolean = false, autoShare: Boolean = true): String = JSONObject()
        .put("type", "config")
        .put("placement", placement)
        .put("keep_awake", keepAwake)
        .put("lock_refresh", lockRefresh)
        .put("auto_share", autoShare)
        .toString()

    fun parseConfig(obj: JSONObject): DesktopConfig? {
        if (obj.optString("type") != "config") return null
        return DesktopConfig(
            placement = if (obj.has("placement")) obj.optInt("placement", 0) else null,
            keepAwake = if (obj.has("keep_awake")) obj.optBoolean("keep_awake", true) else null,
            lockRefresh = if (obj.has("lock_refresh")) obj.optBoolean("lock_refresh", false) else null,
            wifiLockGeneration = obj.optLong("wifi_lock_generation", 0L),
        )
    }
}
