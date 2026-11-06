"""桌面端控制器：把 adb / scrcpy-server / 会话 / 捕获 / 设备辅助通道串起来。

GUI（ui/）只跟这个类打交道，不直接碰 socket/adb，这样界面和引擎互不干扰。
所有对外通知都走 Qt Signal，跨线程发射是安全的。
"""
from __future__ import annotations

import json
import math
import os
import subprocess
import threading
import time
from pathlib import Path

from PySide6.QtCore import QObject, QTimer, Signal

from . import __version__, clipboard, link
from .adb import Adb, AdbError
from .androidapp import (
    ensure_installed as ensure_android_app_installed,
    launch as launch_android_app,
)
from .capture import InputCapture, MOD_ALT, MOD_CONTROL, VK_Q, _dbg as _dbg_edge, entry_target
from .link import DeviceLink
from .pairing import (
    DEFAULT_WATCH_TIMEOUT,
    PairResult,
    QrPairing,
    build_payload,
    candidate_connect_addresses,
    make_credentials,
    parse_services,
    try_connect,
)
from .server import DEFAULT_LOCAL_PORT, ScrcpyServer, default_jar_path
from .session import ControlSession
from .wifi import enable_background_wifi_lock
from .devices import TRANSPORTS, build_device_entries, profile_key, validate_profile
from .pointer import PointerMonitor

CONFIG_DIR = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "inputsharex"
CONFIG_PATH = CONFIG_DIR / "config.json"

DEFAULT_CONFIG = {
    "serial": "",
    "keepalive_ms": 8,
    "cadence_ms": 4,
    "edge_switch": True,
    "edge_zone": 6,
    "edge_on_extended": False,
    "hotkey": "ctrl+alt+q",
    "start_armed": False,
    "auto_connect": True,
    "double_trigger": False,
    "arrow_switch_images": False,
    "placement": 0,
    "android_keep_awake": True,
    "lock_refresh": True,
    "background_wifi_lock": True,
    "wifi_direct": True,
    "device_profiles": [],
    "hidden_profiles": [],
    "selected_profile": "",
}

HOTKEYS = {
    "ctrl+alt+q": (MOD_CONTROL | MOD_ALT, VK_Q),
    "ctrl+alt+f9": (MOD_CONTROL | MOD_ALT, 0x78),
    "ctrl+alt+f10": (MOD_CONTROL | MOD_ALT, 0x79),
    "f12": (0, 0x7B),
}


def load_config() -> dict:
    config = dict(DEFAULT_CONFIG)
    try:
        config.update(json.loads(CONFIG_PATH.read_text(encoding="utf-8")))
    except (OSError, ValueError):
        pass
    return config


def save_config(config: dict) -> None:
    try:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        CONFIG_PATH.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError:
        pass


