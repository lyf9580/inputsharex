package com.inputsharex.app.update

import android.app.DownloadManager
import android.content.Context
import android.net.Uri
import android.os.Environment
import android.util.Log
import com.inputsharex.app.BuildConfig
import org.json.JSONObject
import java.net.HttpURLConnection
import java.net.URL

/** 已发布的新版本信息。 */
data class UpdateInfo(
    val version: String,
    val pageUrl: String,
    val notes: String,
    val assetName: String,
    val assetUrl: String,
)

/**
 * 启动时检查 GitHub 上是否有新版安卓端 APK。
 *
 * 只读取 releases/latest 的 JSON，不依赖任何第三方库；下载交给系统 DownloadManager，
 * 文件落在标准的公共下载目录（Download/），完成后由系统通知栏提示安装。
 *
 * 注意：Release 的 tag 是桌面端版本号，安卓端版本号独立（如 tag v0.4.3 / APK 0.3.0），
 * 因此这里比较的是资产文件名里的安卓版本号，而不是 tag。
 */
object UpdateChecker {
    const val REPO_URL = "https://github.com/lyf9580/inputsharex"
    private const val API_URL = "https://api.github.com/repos/lyf9580/inputsharex/releases/latest"
    private const val ASSET_PREFIX = "inputsharex-android-"
    private const val APK_SUFFIX = ".apk"
    private const val TIMEOUT_MS = 8000

    /** 后台检查；onResult 在子线程回调，调用方需切回主线程。 */
    fun check(onResult: (UpdateInfo?) -> Unit) {
        Thread {
            val info = runCatching { fetchLatest() }
                .onFailure { Log.w("UpdateChecker", "check failed", it) }
                .getOrNull()
            onResult(info)
        }.apply { isDaemon = true }.start()
    }

    /** 交给系统下载器下载到公共 Download 目录，返回 download id。 */
    fun download(context: Context, info: UpdateInfo): Long {
        val request = DownloadManager.Request(Uri.parse(info.assetUrl))
            .setTitle("输入流转X ${info.version}")
            .setDescription("正在下载安卓端更新")
            .setMimeType("application/vnd.android.package-archive")
            .setNotificationVisibility(DownloadManager.Request.VISIBILITY_VISIBLE_NOTIFY_COMPLETED)
            .setDestinationInExternalPublicDir(Environment.DIRECTORY_DOWNLOADS, info.assetName)
        val manager = context.getSystemService(Context.DOWNLOAD_SERVICE) as DownloadManager
        return manager.enqueue(request)
    }

    /** 已下载文件的 content:// Uri，供安装时使用。 */
    fun downloadedUri(context: Context, downloadId: Long): Uri? {
        val manager = context.getSystemService(Context.DOWNLOAD_SERVICE) as DownloadManager
        return manager.getUriForDownloadedFile(downloadId)
    }

    private fun fetchLatest(): UpdateInfo? {
        val connection = (URL(API_URL).openConnection() as HttpURLConnection).apply {
            connectTimeout = TIMEOUT_MS
            readTimeout = TIMEOUT_MS
            requestMethod = "GET"
            setRequestProperty("Accept", "application/vnd.github+json")
            setRequestProperty("User-Agent", "InputShareX-Android/${BuildConfig.VERSION_NAME}")
        }
        try {
            if (connection.responseCode != HttpURLConnection.HTTP_OK) return null
            val body = connection.inputStream.bufferedReader().use { it.readText() }
            val json = JSONObject(body)
            val assets = json.optJSONArray("assets") ?: return null
            for (index in 0 until assets.length()) {
                val asset = assets.optJSONObject(index) ?: continue
                val name = asset.optString("name")
                if (!name.startsWith(ASSET_PREFIX) || !name.endsWith(APK_SUFFIX)) continue
                val version = name.removePrefix(ASSET_PREFIX).removeSuffix(APK_SUFFIX)
                if (version.isBlank() || !isNewer(version, BuildConfig.VERSION_NAME)) continue
                return UpdateInfo(
                    version = version,
                    pageUrl = json.optString("html_url", REPO_URL),
                    notes = json.optString("body", ""),
                    assetName = name,
                    assetUrl = asset.optString("browser_download_url"),
                )
            }
            return null
        } finally {
            connection.disconnect()
        }
    }

    internal fun isNewer(remote: String, current: String): Boolean {
        val remoteParts = parseVersion(remote) ?: return false
        val currentParts = parseVersion(current) ?: return false
        val size = maxOf(remoteParts.size, currentParts.size)
        for (index in 0 until size) {
            val remoteValue = remoteParts.getOrElse(index) { 0 }
            val currentValue = currentParts.getOrElse(index) { 0 }
            if (remoteValue != currentValue) return remoteValue > currentValue
        }
        return false
    }

    private fun parseVersion(text: String): List<Int>? =
        Regex("(\\d+(?:\\.\\d+)*)").find(text)?.groupValues?.get(1)
            ?.split(".")?.mapNotNull { it.toIntOrNull() }
}
