"""控制通道会话：发送 HID 报文，并在空闲时补发零位移报文保活无线链路。

性能设计（v0.2）：
1. TCP_NODELAY：控制通道是交互式小包，Nagle 会把小包压住等 ACK，
   在无线链路上表现为 30-200ms 的"突然一顿"。必须关掉。
2. 合并发送：鼠标位移先进累加器，每 cadence_ms（默认 4ms）把这一拍的
   位移合并成「一次 sendall、多帧 HID」。原来每个鼠标事件一个 TCP 包
   （125-1000 包/秒），现在最多 250 包/秒，无线空口开销显著下降。
3. 保活与输入共用一条连接、同一个发送线程：空闲超过 keepalive_interval
   就补一帧零位移，避免安卓端 Wi-Fi 空闲省电把下行包缓存到下次 DTIM。
"""
from __future__ import annotations

import math
import socket
import struct
import threading
import time

from . import hid, keyboard, protocol

# 单拍最多合并多少帧位移（用户甩鼠标时防止一次发几十帧把空口挤爆）
MAX_REPORTS_PER_TICK = 8
# 单帧最大位移（HID 是 8 位有符号）
_MAX_STEP = 120

_WINMM = None


def _time_begin_period(ms: int = 1) -> bool:
    """Windows 默认定时器精度 15.6ms：4ms 合并周期和 8ms 保活会被抖成 15.6ms。

    用 winmm.timeBeginPeriod(1) 把系统定时器精度提到 1ms（浏览器/游戏/音频程序
    都会这么干），线程 sleep 才真的按 4ms 走。
    """
    global _WINMM
    try:
        if _WINMM is None:
            import ctypes

            _WINMM = ctypes.WinDLL("winmm")
        return _WINMM.timeBeginPeriod(ms) == 0
    except Exception:
        return False


def _time_end_period(ms: int = 1) -> None:
    try:
        if _WINMM is not None:
            _WINMM.timeEndPeriod(ms)
    except Exception:
        pass


def _boost_thread_priority(priority: int = 2) -> None:
    """把发送线程提到 HIGHEST，避免被 Windows 调度拖到 10ms+ 的间隔。"""
    try:
        import ctypes

        kernel32 = ctypes.WinDLL("kernel32")
        handle = kernel32.GetCurrentThread()
        kernel32.SetThreadPriority(handle, priority)
    except Exception:
        pass


