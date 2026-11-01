# 架构与协议

## 1. 原应用（InputShare2 1.1.11.0）逆向结论

| 项目 | 结论 |
| --- | --- |
| 形态 | MSIX Store 包，全信任桌面应用（`runFullTrust`）+ `internetClient` + `DeviceCapability usb` |
| 核心 | `InputShare2.exe`：Python 3.13 + Nuitka standalone 编译；依赖 PySide6/PIL/pywin32/zeroconf/pydantic/orjson/psutil |
| 界面 | `inputshare-ui.exe`：Tauri（Rust + WebView2），`--core-port 23512`，通过本地 HTTP+SSE 与核心通信 |
| 本地 API | 核心监听 `[::1]:23512`：`/api/settings`、`/api/devices`、`/api/adb`、`/api/exit`、`/api/tauri-sse`；唯一写端点 `PUT /api/devices/config`（body `{"placement":0,"config":{...DeviceConfig}}`） |
| 设备端 | 随包 `server_bin/scrcpy-server`（scrcpy 3.3.3 分支，91,904B）+ `server_bin/reporter.apk`（`com.bhznjns.inputshare`，Kotlin/Compose：AccessibilityService 悬浮层 + `LocalSocketServer`（抽象 socket `localabstract:inputshare`）+ kotlinx.serialization 配置同步） |
| ADB | 自带 `adb.exe`，daemon 起在 `127.0.0.1:5038`（避开默认 5037）；无线配对走 `WIFI:T:ADB;S:inputshare-pairing;P:` 二维码 |
| 配置 | `%LOCALAPPDATA%\Packages\BHznJNs.InputShare2_ahfsmsjm0fbat\LocalCache\Local\com.bhznjns.inputshare2\config.json`；每设备项 `placement/auto_connect/mouse_motion_rate/scroll_rate/clipboard_sharing/keep_device_awake` |
| 输入通道 | scrcpy-server 的 UHID：设备上出现 `uinput` 鼠标/键盘设备（原应用分支自报 `scrcpy 3.3.3 Secondary`） |

关键判断：**核心逻辑无法反编译回源码**（Nuitka 编译为 C），只能按可观测行为 + 公开 scrcpy 协议重写；设备端 reporter 的能力（悬浮层/边缘提示线）可选，MVP 不需要。

## 2. 本项目架构

```
┌──────────────────────── Windows (python) ────────────────────────┐
│ capture.py   相对鼠标捕获（ClipCursor + 500Hz 轮询）              │
│ session.py   控制通道会话 + 内置保活（空闲零位移报文 8ms）         │
│ server.py    adb push/forward + app_process 启动 scrcpy-server    │
│ adb.py       adb 进程封装                                         │
└───────────────────────────────┬──────────────────────────────────┘
                                │ TCP 127.0.0.1:27183 (adb forward)
                                ▼
                 localabstract:scrcpy (Android, app_process)
                                │ UHID_CREATE / UHID_INPUT
                                ▼
                 /dev/uhid → 内核 input 子系统 → "inputsharex" 鼠标
```

无视频、无音频，只开控制 socket：`video=false audio=false control=true`。

## 3. 控制协议（scrcpy v3.3.3，按服务端源码核对）

大端序；消息 = `type: u8 + payload`。

| type | 名称 | payload |
| --- | --- | --- |
| 0 | INJECT_KEYCODE | action u8, keycode u32, repeat u32, metaState u32 |
| 1 | INJECT_TEXT | text: u8 长度 + UTF-8 |
| 2 | INJECT_TOUCH_EVENT | action u8, pointerId u64, position u64(x<<32\|y), screenW u16, screenH u16, pressure u16, actionButton u32, buttons u32 |
| 3 | INJECT_SCROLL_EVENT | position u64, hScroll i16, vScroll i16, buttons u32 |
| 8 | GET_CLIPBOARD | copyKey u8 |
| 9 | SET_CLIPBOARD | sequence u64, paste u8, text: u8 长度 + UTF-8 |
| 10 | SET_DISPLAY_POWER | on u8 |
| 11 | ROTATE_DEVICE | — |
| 12 | UHID_CREATE | id u16, vendor u16, product u16, name(u8 长度+UTF-8), descriptor(u16 长度+字节) |
| 13 | UHID_INPUT | id u16, data(u16 长度+字节) |
| 14 | UHID_DESTROY | id u16 |
| 17 | RESET_VIDEO | — |

启动参数（server 端 key=value）：
`tunnel_forward=true video=false audio=false control=true send_dummy_byte=true cleanup=false log_level=info`，并设置 `CLASSPATH=<remote jar>`。

握手：连接 local 端口后先读 1 个 dummy 字节（`0x00`）。

## 4. 鼠标 HID

描述符取自 scrcpy `hid_mouse.c`（5 键 + REL_X/REL_Y/WHEEL/AC_PAN）。输入报文 5 字节：

```
byte0: bit0 左键, bit1 右键, bit2 中键, bit3 side, bit4 extra
byte1: REL_X (-127..127)
byte2: REL_Y
byte3: REL_WHEEL
byte4: REL_HWHEEL
```

一次位移超过 ±127 需拆成多帧。

## 5. 保活（关键优化）

平板 Wi-Fi 空闲时射频进入 PS/DTIM，AP 缓冲下行包 → 输入事件延迟 30–200ms 抖动。
做法：控制通道空闲 >8ms 时发送 `UHID_INPUT` 零位移报文（5 字节 `00 00 00 00 00`），约 6–15KB/s，射频保持活跃；鼠标位移报文本身就是高频心跳，因此只在真正的空闲期补发。

实测（ping PC→平板）：空闲 p50 22–50ms / p90 78–122ms / max 105–214ms → 保活后 p50 10–13ms / p90 30–56ms / max 59–146ms。

## 6. 与原应用的差异（迭代方向）

| 能力 | 原应用 | 本项目 |
| --- | --- | --- |
| 输入通道 | UHID（scrcpy 分支） | UHID（上游 scrcpy 3.3.3） |
| 保活 | 无（依赖系统） | 内置零位移报文 |
| 边缘切换 | 有 | 待做 v0.4 |
| 悬浮层/边缘提示 | reporter.apk 无障碍服务 | 暂不需要（可选 v0.8） |
| 视频镜像 | 有 | 待做 v0.6（可选） |
| 界面 | Tauri + WebView2 | 先 CLI，v0.5 托盘 |