class AppController(QObject):
    androidMessage = Signal(object)
    edgeConfirmed = Signal(object)
    # 状态：idle | discovering | connecting | connected | sharing | error
    stateChanged = Signal(str, str)          # (state, detail)
    devicesChanged = Signal(list)            # [{serial, state, name, addr, wired}]
    statsChanged = Signal(dict)              # {rx?, sent_bytes, sent_packets, rtt_ms, fps, armed}
    logMessage = Signal(str)
    errorRaised = Signal(str)
    paired = Signal(bool, str)               # (ok, message)
    qrPairStatus = Signal(str)               # 二维码配对进度文案
    qrPairFinished = Signal(bool, str, str)  # (ok, serial, message)
    qrDeviceConnected = Signal(str)          # 后台等待连接成功后的 serial
    configChanged = Signal(dict)             # 安卓端同步过来的设置变更

    def __init__(self, adb_path: str | None = None, adb_port: int = 5038,
                 local_port: int = DEFAULT_LOCAL_PORT, link_port: int = link.DEFAULT_LINK_PORT,
                 parent: QObject | None = None):
        super().__init__(parent)
        self.config = load_config()
        self.adb = Adb(adb_path=adb_path or self.config.get("adb_path"), port=adb_port)
        self.local_port = local_port
        self.link_port = link_port
        self.state = "idle"
        self.devices: list[dict] = []
        self._models: dict[str, str] = {}   # serial -> 型号名（getprop 缓存）
        self._hardware = {}
        self._pending_start = False         # 未连接时请求共享 → 连上后自动开始
        self.serial: str | None = None
        self.server: ScrcpyServer | None = None
        self.session: ControlSession | None = None
        self.capture: InputCapture | None = None
        self.pointer_monitor = None
        self._enter_fraction: float | None = None
        self._discovered_devices = []
        self.device_link: DeviceLink | None = None
        self._armed = False
        # 共享期间临时打开“物理键盘也显示软键盘”，结束/退出时恢复用户原设置。
        self._ime_hard_kb_old: str | None = None
        self._stop = False
        self._sharing = False
        self._sharing_thread = None
        self._sharing_lifecycle_lock = threading.RLock()
        self._restart_waiting = False
        self._selected_transport = "tls" if self.config.get("wifi_direct", True) else "adb"
        remembered_transport = self.config.get("selected_profile", "").partition("|")[0]
        if remembered_transport in TRANSPORTS:
            self._selected_transport = remembered_transport
        self._active_profile = self.config.get("selected_profile", "")
        self._capture_generation = 0
        self._auto_start_requested = False
        self._suspend_auto_connect = False
        self._switching = False
        self.androidMessage.connect(self._handle_android_message)
        self.edgeConfirmed.connect(self._on_verified_edge)
        self._last_stats = (0.0, 0, 0)
        self._since = time.perf_counter()
        self._discovery_thread: threading.Thread | None = None
        self._link_thread_lock = threading.Lock()
        self._qr_session: QrPairing | None = None
        self._qr_thread: threading.Thread | None = None
        self._qr_watch_event: threading.Event | None = None
        self._qr_watch_thread: threading.Thread | None = None
        self._screen_size: tuple[int, int] | None = None
        self._clip_last = ""
        self._arrow_pressed: set[int] = set()

        self._stats_timer = QTimer(self)
        self._stats_timer.timeout.connect(self._emit_stats)
        self._stats_timer.start(500)

        self._auto_reconnect = self.config.get("auto_connect", True)

    # -- helpers ---------------------------------------------------
    def _set_state(self, state: str, detail: str = "") -> None:
        self.state = state
        self.stateChanged.emit(state, detail)
        self._publish_sharing_state()

    def _publish_sharing_state(self):
        link = self.device_link
        if link is None:
            return
        actual = self.server.transport if self.server else ""
        transport = ("usb" if self.serial and ":" not in self.serial else
                     "tls" if actual == "tls-direct" else "adb" if actual else self._selected_transport)
        payload = {"type": "sharing_state", "sharing": bool(self.state == "sharing" and self.session and not self.session.closed),
                   "phase": self.state, "input_armed": self._armed, "transport": transport,
                   "transport_label": TRANSPORTS.get(transport, ""), "placement": self.config.get("placement", 0),
                   "capture_generation": self._capture_generation}
        link.sharing_state = payload
        link.send(payload)

    def _handle_android_message(self, event):
        if event["source"] is not self.device_link:
            return
        msg = event["message"]
        if msg.get("type") == "hello":
            self._publish_sharing_state()
            if self._auto_start_requested and not self._suspend_auto_connect and msg.get("auto_share", True):
                self.start_sharing()
        elif msg.get("type") == "sharing_request" and isinstance(msg.get("enabled"), bool):
            self.start_sharing() if msg["enabled"] else self.stop_sharing()
        elif msg.get("type") == "config":
            self._apply_android_config(msg)

    def _apply_android_config(self, msg: dict) -> None:
        """安卓端上报的设置：以当前连接的设备为准同步到电脑端，并把电脑端配置回传。"""
        changes: dict = {}
        placement = msg.get("placement")
        if isinstance(placement, int) and not isinstance(placement, bool) and placement in range(4):
            # 协议里的 placement 已是电脑端语义（安卓端按自己的边界取反后发送），这里直接采用。
            if placement != int(self.config.get("placement", 0)):
                changes["placement"] = placement
        keep_awake = msg.get("keep_awake")
        if isinstance(keep_awake, bool) and keep_awake != bool(self.config.get("android_keep_awake", True)):
            changes["android_keep_awake"] = keep_awake
        lock_refresh = msg.get("lock_refresh")
        if isinstance(lock_refresh, bool) and lock_refresh != bool(self.config.get("lock_refresh", True)):
            changes["lock_refresh"] = lock_refresh
        if changes:
            self.config.update(changes)
            save_config(self.config)
            if self.capture is not None and "placement" in changes:
                self.capture.set_edge(int(changes["placement"]))
                if self.pointer_monitor is not None:
                    self.pointer_monitor.set_enabled(self._armed, self._capture_generation, int(changes["placement"]))
            self._publish_sharing_state()
            self.configChanged.emit(dict(changes))
            self._log("已按安卓端设置同步：" + ", ".join(
                f"{self._config_name(key)}={self._config_label(key, value)}" for key, value in changes.items()))
        # 无论安卓端是否带来变化都回传电脑端当前配置：安卓端按字段存在与否应用，两端最终收敛。
        if self.device_link is not None:
            self.device_link.send_config(self._link_config())

    @staticmethod
    def _config_name(key: str) -> str:
        return {"placement": "放置方向", "android_keep_awake": "保持唤醒", "lock_refresh": "锁定高刷新率"}.get(key, key)

    @staticmethod
    def _config_label(key: str, value) -> str:
        if key == "placement":
            return ("右侧", "左侧", "下方", "上方")[int(value)]
        return "开" if value else "关"

    def _on_verified_edge(self, event):
        if not (self.capture and self.capture.armed and not self.capture.buttons
                and event.get("generation") == self._capture_generation
                and event.get("placement") == self.config.get("placement", 0)
                and self.config.get("placement", 0) in range(4)):
            _dbg_edge(f"verified-edge-rejected event={event} armed={getattr(self.capture, 'armed', None)} gen={self._capture_generation}")
            return
        try:
            x, y, mx, my = (float(event[k]) for k in ("x", "y", "max_x", "max_y"))
        except (KeyError, ValueError, TypeError):
            return
        if not all(math.isfinite(v) for v in (x, y, mx, my)) or mx <= 0 or my <= 0:
            return
        if not (0 <= x <= mx and 0 <= y <= my):
            return
        boundary = (x <= .5, x >= mx - .5, y <= .5, y >= my - .5)[self.config.get("placement", 0)]
        if boundary:
            # 安卓端沿共享边的比例位置：横向相邻取 y，纵向相邻取 x；
            # 坐标已在探针里换算成当前旋转后的逻辑坐标，因此横竖屏都通用。
            if self.config.get("placement", 0) in (0, 1):
                fraction = y / my
            else:
                fraction = x / mx
            self.capture.return_from_device(fraction)

    def start_sharing_on_connect(self):
        self._auto_start_requested = True
        if self.device_link and self.device_link.connected and self.device_link.device_info.get("auto_share", True):
            self.start_sharing()

    def _log(self, message: str) -> None:
        self.logMessage.emit(message)

    # -- 设备发现 --------------------------------------------------
    def start_discovery(self) -> None:
        if self._discovery_thread and self._discovery_thread.is_alive():
            return
        self._stop = False
        self._discovery_thread = threading.Thread(
            target=self._discovery_loop, name="discovery", daemon=True
        )
        self._discovery_thread.start()

    def _discovery_loop(self) -> None:
        self._set_state("discovering")
        while not self._stop:
            try:
                self.adb.start_server()
                mdns = self.adb.mdns_services()
                online = dict(self.adb.devices())
                # mDNS 只告诉我们在哪，不会自动连上；无线调试重启后端口会变，
                # 所以这里主动对还没上线的地址做一次 adb connect。
                missing = [addr for _n, addr in mdns if online.get(addr) != "device"]
                if missing and not self._sharing and not self.serial:
                    for addr in missing[:3]:
                        try:
                            self.adb.connect(addr)
                        except AdbError:
                            pass
                    time.sleep(0.3)
                    online = dict(self.adb.devices())
                found: list[dict] = []
                for name, addr in mdns:
                    found.append({"serial": addr, "state": online.get(addr, "offline"),
                                  "name": name, "addr": addr, "wired": False})
                for serial, state in online.items():
                    if serial.endswith("_adb-tls-connect._tcp"):
                        continue
                    entry = next((f for f in found if f["serial"] == serial), None)
                    if entry is None:
                        entry = {"serial": serial, "state": state, "name": serial,
                                 "addr": serial if ":" in serial else "", "wired": ":" not in serial}
                        found.append(entry)
                    entry["state"] = state
                # 已连接的设备换成真实型号名（mDNS 名字形如 adb-<serial>-xxxx，用户看不懂）
                for entry in found:
                    if entry["state"] != "device":
                        continue
                    serial = entry["serial"]
                    if serial not in self._models:
                        self._models[serial] = self.adb.model_name(serial) or ""
                    if self._models[serial]:
                        entry["name"] = self._models[serial]
                    if serial not in self._hardware:
                        try:
                            self._hardware[serial] = self.adb.run_for(serial, "shell", "getprop", "ro.serialno", timeout=4).stdout.decode().strip()
                            if self._hardware[serial] == "unknown":
                                self._hardware[serial] = ""
                        except (AdbError, OSError, subprocess.SubprocessError):
                            self._hardware[serial] = ""
                    entry["hardware_id"] = self._hardware.get(serial, "")
                # 只保留连上的 / 可发现的，按可用优先排序
                found.sort(key=lambda d: (d["state"] != "device", ":" in d["serial"], d["serial"]))
                self._discovered_devices = found
                found = build_device_entries(found, list(self.config.get("device_profiles", [])),
                                             set(self.config.get("hidden_profiles", [])))
                if found != self.devices:
                    self.devices = found
                    self.devicesChanged.emit(found)
                if self.state == "discovering":
                    self._set_state("idle")
                # 连接健康检查：设备掉线（拔 USB / 无线调试重启换端口）时自动重连
                if self.serial:
                    if self.serial not in online:
                        self._missing = getattr(self, "_missing", 0) + 1
                        if self._missing >= 2:
                            self._log("设备已断开，重新搜索…")
                            self._missing = 0
                            if self._sharing:
                                try:
                                    self.stop_sharing()
                                except Exception:  # noqa: BLE001 - 收尾失败也要继续重连
                                    pass
                            if self._sharing_thread:
                                self._sharing_thread.join(timeout=6)
                            if self.device_link:
                                self.device_link.stop()
                                self.device_link = None
                            self.serial = ""
                            self.adb.serial = ""
                            self.config["serial"] = ""
                            save_config(self.config)
                            self._set_state("idle")
                    else:
                        self._missing = 0
                # 已连着就不再重复连接；只在空闲且没有 serial 时自动挑一台
                if self._auto_reconnect and not self._sharing and not self.serial and not self._suspend_auto_connect and not self._switching:
                    ready = [d for d in found if d["state"] == "device"]
                    # USB 直连优先：实测抖动 p99 9ms vs 无线 22ms，有 USB 就不用无线
                    wired = [d for d in ready if ":" not in d["serial"]]
                    candidate = next((d for d in ready if d["id"] == self.config.get("selected_profile")), None)
                    if candidate is None and self.config.get("selected_profile"):
                        identity = self.config.get("selected_hardware_id")
                        candidate = next((d for d in ready if identity and d.get("hardware_id") == identity
                                          and d["transport"] == self._selected_transport), None)
                        if candidate is None and identity and self._selected_transport == "usb":
                            candidate = next((d for d in ready if d.get("hardware_id") == identity and d["transport"] == "tls"), None)
                    if candidate is None and not self.config.get("selected_profile"):
                        candidate = wired[0] if wired else None
                    if candidate is None and not self.config.get("selected_profile"):
                        target = self.config.get("serial") or ""
                        candidate = next((d for d in ready if d["serial"] == target), None)
                    if candidate is None and not self.config.get("selected_profile"):
                        ready.sort(key=lambda d: (":" in d["serial"], d["serial"]))
                        candidate = ready[0] if ready else None
                    if candidate is not None:
                        self.connect_profile(candidate["id"])
            except (AdbError, FileNotFoundError, OSError) as exc:
                self.errorRaised.emit(str(exc))
                self._set_state("error", str(exc))
            time.sleep(3.0)

    # -- 连接 ------------------------------------------------------
    def connect(self, serial: str) -> None:
        if self._sharing or self.serial or self.state == "connecting":
            return
        self._set_state("connecting", serial)
        threading.Thread(target=self._connect_worker, args=(serial,), daemon=True).start()

    def connect_profile(self, identifier):
        device = next((d for d in self.devices if d["id"] == identifier), None)
        if device is None or self._switching:
            return
        if self.serial == device["serial"] and self._active_profile == identifier:
            return
        self._switching = True
        self._suspend_auto_connect = False
        def switch():
            was_sharing = self._sharing or self._pending_start
            try:
                self.stop_sharing()
                if self._sharing_thread:
                    self._sharing_thread.join(timeout=8)
                    if self._sharing_thread.is_alive():
                        raise RuntimeError("旧共享尚未退出，请稍后重试")
                if self.device_link:
                    self.device_link.stop()
                    self.device_link = None
                self.serial = None
                self._active_profile = identifier
                self._selected_transport = device["transport"]
                self.config["selected_profile"] = identifier
                self.config["selected_hardware_id"] = device.get("hardware_id", "")
                self._pending_start = was_sharing
                self._set_state("connecting", device["serial"])
                self._connect_worker(device["serial"])
            except (OSError, RuntimeError) as exc:
                self._set_state("error", str(exc))
            finally:
                self._switching = False
        threading.Thread(target=switch, name="device-switch", daemon=True).start()

    def save_device(self, name, serial, transport, previous_id=None):
        profile = validate_profile(name, serial, transport)
        profiles = list(self.config.get("device_profiles", []))
        if any(p["id"] == profile["id"] and p["id"] != previous_id for p in profiles):
            raise ValueError("该连接配置已存在")
        profiles = [p for p in profiles if p["id"] not in (previous_id, profile["id"])] + [profile]
        hidden = set(self.config.get("hidden_profiles", []))
        if previous_id and previous_id != profile["id"]:
            hidden.add(previous_id)
        hidden.discard(profile["id"])
        self.config.update(device_profiles=profiles, hidden_profiles=sorted(hidden))
        save_config(self.config)
        self.devices = build_device_entries(self._discovered_devices, profiles, hidden)
        # Rebuild from base endpoints in the discovery loop; publish saved changes now.
        self.devicesChanged.emit(self.devices)
        if previous_id and previous_id == self._active_profile:
            self.connect_profile(profile["id"])
        return profile["id"]

    def delete_device(self, identifier):
        self.config["device_profiles"] = [p for p in self.config.get("device_profiles", []) if p["id"] != identifier]
        self.config["hidden_profiles"] = sorted(set(self.config.get("hidden_profiles", [])) | {identifier})
        self.devices = [d for d in self.devices if d["id"] != identifier]
        if self._active_profile == identifier:
            self._suspend_auto_connect = True
            self.config["selected_profile"] = ""
            self.config["selected_hardware_id"] = ""
            def remove_active():
                self.stop_sharing()
                if self._sharing_thread:
                    self._sharing_thread.join(timeout=8)
                if self.device_link:
                    self.device_link.stop()
                    self.device_link = None
                self.serial = None
                self._active_profile = ""
                self._set_state("idle")
            threading.Thread(target=remove_active, daemon=True).start()
        save_config(self.config)
        self.devicesChanged.emit(self.devices)

    def _connect_worker(self, serial: str) -> None:
        try:
            resolved = self.adb.resolve_serial(serial)
            if resolved != serial:
                raise AdbError("所选设备尚未连接，请检查地址或有线授权")
            self.serial = resolved
            if not self.config.get("selected_hardware_id"):
                try:
                    identity = self.adb.run_for(resolved, "shell", "getprop", "ro.serialno", timeout=4).stdout.decode().strip()
                    self.config["selected_hardware_id"] = identity if identity != "unknown" else ""
                except (AdbError, OSError, subprocess.SubprocessError):
                    pass
            self.adb.serial = self.serial
            self.config["serial"] = self.serial
            save_config(self.config)
            self._log(f"设备已连接：{self.serial}")
            self._set_state("connected", self.serial)
            self._ensure_android_app(self.serial)
            self._start_link()
            # 辅助通道（adb reverse）就绪后再拉起 App，App 会主动连上来；
            # 连接完全由电脑端发起，安卓端平时不会自行重连。
            self._launch_android_app(self.serial)
            if self._pending_start:
                self._pending_start = False
                self._log("设备已就绪，开始共享…")
                self.start_sharing()
        except (AdbError, FileNotFoundError, OSError) as exc:
            self._log(f"连接失败：{exc}")
            self._set_state("error", str(exc))
            if self._auto_reconnect:
                threading.Timer(2.0, lambda: self.connect(serial)).start()

    def _ensure_android_app(self, serial: str) -> None:
        """连接后确认安卓端装有配套 App；缺了自动安装（失败不影响键鼠共享）。"""
        try:
            ensure_android_app_installed(self.adb, serial, log=self._log)
        except Exception as exc:  # 自动安装是增强能力，不能拖垮连接流程
            self._log(f"检查/安装安卓端 App 时出错：{exc}")

    def _launch_android_app(self, serial: str) -> None:
        """电脑端连接成功后自动拉起安卓 App，并让它连接辅助通道。"""
        try:
            launch_android_app(self.adb, serial, log=self._log)
        except Exception as exc:  # 拉起失败不应打断连接/共享
            self._log(f"自动打开安卓端 App 时出错：{exc}")

    def _start_link(self) -> None:
        if self.device_link is not None:
            try:
                self.device_link.stop()
            except Exception:
                pass
        if not self.serial:
            return
        self.device_link = DeviceLink(self.adb, self.serial, port=self.link_port)
        active_link = self.device_link
        active_link.on_message = lambda message: self.androidMessage.emit({"source": active_link, "message": message})
        try:
            self.device_link.start()
            self._log(f"设备辅助通道已启动：127.0.0.1:{self.link_port}")
        except OSError as exc:
            self._log(f"辅助通道启动失败（{exc}），不影响键鼠共享")
        self._publish_sharing_state()

    def _link_config(self) -> dict:
        """下发给安卓端 App 的配置（未知字段在旧版 App 上会被忽略）。"""
        return {
            "double_trigger": self.config.get("double_trigger", False),
            "placement": self.config.get("placement", 0),
            "keep_awake": self.config.get("android_keep_awake", True),
            "lock_refresh": bool(self.config.get("lock_refresh", True)),
            "wifi_lock_generation": getattr(self, "_wifi_lock_generation", 0),
        }

    def disconnect(self) -> None:
        self.stop_sharing()
        if self.device_link is not None:
            self.device_link.stop()
            self.device_link = None
        self.serial = None
        self._set_state("idle")

    # -- 共享 ------------------------------------------------------
    def start_sharing(self, start_armed: bool | None = None) -> None:
        with self._sharing_lifecycle_lock:
            self._start_sharing_locked(start_armed)

    def _start_sharing_locked(self, start_armed: bool | None = None) -> None:
        if self._sharing:
            return
        previous = self._sharing_thread
        if previous and previous.is_alive():
            self._pending_start = True
            if self._restart_waiting:
                return
            self._restart_waiting = True
            def restart_after_cleanup():
                previous.join()
                with self._sharing_lifecycle_lock:
                    self._restart_waiting = False
                    if self._pending_start and not self._stop:
                        self._pending_start = False
                        self._start_sharing_locked(start_armed)
            threading.Thread(target=restart_after_cleanup, name="sharing-restart", daemon=True).start()
            return
        if not self.serial:
            # 还没连上设备：记住意图，连接成功后自动开始（--start-sharing / 开机自启用）
            self._pending_start = True
            self._log("等待设备连接后自动开始共享…")
            return
        self._sharing = True
        self._set_state("starting", self.serial)
        self._sharing_thread = threading.Thread(target=self._sharing_worker,
                         args=(self.config.get("start_armed", False) if start_armed is None else start_armed,),
                         daemon=True)
        self._sharing_thread.start()

    def _sharing_worker(self, start_armed: bool) -> None:
        try:
            self._set_state("connecting", "启动服务端…")
            interval = max(self.config.get("keepalive_ms", 8), 0) / 1000
            cadence = max(self.config.get("cadence_ms", 4), 0)
            # 无线链路：把合并节拍对齐到 120Hz 一帧（8.33ms）。实测 Wi-Fi 下 4ms 节拍的
            # 多个小包会被聚合成一批同时到达（指针一格跳 2-3px），对齐到每帧一次更稳。
            if ":" in (self.serial or ""):
                cadence = max(cadence, 8.33)
                try:
                    if self.config.get("background_wifi_lock", True) and enable_background_wifi_lock(self.adb, self.serial):
                        self._wifi_lock_generation = time.time_ns()
                        if self.device_link is not None:
                            # 只带 Wi-Fi 锁代号，避免全量配置覆盖安卓端刚同步的设置
                            self.device_link.send_config({"wifi_lock_generation": self._wifi_lock_generation})
                        self._log("已启用后台高性能 Wi-Fi 锁兼容设置")
                    else:
                        self._log("设备不支持后台 Wi-Fi 锁兼容设置，继续使用现有连接")
                except (AdbError, OSError, subprocess.SubprocessError) as exc:
                    self._log(f"后台 Wi-Fi 锁设置失败：{exc}，继续共享")
            server = ScrcpyServer(self.adb, self.serial, local_port=self.local_port,
                                  jar_path=default_jar_path(),
                                  direct=self._selected_transport == "tls")
            self.server = server
            sock = server.start()
            if server.transport == "tls-direct":
                self._log("无线控制已使用加密直连通道")
            elif server.direct_error:
                self._log(f"加密直连暂不可用，已回退 ADB：{server.direct_error}")
            session = ControlSession(sock, keepalive_interval=interval, cadence_ms=cadence)
            self.session = session
            self._clip_last = ""
            self._arrow_pressed.clear()
            self._screen_size = self._android_screen_size()
            if self._screen_size:
                session.set_screen_size(*self._screen_size)
            session.on_clipboard = self._on_device_clipboard
            session.create_mouse("inputsharex")
            # 键盘设备不在共享开始时就创建：安卓把 UHID 键盘当成物理键盘，
            # 会隐藏自带软键盘。推迟到真正接管鼠标时再创建。
            session.start()
            # 共享期间允许“物理键盘 + 软键盘”并存，避免 UHID 键盘顶掉安卓自带输入法；
            # 结束共享时（_teardown_sharing）恢复原来的系统设置。
            self._schedule_ime_with_hard_keyboard(True)
            if not self._sharing:
                return
            try:
                self.pointer_monitor = PointerMonitor(self.adb, self.serial,
                    str(Path(server.jar_path).with_name("inputsharex-relay.jar")), self.edgeConfirmed.emit,
                    lambda: self._log("边缘返回验证已停止，可按 Ctrl+Alt+Q 返回") if self._sharing else None)
                self.pointer_monitor.start()
            except (AdbError, OSError, RuntimeError, subprocess.SubprocessError):
                if self.pointer_monitor:
                    self.pointer_monitor.stop()
                    self.pointer_monitor = None
                self._log("自动边缘返回暂不可用，可按 Ctrl+Alt+Q 返回")
            self._log("UHID 鼠标已创建，保活 %dms，合并周期 %.0fms"
                      % (self.config.get("keepalive_ms", 8), cadence))

            mods, vk = HOTKEYS.get(self.config.get("hotkey", "ctrl+alt+q"), HOTKEYS["ctrl+alt+q"])
            capture = InputCapture(
                on_move=lambda dx, dy, buttons: session.mouse_move(dx, dy, buttons),
                on_button=lambda buttons: session.mouse_button(buttons),
                on_wheel=lambda v, h: session.scroll(v, h),
                on_key=self._on_key,
                on_clipboard=self._on_windows_clipboard,
                on_toggle=self._on_arm_toggle,
                on_edge=self._on_edge,
                on_enter=self._on_edge_enter,
                hotkey_mods=mods,
                hotkey_vk=vk,
                edge_switch=bool(self.config.get("edge_switch", True)),
                edge=int(self.config.get("placement", 0)),
                edge_zone=int(self.config.get("edge_zone", 6)),
                allow_extended_edge=bool(self.config.get("edge_on_extended", False)),
            )
            self.capture = capture
            capture.can_capture = lambda: (self._sharing and self.state == "sharing"
                and self.session is session and not session.closed
                and session._thread is not None and session._thread.is_alive()
                and self.pointer_monitor is not None and self.pointer_monitor.available)
            capture.start()
            if not self._sharing:
                return
            self._set_state("sharing", self.serial)
            if start_armed:
                capture.arm()
            while session and not session.closed:
                if self._sharing is False:
                    break
                time.sleep(0.3)
            if self.session and self.session.closed:
                self._log("控制通道断开")
        except (AdbError, FileNotFoundError, OSError, RuntimeError) as exc:
            self._log(f"启动共享失败：{exc}")
            self._set_state("error", str(exc))
        finally:
            self._sharing = False
            self._teardown_sharing()
            if self.state not in ("error",):
                self._set_state("connected" if self.serial else "idle")

    def _teardown_sharing(self) -> None:
        self._clip_last = ""
        self._screen_size = None
        self._arrow_pressed.clear()
        if self.capture is not None:
            try:
                self.capture.stop()
            except Exception:
                pass
            self.capture = None
        if self.pointer_monitor:
            try:
                self.pointer_monitor.stop()
            except (OSError, subprocess.SubprocessError):
                pass
            self.pointer_monitor = None
        if self.session is not None:
            try:
                self.session.destroy_devices()
            except Exception:
                pass
            self.session.close()
            self.session = None
        if self.server is not None:
            try:
                self.server.stop()
            except Exception:
                pass
            self.server = None
        self._armed = False
        self._schedule_ime_with_hard_keyboard(False)

    # -- 安卓软键盘 ------------------------------------------------
    def _apply_ime_with_hard_keyboard(self, enable: bool) -> None:
        """共享期间让安卓在“物理键盘已连接”时仍显示自带软键盘，结束后还原。

        键盘流转用 UHID 键盘，安卓会把它当成外接物理键盘；系统默认在检测到
        物理键盘时隐藏软输入法（show_ime_with_hard_keyboard=0），表现为接管
        后平板自带的输入法键盘不再弹出。这里只在共享期间临时打开该设置，
        共享结束/程序退出时恢复原值，避免永久改掉用户的系统设置。
        """
        adb, serial = self.adb, self.serial
        if adb is None or not serial:
            return
        try:
            if enable:
                if self._ime_hard_kb_old is None:
                    proc = adb.run_for(
                        serial, "shell", "settings", "get", "secure",
                        "show_ime_with_hard_keyboard", check=False, timeout=6,
                    )
                    value = (proc.stdout or b"").decode("utf-8", "replace").strip()
                    self._ime_hard_kb_old = value or "0"
                adb.run_for(
                    serial, "shell", "settings", "put", "secure",
                    "show_ime_with_hard_keyboard", "1", check=False, timeout=6,
                )
            else:
                old = self._ime_hard_kb_old
                self._ime_hard_kb_old = None
                if old is not None:
                    adb.run_for(
                        serial, "shell", "settings", "put", "secure",
                        "show_ime_with_hard_keyboard", old, check=False, timeout=6,
                    )
        except Exception:
            pass

    def _schedule_ime_with_hard_keyboard(self, enable: bool) -> None:
        try:
            threading.Thread(
                target=self._apply_ime_with_hard_keyboard,
                args=(enable,),
                name="ime-hard-keyboard",
                daemon=True,
            ).start()
        except Exception:
            pass

    def stop_sharing(self) -> None:
        with self._sharing_lifecycle_lock:
            self._sharing = False
            self._pending_start = False
            if self.capture:
                self.capture.disarm()
            if self.session:
                self.session.close()
        self._set_state("connected" if self.serial else "idle")

    # -- 接管 ------------------------------------------------------
    def _on_arm_toggle(self, armed: bool) -> None:
        self._armed = armed
        self._capture_generation = time.time_ns()
        _dbg_edge(f"arm-toggle armed={armed} gen={self._capture_generation} probe_available={getattr(self.pointer_monitor, 'available', None)}")
        if self.pointer_monitor:
            self.pointer_monitor.set_enabled(armed, self._capture_generation, self.config.get("placement", 0))
        if not armed:
            self._arrow_pressed.clear()
        if not armed and self.session is not None:
            # 切回电脑：清掉卡住的按键，并销毁 UHID 键盘设备，
            # 让安卓恢复自带软键盘（物理键盘设备存在时系统会隐藏软键盘）。
            try:
                self.session.release_keys()
                self.session.destroy_keyboard()
            except Exception:
                pass
        if armed and self.session is not None:
            # 接管时才挂上 UHID 键盘（安卓会把它当物理键盘，未接管时不该存在）。
            try:
                self.session.create_keyboard("inputsharex")
            except Exception:
                pass
        if armed and self.session is not None and self._enter_fraction is not None:
            fraction = self._enter_fraction
            self._enter_fraction = None
            threading.Thread(target=self._align_pointer_to_shared_edge,
                             args=(fraction,), name="align-pointer", daemon=True).start()
        self._publish_sharing_state()
        if armed:
            self._log("鼠标已切换到安卓端（移动回原侧边缘可切回）")
        else:
            self._log("鼠标已切回电脑")

    def _on_key(self, vk: int, scan: int, extended: bool, up: bool) -> None:
        """接管期间把 PC 键盘事件转发到安卓（由低级键盘钩子回调）。"""
        session = self.session
        if session is None or session.closed:
            return
        try:
            # 图文切换模式：方向键左右改成横向滑动，不再发方向键（抖音图文用）。
            if (self.config.get("arrow_switch_images", False) and vk in (0x25, 0x27)
                    and self._screen_size):
                if up:
                    self._arrow_pressed.discard(vk)
                    return
                if vk in self._arrow_pressed:
                    return
                self._arrow_pressed.add(vk)
                session.swipe_horizontal(forward=(vk == 0x27))
                return
            session.keyboard_event(vk, scan, extended, up)
        except Exception:
            pass

    def _on_windows_clipboard(self, text: str) -> None:
        """电脑剪贴板变化：同步到安卓端。"""
        if not text or text == self._clip_last:
            return
        session = self.session
        if session is None or session.closed:
            return
        try:
            if session.set_clipboard_text(text):
                self._clip_last = text
        except Exception:
            pass

    def _on_device_clipboard(self, text: str) -> None:
        """安卓端剪贴板变化：写回电脑剪贴板。"""
        if not text or text == self._clip_last:
            return
        try:
            if clipboard.write_text(text):
                self._clip_last = text
                self._log("已从安卓端同步剪贴板")
        except Exception:
            pass

    def _android_screen_size(self) -> tuple[int, int] | None:
        """安卓端当前逻辑分辨率（图文切换手势用）；拿不到返回 None。"""
        serial = self.serial
        if not serial:
            return None
        width = height = 0
        try:
            text = self.adb.run_for(serial, "shell", "wm", "size", check=False,
                                    timeout=10).stdout.decode("utf-8", "replace")
        except Exception:
            text = ""
        override = physical = ""
        for line in text.splitlines():
            line = line.strip()
            if line.startswith("Override size:"):
                override = line.split(":", 1)[1].strip()
            elif line.startswith("Physical size:"):
                physical = line.split(":", 1)[1].strip()
        raw = override or physical
        if "x" in raw:
            try:
                w, h = raw.lower().split("x", 1)
                width, height = int(w), int(h)
            except ValueError:
                width = height = 0
        if width <= 0 or height <= 0:
            return None
        rotated = False
        try:
            dump = self.adb.run_for(serial, "shell", "dumpsys", "input", check=False,
                                    timeout=10).stdout.decode("utf-8", "replace")
            for line in dump.splitlines():
                if "SurfaceOrientation" in line:
                    rotated = line.rsplit(":", 1)[-1].strip() in ("1", "3")
                    break
        except Exception:
            pass
        return (height, width) if rotated else (width, height)

    def _on_edge(self, edge: int, entered: bool) -> None:
        names = {0: "右", 1: "左", 2: "下", 3: "上"}
        side = names.get(int(edge), str(edge))
        self._log(f"边缘切换：{side}侧 → 安卓端" if entered else f"边缘切换：{side}侧 → 电脑")

    def _on_edge_enter(self, edge: int, fraction) -> None:
        self._enter_fraction = None if fraction is None else max(0.0, min(1.0, float(fraction)))

    def _align_pointer_to_shared_edge(self, fraction: float) -> None:
        """进入安卓端后，把安卓端光标放到共享边内侧的等比位置。

        对齐期间让探针暂时不报 EDGE（仍持续上报 POSITION），避免中途误触发返回。
        """
        edge = getattr(self.capture, "edge", None)
        monitor = self.pointer_monitor
        if edge is None or monitor is None:
            return

        try:
            monitor.suppress_edges(6000)
        except Exception:
            pass

        gain = 1.0
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            if not self._armed or self.session is None or self.session.closed:
                return
            pos = monitor.latest_position(0.5)
            if pos is None:
                time.sleep(0.03)
                continue
            x, y, max_x, max_y = pos
            tx, ty = entry_target(edge, fraction, max_x, max_y)
            dx, dy = tx - x, ty - y
            if abs(dx) <= 6 and abs(dy) <= 6:
                return
            # 按实测的移动增益换算请求：安卓端对 UHID 相对移动有加速，1 格≠1 像素。
            req_x = int(max(-40, min(40, round(dx / gain))))
            req_y = int(max(-40, min(40, round(dy / gain))))
            if req_x == 0 and dx != 0:
                req_x = 1 if dx > 0 else -1
            if req_y == 0 and dy != 0:
                req_y = 1 if dy > 0 else -1
            try:
                self.session.mouse_move(req_x, req_y)
            except Exception:
                return
            time.sleep(0.1)
            moved = monitor.latest_position(0.6)
            if moved is not None:
                ax = abs(moved[0] - x)
                ay = abs(moved[1] - y)
                ratio = None
                if abs(dx) >= abs(dy) and req_x:
                    ratio = ax / abs(req_x)
                elif req_y:
                    ratio = ay / abs(req_y)
                if ratio is not None and ratio > 0.05:
                    gain = max(0.3, min(4.0, 0.6 * gain + 0.4 * ratio))

    def toggle_armed(self) -> None:
        if self.capture is None:
            return
        if self._armed:
            self.capture.disarm()
        else:
            self.capture.arm()

    @property
    def armed(self) -> bool:
        return self._armed

    # -- 统计 ------------------------------------------------------
    def _emit_stats(self) -> None:
        if self.capture and self.capture.armed and not self.capture.can_capture():
            self.capture.disarm()
            self._log("控制通道不可用，鼠标已释放回电脑")
        sent_bytes = self.session.sent_bytes if self.session else 0
        packets = self.session.sent_packets if self.session else 0
        now = time.perf_counter()
        last_bytes, last_packets, _ = self._last_stats
        dt = max(now - self._since, 1e-3)
        rate = max(0.0, (sent_bytes - last_bytes) / dt)
        pps = max(0.0, (packets - last_packets) / dt)
        self._last_stats = (sent_bytes, packets, now)
        self._since = now
        latency = self.session.latency_stats() if self.session else {"p50": None, "p95": None, "n": 0}
        self.statsChanged.emit({
            "sent_bytes": sent_bytes,
            "sent_packets": packets,
            "bytes_per_sec": rate,
            "packets_per_sec": pps,
            "rtt_ms": self.device_link.rtt_ms if self.device_link else None,
            "inject_p50_ms": latency.get("p50"),
            "inject_p95_ms": latency.get("p95"),
            "armed": self._armed,
            "device_name": (self.device_link.device_info.get("device") if self.device_link and self.device_link.connected else None),
            "link_connected": bool(self.device_link and self.device_link.connected),
            "sharing": bool(self.state == "sharing" and self.session and not self.session.closed),
            "transport_label": self.device_link.sharing_state.get("transport_label", "") if self.device_link else "",
            "auto_share": self.device_link.device_info.get("auto_share") if self.device_link else None,
        })
        self._publish_sharing_state()

    # -- 配对 ------------------------------------------------------
    def pair(self, ip: str, pair_port: int, code: str) -> None:
        threading.Thread(target=self._pair_worker, args=(ip, pair_port, code), daemon=True).start()

    def _pair_worker(self, ip: str, pair_port: int, code: str) -> None:
        try:
            self.adb.start_server()
            text = self.adb.text("pair", f"{ip}:{pair_port}", code)
            ok = "Successfully paired" in text
            self._log(text.strip())
            self.paired.emit(ok, text.strip())
        except (AdbError, OSError) as exc:
            self.paired.emit(False, str(exc))

    # -- 二维码配对 ------------------------------------------------
    def start_qr_pairing(self) -> dict:
        """生成一次二维码配对凭据并开始等待安卓端扫码。

        返回 {"name", "password", "payload"}，供界面绘制二维码。
        """
        self.cancel_qr_pairing()
        self._stop_qr_watch()
        name, password = make_credentials()
        payload = build_payload(name, password)
        session = QrPairing(
            self.adb, name, password,
            on_status=lambda text: self.qrPairStatus.emit(text),
        )
        self._qr_session = session

        def worker() -> None:
            try:
                self.adb.start_server()
                result = session.run()
            except Exception as exc:  # noqa: BLE001 - 配对失败要把原因带回界面
                result = PairResult(False, "", f"二维码配对失败：{exc}")
            finally:
                current = self._qr_session is session
                if current:
                    self._qr_session = None
            if not current:
                # 已被新的配对请求取代：旧结果（通常是“已取消”）不能打扰新界面
                return
            if session.cancelled:
                # 用户主动取消：不显示错误，也不写日志
                return
            if result.message:
                self._log(result.message)
            if result.ok and result.serial and result.serial != self.serial:
                # 正在共享时不切换设备，但配对完成后也要把配套 App 装好
                if self._sharing or self.state in ("starting", "sharing"):
                    threading.Thread(
                        target=self._ensure_android_app,
                        args=(result.serial,),
                        name="qr-pair-app",
                        daemon=True,
                    ).start()
            elif (
                result.ok
                and not result.serial
                and session.pairing_addr
                and "后台继续等待" in (result.message or "")
            ):
                self._start_qr_connect_watch(session.pairing_addr, session.baseline)
            self.qrPairFinished.emit(result.ok, result.serial or "", result.message or "")

        self._qr_thread = threading.Thread(target=worker, name="qr-pairing", daemon=True)
        self._qr_thread.start()
        return {"name": name, "password": password, "payload": payload}

    def cancel_qr_pairing(self) -> None:
        session, self._qr_session = self._qr_session, None
        if session is not None:
            session.cancel()

    def _stop_qr_watch(self) -> None:
        event, self._qr_watch_event = self._qr_watch_event, None
        if event is not None:
            event.set()

    def _start_qr_connect_watch(self, pairing_addr: str, baseline: set[str]) -> None:
        """配对成功但连接端口还没出现：后台继续找，连上后装 App 并通知界面。"""
        self._stop_qr_watch()
        stop = threading.Event()
        self._qr_watch_event = stop

        def watcher() -> None:
            deadline = time.monotonic() + DEFAULT_WATCH_TIMEOUT
            tried: set[str] = set()
            last_status = 0.0
            while time.monotonic() < deadline and not stop.is_set() and not self._stop:
                try:
                    services = parse_services(self.adb.text("mdns", "services", check=False))
                except (AdbError, OSError):
                    services = []
                for addr in candidate_connect_addresses(services, pairing_addr, baseline):
                    if addr in tried:
                        continue
                    tried.add(addr)
                    if try_connect(self.adb, addr, wait=3.0):
                        self._log(f"已连上配对设备：{addr}")
                        self._ensure_android_app(addr)
                        if not stop.is_set():
                            self.qrDeviceConnected.emit(addr)
                        return
                now = time.monotonic()
                if now - last_status >= 3.0:
                    elapsed = int(DEFAULT_WATCH_TIMEOUT - (deadline - now))
                    self.qrPairStatus.emit(
                        f"配对成功，仍在后台等待手机连接…（已等待 {elapsed} 秒）"
                    )
                    last_status = now
                stop.wait(1.0)
            if not stop.is_set() and not self._stop:
                self._log("配对成功，但手机一直没有连上；请在设备页重新扫描或手动连接。")

        self._qr_watch_thread = threading.Thread(
            target=watcher, name="qr-connect-watch", daemon=True
        )
        self._qr_watch_thread.start()

    # -- 配置 ------------------------------------------------------
    def update_config(self, **changes) -> None:
        self.config.update(changes)
        save_config(self.config)
        if self.capture is not None:
            if "placement" in changes:
                self.capture.set_edge(int(changes["placement"]))
                if self.pointer_monitor:
                    self.pointer_monitor.set_enabled(self._armed, self._capture_generation, int(changes["placement"]))
            if "edge_switch" in changes:
                self.capture.set_edge_switch(bool(changes["edge_switch"]))
            if "edge_on_extended" in changes:
                self.capture.set_allow_extended_edge(bool(changes["edge_on_extended"]))
        if self.device_link is not None:
            self.device_link.send_config(self._link_config())
        self._publish_sharing_state()
        self._log("设置已保存：" + ", ".join(f"{k}={v}" for k, v in changes.items()))

    def shutdown(self) -> None:
        self._stop = True
        self._stop_qr_watch()
        self.stop_sharing()
        if self._sharing_thread:
            self._sharing_thread.join(timeout=6)
        # 退出前确保把 show_ime_with_hard_keyboard 还原（异步恢复线程可能来不及跑）。
        self._apply_ime_with_hard_keyboard(False)
        if self.device_link is not None:
            self.device_link.stop()
        time.sleep(0.2)