class ControlSession:
    def __init__(self, sock: socket.socket, keepalive_interval: float = 0.008,
                 cadence_ms: float = 4.0):
        self.sock = sock
        self.keepalive_interval = keepalive_interval
        self.cadence = max(cadence_ms, 0.0) / 1000.0
        self._lock = threading.Lock()
        self._last_send = 0.0
        self._closed = False
        self._thread: threading.Thread | None = None
        self.mouse_buttons = 0
        self.sent_bytes = 0
        self.sent_messages = 0
        self.sent_packets = 0
        self._dx = 0
        self._dy = 0
        self._wheel = 0
        self._pan = 0
        self._dirty = False
        self._pending_since = 0.0
        self._latency_samples: list[float] = []
        self._kb_mods = 0
        self._kb_keys: list[int] = []
        # 安卓端把 UHID 键盘当成“已连接物理键盘”，设备存在时系统会隐藏软键盘。
        # 因此键盘设备只在接管期间创建，切回电脑就销毁，避免安卓自带输入法被顶掉。
        self._kb_created = False
        # 剪贴板同步：设备端变化回调 on_clipboard(text)，收到 SET_CLIPBOARD 的 ACK 回调
        # on_clipboard_ack(sequence)。设备端消息由独立接收线程解析。
        self.on_clipboard = None
        self.on_clipboard_ack = None
        self._recv_thread: threading.Thread | None = None
        self._recv_buffer = bytearray()
        self._clip_sequence = 0
        self._screen_size: tuple[int, int] | None = None
        try:
            self.sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        except OSError:
            pass
        # 尝试 DSCP EF。Windows 可能拒绝设置；adb forward 下此 socket 只到本机 adb，
        # 不能据此认定 adb 的无线 TLS 连接也带有同样标记。
        try:
            self.sock.setsockopt(socket.IPPROTO_IP, socket.IP_TOS, 0xB8)
        except OSError:
            pass

    # -- raw send --------------------------------------------------
    def _send_locked(self, payload: bytes) -> None:
        try:
            self.sock.sendall(payload)
        except OSError:
            self._closed = True
            return
        self._last_send = time.perf_counter()
        self.sent_packets += 1

    def send(self, payload: bytes) -> None:
        """立即发送（设备创建/按键等低频消息）。"""
        if self._closed:
            return
        with self._lock:
            if self._closed:
                return
            self._send_locked(payload)
            self.sent_bytes += len(payload)
            self.sent_messages += 1

    # -- setup -----------------------------------------------------
    def create_mouse(self, name: str = "inputsharex") -> None:
        self.send(protocol.uhid_create(hid.HID_MOUSE_ID, name, hid.MOUSE_DESCRIPTOR))

    def create_keyboard(self, name: str = "inputsharex") -> None:
        """创建 UHID 键盘设备（只在接管期间需要）。重复调用无副作用。"""
        if self._closed or self._kb_created:
            return
        self.send(protocol.uhid_create(hid.HID_KEYBOARD_ID, f"{name}-kbd", hid.KEYBOARD_DESCRIPTOR))
        if not self._closed:
            self._kb_created = True

    def destroy_keyboard(self) -> None:
        """接管结束立即销毁键盘设备，让安卓恢复自带软键盘。"""
        if self._closed or not self._kb_created:
            return
        self._kb_mods = 0
        self._kb_keys = []
        self.send(protocol.uhid_destroy(hid.HID_KEYBOARD_ID))
        self._kb_created = False

    def destroy_devices(self) -> None:
        self.send(protocol.uhid_destroy(hid.HID_MOUSE_ID))
        self.send(protocol.uhid_destroy(hid.HID_KEYBOARD_ID))
        self._kb_mods = 0
        self._kb_keys = []
        self._kb_created = False

    # -- input -----------------------------------------------------
    def mouse_move(self, dx: int, dy: int, buttons: int | None = None) -> None:
        """只累加不发送，由发送线程按 cadence 合并。"""
        if self._closed:
            return
        with self._lock:
            if buttons is not None:
                self.mouse_buttons = buttons & 0x1F
            if not self._dirty:
                self._pending_since = time.perf_counter()
            self._dx += dx
            self._dy += dy
            if dx or dy:
                self._dirty = True

    def mouse_button(self, buttons: int) -> None:
        """按键变化立即发（点击手感不能被合并延迟）。"""
        with self._lock:
            buttons &= 0x1F
            if self._closed or buttons == self.mouse_buttons:
                return
            # Raw Input 每次移动也会回调当前按键，只有变化才需要立即发包。
            # 先发送此前累积的位移，避免点击前的移动被算作按住后的拖动。
            while self._dirty and not self._closed:
                payload = self._build_tick_locked()
                if payload:
                    self._send_locked(payload)
                    self.sent_bytes += len(payload)
                    self.sent_messages += 1
            self._pending_since = 0.0
            if self._closed:
                return
            self.mouse_buttons = buttons
            payload = protocol.uhid_input(
                hid.HID_MOUSE_ID, hid.mouse_report(buttons, 0, 0)
            )
            self._send_locked(payload)
            self.sent_bytes += len(payload)
            self.sent_messages += 1

    def mouse_click(self, button: int, hold: float = 0.02) -> None:
        self.mouse_button(self.mouse_buttons | button)
        time.sleep(hold)
        self.mouse_button(self.mouse_buttons & ~button)

    def scroll(self, v: int, h: int = 0) -> None:
        with self._lock:
            if not self._dirty:
                self._pending_since = time.perf_counter()
            self._wheel += v
            self._pan += h
            self._dirty = True

    def key(self, action: int, keycode: int, repeat: int = 0, meta_state: int = 0) -> None:
        self.send(protocol.inject_keycode(action, keycode, repeat, meta_state))

    def text(self, value: str) -> None:
        self.send(protocol.inject_text(value))

    # -- 键盘（UHID 物理键盘） --------------------------------------
    def keyboard_event(self, vk: int, scan: int, extended: bool, up: bool) -> None:
        """把一次 Windows 键盘事件转成 HID 键盘报文发到安卓。

        只有状态真的变化才发包：按住不放时系统会重复上报 down，安卓内核
        自己会做按键重复，重复报文反而可能让按键抖动。
        """
        if self._closed:
            return
        if not self._kb_created:
            # 正常流程在接管时已创建；这里兜底，避免漏创建时按键丢失。
            self.create_keyboard()
        bit = keyboard.modifier_bit(vk, scan, extended)
        usage = None if bit else keyboard.usage_for(vk, scan, extended)
        with self._lock:
            if self._closed:
                return
            changed = False
            if bit:
                old = self._kb_mods
                self._kb_mods = (self._kb_mods & ~bit) if up else (self._kb_mods | bit)
                changed = self._kb_mods != old
            elif usage is not None:
                if up:
                    if usage in self._kb_keys:
                        self._kb_keys.remove(usage)
                        changed = True
                elif usage not in self._kb_keys and len(self._kb_keys) < 6:
                    self._kb_keys.append(usage)
                    changed = True
            if not changed:
                return
            payload = protocol.uhid_input(
                hid.HID_KEYBOARD_ID, hid.keyboard_report(self._kb_mods, self._kb_keys)
            )
            self._send_locked(payload)
            self.sent_bytes += len(payload)
            self.sent_messages += 1

    def release_keys(self) -> None:
        """切回电脑/停止共享时清空所有按键，避免修饰键或字符键卡在安卓端。"""
        if self._closed or not self._kb_created:
            return
        with self._lock:
            if self._closed:
                return
            if not self._kb_mods and not self._kb_keys:
                return
            self._kb_mods = 0
            self._kb_keys = []
            payload = protocol.uhid_input(hid.HID_KEYBOARD_ID, hid.keyboard_report())
            self._send_locked(payload)
            self.sent_bytes += len(payload)
            self.sent_messages += 1

    # -- 剪贴板同步 ------------------------------------------------
    def set_screen_size(self, width: int, height: int) -> None:
        """记录安卓当前逻辑分辨率（横屏时宽高已互换），供手势注入使用。"""
        if width > 0 and height > 0:
            self._screen_size = (int(width), int(height))

    def set_clipboard_text(self, text: str, paste: bool = False) -> bool:
        """把电脑端剪贴板文本写到安卓端。返回是否真的发出。"""
        if self._closed:
            return False
        with self._lock:
            if self._closed:
                return False
            self._clip_sequence += 1
            payload = protocol.set_clipboard(text, paste, self._clip_sequence)
            self._send_locked(payload)
            self.sent_bytes += len(payload)
            self.sent_messages += 1
            return True

    def request_clipboard(self, copy_key: int = 0) -> None:
        self.send(protocol.get_clipboard(copy_key))

    def _recv_loop(self) -> None:
        while not self._closed:
            try:
                data = self.sock.recv(4096)
            except OSError:
                self._closed = True
                return
            if not data:
                self._closed = True
                return
            self._recv_buffer += data
            try:
                self._drain_device_messages()
            except Exception:
                self._closed = True
                return

    def _drain_device_messages(self) -> None:
        """解析设备端消息：0=剪贴板文本(4字节长度+UTF-8)、1=ACK(8字节序号)、2=UHID 输出。"""
        buf = self._recv_buffer
        while buf:
            kind = buf[0]
            if kind == protocol.DEVICE_MSG_CLIPBOARD:
                if len(buf) < 5:
                    return
                length = struct.unpack(">I", bytes(buf[1:5]))[0]
                if len(buf) < 5 + length:
                    return
                raw = bytes(buf[5:5 + length])
                del buf[:5 + length]
                text = raw.decode("utf-8", "replace")
                if self.on_clipboard is not None:
                    self.on_clipboard(text)
            elif kind == protocol.DEVICE_MSG_ACK_CLIPBOARD:
                if len(buf) < 9:
                    return
                sequence = struct.unpack(">Q", bytes(buf[1:9]))[0]
                del buf[:9]
                if self.on_clipboard_ack is not None:
                    self.on_clipboard_ack(sequence)
            elif kind == protocol.DEVICE_MSG_UHID_OUTPUT:
                if len(buf) < 5:
                    return
                size = struct.unpack(">H", bytes(buf[3:5]))[0]
                if len(buf) < 5 + size:
                    return
                del buf[:5 + size]
            else:
                # 未知消息无法确定长度，继续读只会错位；直接结束会话。
                raise ValueError(f"unknown device message type: {kind}")

    # -- 手势（图文左右切换） --------------------------------------
    def swipe_horizontal(self, forward: bool, steps: int = 8, interval: float = 0.012) -> bool:
        """在安卓当前界面上做一次水平滑动。forward=True 表示"下一张"（手指向左滑）。

        使用 scrcpy 的 INJECT_TOUCH_EVENT：pointer_id=0 会被服务端当作手指触摸，
        而不是鼠标事件，因此能被 ViewPager/抖音图文识别成翻页手势。
        """
        if self._closed:
            return False
        size = self._screen_size
        if not size:
            return False
        width, height = size
        y = height // 2
        left = int(width * 0.22)
        right = int(width * 0.78)
        if forward:
            x_start, x_end = right, left
        else:
            x_start, x_end = left, right
        pid = 0
        self.send(protocol.inject_touch(0, pid, x_start, y, width, height,
                                        pressure=0xFFFF, action_button=0, buttons=0))
        for i in range(1, steps):
            t = i / steps
            x = int(round(x_start + (x_end - x_start) * t))
            self.send(protocol.inject_touch(2, pid, x, y, width, height,
                                            pressure=0xFFFF, action_button=0, buttons=0))
            time.sleep(interval)
        self.send(protocol.inject_touch(1, pid, x_end, y, width, height,
                                        pressure=0, action_button=0, buttons=0))
        return True

    # -- 合并发送线程 ----------------------------------------------
    def set_keepalive(self, keepalive_ms: float) -> None:
        self.keepalive_interval = max(0.0, keepalive_ms) / 1000.0

    def set_cadence(self, cadence_ms: float) -> None:
        self.cadence = max(0.0, cadence_ms) / 1000.0

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self._loop, name="control-sender", daemon=True)
        self._thread.start()
        if self._recv_thread is None or not self._recv_thread.is_alive():
            self._recv_thread = threading.Thread(
                target=self._recv_loop, name="control-recv", daemon=True
            )
            self._recv_thread.start()

    def start_keepalive(self) -> None:  # 兼容旧接口
        self.start()

    @staticmethod
    def _split(total: int, parts: int) -> list[int]:
        """把 total 均分成 parts 段（每段 |v| <= 127，方向一致）。"""
        if parts <= 0:
            return []
        sign = -1 if total < 0 else 1
        mag = abs(total)
        base, rem = divmod(mag, parts)
        return [sign * (base + (1 if i < rem else 0)) for i in range(parts)]

    def _build_tick_locked(self) -> bytes:
        chunks: list[bytes] = []
        dx, dy = self._dx, self._dy
        wheel, pan = self._wheel, self._pan
        self._dx = self._dy = self._wheel = self._pan = 0
        self._dirty = False

        if dx or dy:
            parts = max(1, math.ceil(max(abs(dx), abs(dy)) / _MAX_STEP))
            parts = min(parts, MAX_REPORTS_PER_TICK)
            # 不能把大位移平均到 8 帧后再交给 HID clamp，那会永久丢失位移。
            # 本拍保留方向，超出预算的部分留到下一拍发送。
            capacity = parts * _MAX_STEP
            largest = max(abs(dx), abs(dy))
            if largest > capacity:
                step_x = round(dx * capacity / largest)
                step_y = round(dy * capacity / largest)
                self._dx, self._dy = dx - step_x, dy - step_y
                self._dirty = bool(self._dx or self._dy)
                dx, dy = step_x, step_y
            xs = self._split(dx, parts)
            ys = self._split(dy, parts)
            for sx, sy in zip(xs, ys):
                chunks.append(
                    protocol.uhid_input(
                        hid.HID_MOUSE_ID, hid.mouse_report(self.mouse_buttons, sx, sy)
                    )
                )
        while wheel or pan:
            step_w = max(-127, min(127, wheel))
            step_p = max(-127, min(127, pan))
            chunks.append(
                protocol.uhid_input(
                    hid.HID_MOUSE_ID, hid.mouse_report(self.mouse_buttons, 0, 0, step_w, step_p)
                )
            )
            wheel -= step_w
            pan -= step_p
        return b"".join(chunks)

    def _loop(self) -> None:
        _time_begin_period(1)
        _boost_thread_priority()
        try:
            next_tick = time.perf_counter()
            while not self._closed:
                with self._lock:
                    if self._closed:
                        return
                    payload = self._build_tick_locked() if self._dirty else b""
                    idle = time.perf_counter() - self._last_send
                    if payload:
                        self._send_locked(payload)
                        self.sent_bytes += len(payload)
                        self.sent_messages += 1
                        if self._pending_since:
                            self._latency_samples.append(
                                (time.perf_counter() - self._pending_since) * 1000.0
                            )
                            if len(self._latency_samples) > 240:
                                del self._latency_samples[:60]
                            self._pending_since = 0.0
                    elif self.keepalive_interval > 0 and idle >= self.keepalive_interval:
                        # 保活帧必须带当前按键状态：如果无脑发 0x00 按键，
                        # 按住/拖拽的鼠标键会在 8ms 后被这一帧"松开"，表现就是
                        # 安卓端点击无效、拖不动。
                        keepalive = protocol.uhid_input(
                            hid.HID_MOUSE_ID, hid.mouse_report(self.mouse_buttons, 0, 0)
                        )
                        self._send_locked(keepalive)
                        self.sent_bytes += len(keepalive)
                        self.sent_messages += 1
                    cadence = self.cadence if self.cadence > 0 else 0.004
                # 固定节拍推进，而不是"发完再睡 cadence"——后者会让周期越走越长
                next_tick += cadence
                remaining = next_tick - time.perf_counter()
                if remaining > 0:
                    time.sleep(remaining)
                elif remaining < -0.05:
                    # 被系统抢占落后太多，重排节拍，避免追赶式连发
                    next_tick = time.perf_counter()
        finally:
            _time_end_period(1)

    def latency_stats(self) -> dict:
        """输入事件 → 发到线上的延迟（毫秒），衡量本端引擎的排队/合并开销。"""
        samples = sorted(self._latency_samples)
        if not samples:
            return {"p50": None, "p95": None, "max": None, "n": 0}
        n = len(samples)
        return {
            "p50": round(samples[n // 2], 2),
            "p95": round(samples[min(n - 1, int(n * 0.95))], 2),
            "max": round(samples[-1], 2),
            "n": n,
        }

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            self.sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        try:
            self.sock.close()
        except OSError:
            pass

    @property
    def closed(self) -> bool:
        return self._closed
