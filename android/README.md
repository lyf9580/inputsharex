# 输入流转X · Android 设备端

InputShareX 的配套 Android App：电脑（Windows）端把键盘鼠标共享到平板/手机，本 App 作为**客户端**连接电脑端启动的本地 TCP 服务（经 `adb reverse` 映射），交换状态、延迟与配置。

- 包名：`com.inputsharex.app`
- 应用名：输入流转X
- 技术栈：Kotlin + Jetpack Compose (Material 3)
- minSdk 26 / targetSdk 35 / compileSdk 35

## 功能

| 模块 | 说明 |
| --- | --- |
| 连接页 | 连接状态、电脑名、实时延迟/速率、开始/停止共享、边缘提示开关、电池优化引导 |
| 设置页 | 放置方向（右/左/底/顶，与电脑端双向同步）、保持唤醒（与电脑端双向同步）、实验：锁定高刷新率（与电脑端双向同步） |
| 关于页 | 版本、包名、协议版本、工作方式说明 |
| 前台服务 | `foregroundServiceType=specialUse`，持有 `WifiLock(WIFI_MODE_FULL_HIGH_PERF)`；`PARTIAL_WAKE_LOCK` 由“保持唤醒”开关控制，常驻通知 |
| 边缘提示 | `TYPE_APPLICATION_OVERLAY` 悬浮细线（需 `SYSTEM_ALERT_WINDOW`） |
| 连接方式 | 由电脑端发起：电脑通过 adb 拉起本 App 并携带 `inputsharex_connect`，App 只尝试一次；失败保持“等待电脑连接”，不自动重连 |

## 构建

调试构建使用本机的 Android 调试签名。正式发布使用专用密钥，密钥文件和密码存放在仓库之外。

在 GitHub 仓库的 Actions secrets 中配置下列四项，再推送发布标签。

| Secret | 内容 |
| --- | --- |
| `ANDROID_RELEASE_KEYSTORE_BASE64` | 新发布 keystore 文件的 Base64 编码 |
| `ANDROID_RELEASE_STORE_PASSWORD` | keystore 密码 |
| `ANDROID_RELEASE_KEY_ALIAS` | 签名密钥别名 |
| `ANDROID_RELEASE_KEY_PASSWORD` | 签名密钥密码 |

本地构建正式 APK 时，用 `ANDROID_RELEASE_KEYSTORE` 指定密钥文件的绝对路径，设置其余三个同名环境变量，然后运行 `:app:assembleRelease`。请备份发布密钥，后续版本沿用该密钥以支持覆盖更新。标签构建会拒绝旧的公开调试证书，并在缺少签名配置时停止。

环境要求：

- JDK 17 或以上（已验证 JDK 21）
- Android SDK：platform-tools、platforms;android-35、build-tools;35.0.0
- `android/local.properties` 中写 `sdk.dir=<你的 SDK 路径>`

命令行构建：

```bash
cd android
./gradlew :app:assembleDebug          # Windows: gradlew.bat :app:assembleDebug
```

产物：`android/app/build/outputs/apk/debug/app-debug.apk`

仓库根目录的打包产物：`dist/InputShareX-android-debug.apk`

安装到设备：

```bash
adb -s <serial> install -r dist/InputShareX-android-debug.apk
```

### 首次构建网络较慢时

依赖来自 Google Maven / Maven Central。若直连较慢，可在 `android/settings.gradle.kts` 中把阿里云镜像放在前面（本仓库已默认使用）：

```
https://maven.aliyun.com/repository/google
https://maven.aliyun.com/repository/public
```

Gradle 发行版使用腾讯镜像（见 `android/gradle/wrapper/gradle-wrapper.properties`）：

```
distributionUrl=https\://mirrors.cloud.tencent.com/gradle/gradle-8.11.1-bin.zip
```

## 连接电脑

