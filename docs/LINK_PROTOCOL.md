# 设备辅助通道协议（桌面端 ↔ 安卓 App）v1

桌面端（Python）和安卓端（Kotlin）之间的**辅助通道**。键鼠数据仍然走 scrcpy 控制通道，
这个通道只做「连接状态 / 延迟 / 设置同步 / 设备信息」，避免污染低延迟路径。

## 传输

```
桌面端: 监听 127.0.0.1:27184 (TCP)
        adb reverse tcp:27184 tcp:27184     # 设备上的 127.0.0.1:27184 → PC
安卓端: 连接 127.0.0.1:27184
```

- 帧格式：**一行一个 UTF-8 JSON**，`\n` 结尾（`readLine` 即可）。
- 双方都必须容忍未知字段；收到未知 `type` 直接忽略。
- 断线后安卓端**不自行重连**：连接由电脑端发起（电脑通过 adb 拉起 App 并携带 `inputsharex_connect`，App 单次尝试）。

## 设备 → PC

```json
{"type":"hello","version":1,"device":"Xiaomi Pad 7 Pro","android":36,"app":"0.1.0"}
{"type":"ping","t":1730000000123}
{"type":"config","placement":1,"keep_awake":true,"lock_refresh":true,"auto_share":true}
```

- `hello`：连接建立后第一帧立即发。`t` 为 `System.currentTimeMillis()`。
- `ping`：每 500ms 一帧，用于桌面端算 RTT。
- `config`：设备端把本地设置回传，也是连接时的初始配置来源。电脑端收到后同步到自己的设置，并把当前配置回发，形成双向收敛。

## PC → 设备

```json
{"type":"welcome","desktop":"DESKTOP-XXX","version":1}
{"type":"pong","t":1730000000123,"ts":1730000000124}
{"type":"ping","t":1730000000125}
{"type":"config","placement":0,"double_trigger":false,"keep_awake":true,"lock_refresh":true}
```

- `welcome`：收到 hello 后立即回；`desktop` 是 PC 主机名。
- `pong`：回应设备发来的 `ping`，原样回 `t`；`ts` 是 PC 收到的本地毫秒时间。
- `ping`：PC 每秒主动发一次（`t` 是 PC 本地毫秒），**设备必须原样回一个 `{"type":"pong","t":<同一个t>}`**。
  这样往返时间用同一台机器的时钟计算，RTT 才是真实的（不加这层会被两端时钟偏差污染）。
- `config`：桌面端设置变化时推送；设备端上报设置后也会回发一次用于收敛。字段可只带部分（设备端按字段存在与否应用）。

## 字段约定

| 字段 | 类型 | 含义 |
| --- | --- | --- |
| `version` | int | 协议版本，当前 1 |
| `placement` | int | 电脑端放置方向：0=右侧 1=左侧 2=下方 3=上方（表示设备位于电脑的哪一侧）。设备端回传时使用同一语义；设备本地边（电脑位于设备的哪一侧）与其按位取反 |
| `double_trigger` | bool | 连击触发（仅电脑端使用） |
| `keep_awake` | bool | 设备端保持常亮/高性能 |
| `lock_refresh` | bool（可选） | 设备端锁定高刷新率；缺省表示旧桌面未下发 |
| `wifi_lock_generation` | long（可选） | Wi-Fi 锁代号，设备端据此重建 Wi-Fi 锁 |

两端行为约定：连接时以设备端设置为准同步电脑端；之后任一端修改都会即时同步到另一端（方向、保持唤醒、锁定高刷新率）。

## 安卓端要求（对应 task-1）

- 前台服务持有 `WifiLock(WIFI_MODE_FULL_HIGH_PERF)` + `PARTIAL_WAKE_LOCK`（后者受“保持唤醒”开关控制）；
- 电脑端拉起后连 `127.0.0.1:27184`，`hello` → 等 `welcome`，然后每 500ms `ping`；
- 界面显示：已连接 / PC 名称 / RTT（ms，跟随 pong 更新）；
- 连接失败不自动重连，服务保持“等待电脑连接”。

## 剪贴板通道（scrcpy 3.3.3 控制 socket）

- 控制 socket 建立后（`video=false` 时它就是服务端首个 socket），服务端先发 **1 字节 dummy + 64 字节设备名**，之后才是设备消息；PC 端由 `server._read_device_meta()` 读掉。
- PC → 设备 `SET_CLIPBOARD`（type 9）：`u64 sequence` + `u8 paste` + `u32 UTF-8 字节数` + 文本；超过 262144 字节按 UTF-8 边界截断。
- PC → 设备 `GET_CLIPBOARD`（type 8）：`u8 copy_key`。
- 设备 → PC：`type 0 CLIPBOARD`（`u32 长度` + UTF-8）、`type 1 ACK_CLIPBOARD`（`u64 sequence`）、`type 2 UHID_OUTPUT`（`u16 id` + `u16 size` + data）。
- 服务端启用 `clipboardAutosync` 时，设备剪贴板变化会主动发 type 0；PC 端以“最近一次同步文本”去重，避免两端来回写造成回环。
