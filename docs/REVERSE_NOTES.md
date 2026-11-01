# 逆向笔记：InputShare2 1.1.11.0

只记录与本项目有关的部分；完整分析（进程、端口、日志、配置、隐私评估）见会话记录。

## 包与进程

- MSIX Store 包：`BHznJNs.InputShare2_1.1.11.0_x64__ahfsmsjm0fbat`，Store 签名，`runFullTrust` + `internetClient` + `DeviceCapability usb`。
- 开机自启：manifest 里 `windows.startupTask` `TaskId="InputShare2-StartUp"`，**默认 `Enabled="false"`**。
- 进程：
  - `Source\InputShare2.exe`：Python 3.13 + **Nuitka** standalone（不是 PyInstaller；`pyinstxtractor` 报 `Missing cookie`），PySide6/PIL/pywin32/zeroconf/pydantic/orjson/psutil。
  - `Source\assets\inputshare-ui.exe`：**Tauri**（Rust + WebView2），参数 `--core-port 23512 --application-language zh_CN --application-theme system`。
- 核心自报版本 `InputShare2 version 1.1.2`，UI 1.1.5，包 1.1.11.0。

## 端口与 IPC

| 组件 | 地址 | 说明 |
| --- | --- | --- |
| 核心 HTTP（UI 调用） | `[::1]:23512` | Python `http.server` 风格；路由 `/api/settings`、`/api/devices`、`/api/adb`、`/api/exit`、`/api/tauri-sse` |
| 写配置 | `PUT /api/devices/config` | body `{"placement":0,"config":{placement,auto_connect,mouse_motion_rate,scroll_rate,clipboard_sharing,keep_device_awake}}` |
| 自带 adb daemon | `127.0.0.1:5038` | 避开默认 5037；PC 通过它连设备 |
| 设备端 scrcpy | `localabstract:scrcpy` | 服务端 jar 在 `server_bin\scrcpy-server`（91,904B，自报 `scrcpy 3.3.3 Secondary developed by BHznJNs`） |
| 设备端 reporter | `localabstract:inputshare` | `reporter.apk` = `com.bhznjns.inputshare` v1.0.19，Kotlin/Compose，AccessibilityService `overlay.OverlayService` + `LocalSocketServer`，消息用 kotlinx.serialization |

配对流程：adb mdns 发现设备 → `adb pair`（字符串里带 `WIFI:T:ADB;S:inputshare-pairing;P:` 二维码）→ `adb connect ip:port`。

## 核心模块（Nuitka 输出的模块名）

```
src.main, src.control_loop, src.core, src.types
src.routes: adb, devices, settings, tauri_sse
src.controllers:
  adb (AdbServiceManager, AdbDeviceManager, listeners.connect/pair, utils)
  input.keyboard (defs, listeners)
  input.mouse (EdgeDetector[MonitorRefresher, TriggerStateMachine], MouseCapturer, MouseEventSender, MousePollingRateEstimator)
  server (ScrcpyServerController, ReporterServerController, OnDeviceServerController)
  ClipboardMonitor, IdleDetector, floating_ball (DropZone, SendFileWorker)
src.utils: clipboard, config, i18n, key_serde, network.port_check, path, queue_utils, resources, scheduler, set_priority, win32(.dpi_utils/.mouse_pos/.msix_utils), float_accumulator
```

设备端 (reporter)：`overlay.OverlayManager/SideLineView/Communicator/CommunicatorMessage`、`utils.LocalSocketServer`、`datastore.{AKeyCode,Deadzone,DoubleTrigger,Placement,Shortcut}` —— 与 PC 端 `config.json` 的 `deadzone/double_trigger/placement/shortcut` 一一对应。

## 配置（PC 端）

`%LOCALAPPDATA%\Packages\BHznJNs.InputShare2_ahfsmsjm0fbat\LocalCache\Local\com.bhznjns.inputshare2\config.json`

```json
{"language":"zh_CN","theme":"system","file_floating_ball":false,"exit_when_idle":false,
 "exit_when_idle_timeout_sec":1800,"edge_switch":true,"double_trigger":false,
 "double_trigger_interval_ms":300,"deadzone":false,"deadzone_size":40,
 "device_config_mapping":{},"key_functions_mapping":{...},"switch_shortcuts":[...]}
```

`key_functions_mapping` 的键值就是 Android `KeyEvent` 常量：F1=187(APP_SWITCH)、F2=3(HOME)、F3=4(BACK)、F7=25(VOLUME_DOWN)、F8=24(VOLUME_UP)、F11=276(SOFT_SLEEP)、F12=224(WAKEUP)。

## 卡顿根因（为什么本项目要自带保活）

PC→平板下行稀疏 UDP/ICMP 在平板 Wi-Fi 空闲时被 AP 缓冲，直到平板下次唤醒（DTIM）才下发：

| 状态 | p50 | p90 | max |
| --- | --- | --- | --- |
| 完全空闲 | 22–50ms | 78–122ms | 105–214ms |
| 平板正在下载 | 9ms | 58ms | 64ms |
| 8ms 保活 | 10–13ms | 30–56ms | 59–146ms |

上行（平板→PC/路由器）始终 2–20ms，说明问题只在「下行唤醒延迟」，不是带宽、不是信号（两端都是 5GHz/11ax/1200Mbps/RSSI -35）。

## 其他约束

- 平板 `cmd wifi` 没有省电开关；`cmd wifi set-low-latency-mode` 需要 root（uid 2000 被拒）。
- 原应用自带 adb（`Source\assets\adb-bin\adb.exe` + AdbWinApi.dll + AdbWinUsbApi.dll）。
- `java.exe` 在本机只能写会话工作区（360 环境限制）：与本项目无关，但解释了之前给 Ghidra/JADX 重定向 APPDATA/TEMP 的原因。
