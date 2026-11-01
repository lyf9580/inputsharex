"""桌面端 ↔ 安卓 App 的辅助通道。

桌面端在本机 127.0.0.1:<port> 起一个 TCP server，再用 `adb reverse tcp:port tcp:port`
把它映射到安卓端上的同一个端口；安卓 App 作为 client 连上来，双方用「一行一个 JSON」通信。

用途：
- 让手机端显示「已连接」、电脑名、实时延迟（ping/pong 往返）
- 把桌面端的设置（死区、双击、放置方向、保持唤醒）推给手机端
- 手机端上报自己的状态（型号、Android 版本），App 里显示

协议（v1）：
  设备 → PC  {"type":"hello","version":1,"device":"Xiaomi Pad 7 Pro","android":36,"app":"0.1.0"}
            {"type":"ping","t":<ms>}            # 每 500ms
            {"type":"config","keep_awake":true,...}
  PC → 设备  {"type":"welcome","desktop":"<hostname>","version":1}
            {"type":"pong","t":<ms>}
            {"type":"config","deadzone":40,"double_trigger":false,"placement":0,"keep_awake":true}
"""
from __future__ import annotations

import json
import socket
import threading
import time

from .adb import Adb, AdbError

DEFAULT_LINK_PORT = 27184


class DeviceLink:
    def __init__(self, adb: Adb | None = None, serial: str | None = None,
                 host: str = "127.0.0.1", port: int = DEFAULT_LINK_PORT, on_message=None):
        self.adb = adb
        self.serial = serial
        self.host = host
        self.port = port
        self._srv: socket.socket | None = None
        self._conn: socket.socket | None = None
        self._thread: threading.Thread | None = None
        self._ping_thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._stop = False
        self.device_info: dict = {}
        self.rtt_ms: float | None = None
        self.clock_offset_ms: float | None = None
        self.connected = False
        self.received = 0
        self._config: dict = {}
        self._send_lock = threading.Lock()
        self.on_message = on_message
        self.sharing_state = {}

    # -- lifecycle -------------------------------------------------
    def start(self) -> None:
        self._stop = False
        self._srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._srv.bind((self.host, self.port))
        self._srv.listen(4)
        self._srv.settimeout(1.0)
        if self.adb is not None and self.serial:
            try:
                self.adb.reverse(self.port, self.port)
            except AdbError:
                pass
        self._thread = threading.Thread(target=self._accept_loop, name="device-link", daemon=True)
        self._thread.start()
        self._ping_thread = threading.Thread(target=self._ping_loop, name="device-link-ping", daemon=True)
        self._ping_thread.start()

    def _ping_loop(self) -> None:
        """每秒从 PC 发起一次 ping，设备回 pong，同一台机器计时 → 真实 RTT。"""
        while not self._stop:
            if self.connected:
                self.send({"type": "ping", "t": time.time() * 1000.0})
            time.sleep(1.0)

    def stop(self) -> None:
        self._stop = True
        with self._lock:
            if self._conn is not None:
                try:
                    self._conn.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
                try:
                    self._conn.close()
                except OSError:
                    pass
                self._conn = None
            self.connected = False
        if self._srv is not None:
            try:
                self._srv.close()
            except OSError:
                pass
            self._srv = None
        if self.adb is not None and self.serial:
            try:
                self.adb.reverse_remove(self.port)
            except (AdbError, AttributeError):
                pass

    # -- io --------------------------------------------------------
    def send(self, obj: dict) -> None:
        with self._lock:
            conn = self._conn
        if conn is None:
            return
        try:
            with self._send_lock:
                conn.sendall((json.dumps(obj, ensure_ascii=False) + "\n").encode("utf-8"))
        except OSError:
            pass

    def send_config(self, config: dict) -> None:
        self._config = dict(config)
        payload = {"type": "config"}
        payload.update(config)
        self.send(payload)

    def _accept_loop(self) -> None:
        while not self._stop:
            if self._srv is None:
                return
            try:
                conn, _ = self._srv.accept()
            except socket.timeout:
                continue
            except OSError:
                return
            conn.settimeout(None)
            try:
                conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            except OSError:
                pass
            with self._lock:
                if self._conn is not None:
                    try:
                        self._conn.close()
                    except OSError:
                        pass
                self._conn = conn
                self.connected = True
            self._read_loop(conn)

    def _read_loop(self, conn: socket.socket) -> None:
        buffer = b""
        try:
            while not self._stop:
                chunk = conn.recv(4096)
                if not chunk:
                    break
                buffer += chunk
                while b"\n" in buffer:
                    line, buffer = buffer.split(b"\n", 1)
                    self._handle(line)
        except OSError:
            pass
        finally:
            with self._lock:
                if self._conn is conn:
                    self._conn = None
                self.connected = False

    def _handle(self, line: bytes) -> None:
        try:
            msg = json.loads(line.decode("utf-8", "replace"))
        except ValueError:
            return
        if not isinstance(msg, dict):
            return
        self.received += 1
        kind = msg.get("type")
        if kind == "hello":
            self.device_info = {
                "device": msg.get("device") or "Android",
                "android": msg.get("android"),
                "app": msg.get("app"),
                "auto_share": msg.get("auto_share", True),
            }
            self.send({"type": "welcome", "desktop": socket.gethostname(), "version": 1})
            if self._config:
                payload = {"type": "config"}
                payload.update(self._config)
                self.send(payload)
            if self.sharing_state:
                self.send(self.sharing_state)
        elif kind == "ping":
            t = msg.get("t")
            now_ms = time.time() * 1000.0
            if isinstance(t, (int, float)):
                delta = now_ms - float(t)
                if self.clock_offset_ms is None or delta < self.clock_offset_ms:
                    self.clock_offset_ms = delta
                # 没有时钟同步，只能用「最小样本」当基线估算抖动
                self.rtt_ms = max(0.0, delta - self.clock_offset_ms)
            self.send({"type": "pong", "t": t, "ts": now_ms})
        elif kind == "pong":
            # 我们自己发起的 ping 被打回来的回环时间（同一台机器计时，无时钟偏差）
            t = msg.get("t")
            if isinstance(t, (int, float)):
                self.rtt_ms = max(0.0, (time.time() * 1000.0) - float(t))
        elif kind == "config":
            # Android settings must not overwrite desktop-owned configuration.
            self.device_info["auto_share"] = msg.get("auto_share", True)
        if self.on_message is not None:
            self.on_message(msg)