1. 电脑端启动 InputShareX 并开始共享（会在 `127.0.0.1:27184` 监听）。
2. 电脑执行端口反代：

   ```bash
   adb -s <serial> reverse tcp:27184 tcp:27184
   ```

3. 打开 App 点「开始共享」。App 连接 `127.0.0.1:27184`，收到 `welcome` 后显示「已连接」与电脑名。

协议细节见仓库 `docs/LINK_PROTOCOL.md`（一行一个 UTF-8 JSON；`hello` / `ping` / `pong` / `welcome` / `config`）。

## 实验功能：锁定高刷新率（120Hz）

背景：部分视频类 App（实测抖音）前台时会把面板压到 50Hz，而鼠标指针由 SurfaceFlinger 按面板刷新率合成，于是指针只有 50 帧/秒、明显不跟手。

做法：在共享服务运行期间，额外创建一个 **1×1、alpha=0.01、`FLAG_NOT_FOCUSABLE | FLAG_NOT_TOUCHABLE` 的 `TYPE_APPLICATION_OVERLAY` + `SurfaceView`**，在它的 Surface 上调用

```kotlin
surface.setFrameRate(
    rate,                                            // 120f
    Surface.FRAME_RATE_COMPATIBILITY_DEFAULT,        // 必须是 0
    Surface.CHANGE_FRAME_RATE_ALWAYS,                // 必须是 1
)
```

即可把面板拉回 120Hz。实测参数很敏感：compat 换成 `FIXED_SOURCE`、或 strategy 换成 `ONLY_IF_SEAMLESS`、或额外设置 `preferredDisplayModeId`，都会失效；1×1 与 2×2 都行；不需要持续投帧（`drawLoop=false`，只每秒重设一次 `setFrameRate`），所以生产路径不跑投帧线程。

- 开关：设置页 →「实验功能」→「锁定高刷新率（实验）」，默认关闭。
- 桌面端可通过 `config.lock_refresh` 下发（缺省字段兼容：老桌面端不发就保持本地设置）。
- 需要 `SYSTEM_ALERT_WINDOW` 权限；窗口是 `NOT_TOUCHABLE`，不影响触摸与 UHID 指针注入。
- 代价：面板常驻高刷新率会增加耗电，且依赖厂商 DisplayManager 的帧率投票行为，换机型可能失效。
- 详细实测数据与证据见仓库 `dist/evidence-refresh/README.md`。

调试用 adb 入口（前台服务未导出，故走 launcher Activity 转发；正式用户不需要）：

```bash
# 让 App 连接电脑（电脑端连接成功后也会自动执行这一条）
adb shell am start -n com.inputsharex.app/.MainActivity --ez inputsharex_connect true
```

```bash

```bash
# 启动实验覆盖层（可用参数扫描）
adb shell am start -n com.inputsharex.app/.MainActivity --ez refresh_exp true \
  --ef rate 120 --ei compat 0 --ei strategy 1 --ei w 1 --ei h 1 --ef alpha 0.01 --ez draw true
# 停止
adb shell am start -n com.inputsharex.app/.MainActivity --ez refresh_stop true
```

## 目录

```
android/
├── app/src/main/java/com/inputsharex/app/
│   ├── App.kt                    # Application
│   ├── MainActivity.kt           # 单 Activity + Compose
│   ├── data/SettingsStore.kt     # SharedPreferences + StateFlow
│   ├── link/Protocol.kt          # 协议常量与帧构造
│   ├── link/LinkClient.kt        # TCP 客户端、ping（连接由电脑端触发）
│   ├── overlay/EdgeOverlay.kt    # 边缘悬浮提示
│   ├── overlay/RefreshLockOverlay.kt # 实验：1×1 覆盖层投票 120Hz
│   ├── service/LinkService.kt    # 前台服务 + 锁
│   ├── ui/                       # Compose 页面与主题
│   └── util/SystemPerms.kt       # 电池优化 / 悬浮窗权限
└── app/src/main/res/             # 字符串、主题、图标
```
