"""Windows 输入捕获：Raw Input 相对位移 + 全局热键 + 边缘切换（多显示器）。

v0.3.2 修复：
1. **多显示器**：边缘判定不再用主屏尺寸，而是用 `MonitorFromPoint` 取光标所在显示器的
   实际矩形（支持扩展屏、负坐标、不同分辨率）。
2. **接管时隐藏光标**：用「陷阱窗口（topmost 4x4 popup）+ ClipCursor 到 1px + WM_SETCURSOR
   里返回空光标」实现真正的全局隐藏（后台线程的 ShowCursor 在很多情况下不生效）。
3. 不再用大块 ClipCursor（v0.3.1 起已去掉），用户不会被"关进小方块"。

两种进入方式：
- 边缘切换（默认）：光标自由移动，撞到当前显示器配置侧边缘且继续外推 → 转发。
- 热键（默认 Ctrl+Alt+Q）：手动进入/退出转发。

边缘进入后，反向移动超过阈值自动切回电脑，光标出现在该显示器边缘内侧。
"""
from __future__ import annotations

import ctypes
import os
import threading
import time
import uuid
from ctypes import wintypes

from . import clipboard
from .keyboard import modifier_bit as _modifier_bit

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)


def _dbg(message: str) -> None:
    """边缘切换调试日志（仅当 INPUTSHAREX_DEBUG_LOG 指向可写文件时启用）。"""
    path = os.environ.get("INPUTSHAREX_DEBUG_LOG")
    if not path:
        return
    try:
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(time.strftime("%H:%M:%S ") + message + "\n")
    except OSError:
        pass

WM_INPUT = 0x00FF
WM_HOTKEY = 0x0312
WM_CLOSE = 0x0010
WM_DESTROY = 0x0002
WM_SETCURSOR = 0x0020
WM_CLIPBOARDUPDATE = 0x031D

# 低级键盘钩子：接管时把 PC 键盘事件转发给安卓，并拦下本地按键。
WH_KEYBOARD_LL = 13
LLKHF_EXTENDED = 0x01
LLKHF_INJECTED = 0x10
LLKHF_UP = 0x80

# 修饰键在位掩码里的分组（hid 键盘报文第一字节）
_KB_CTRL_BITS = 0x01 | 0x10
_KB_ALT_BITS = 0x04 | 0x40
_KB_SHIFT_BITS = 0x02 | 0x20
_KB_GUI_BITS = 0x08 | 0x80

RID_INPUT = 0x10000003
RIDEV_INPUTSINK = 0x00000100

RI_MOUSE_LEFT_BUTTON_DOWN = 0x0001
RI_MOUSE_LEFT_BUTTON_UP = 0x0002
RI_MOUSE_RIGHT_BUTTON_DOWN = 0x0004
RI_MOUSE_RIGHT_BUTTON_UP = 0x0008
RI_MOUSE_MIDDLE_BUTTON_DOWN = 0x0010
RI_MOUSE_MIDDLE_BUTTON_UP = 0x0020
RI_MOUSE_BUTTON_4_DOWN = 0x0040
RI_MOUSE_BUTTON_4_UP = 0x0080
RI_MOUSE_BUTTON_5_DOWN = 0x0100
RI_MOUSE_BUTTON_5_UP = 0x0200
RI_MOUSE_WHEEL = 0x0400
RI_MOUSE_HWHEEL = 0x0800

BTN_LEFT = 0x01
BTN_RIGHT = 0x02
BTN_MIDDLE = 0x04
BTN_SIDE = 0x08
BTN_EXTRA = 0x10

MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
MOD_NOREPEAT = 0x4000

VK_F9 = 0x78
VK_F10 = 0x79
VK_F12 = 0x7B
VK_Q = 0x51

EDGE_RIGHT = 0
EDGE_LEFT = 1
EDGE_BOTTOM = 2
EDGE_TOP = 3

WS_POPUP = 0x80000000
WS_EX_TOPMOST = 0x00000008
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_NOACTIVATE = 0x08000000
HWND_TOPMOST = -1
SWP_NOACTIVATE = 0x0010
SWP_SHOWWINDOW = 0x0040
SW_HIDE = 0
MONITOR_DEFAULTTONEAREST = 2
MONITOR_DEFAULTTOPRIMARY = 1
OCR_NORMAL = 32512


class RAWINPUTDEVICE(ctypes.Structure):
    _fields_ = [
        ("usUsagePage", wintypes.USHORT),
        ("usUsage", wintypes.USHORT),
        ("dwFlags", wintypes.DWORD),
        ("hwndTarget", wintypes.HWND),
    ]


class RAWINPUTHEADER(ctypes.Structure):
    _fields_ = [
        ("dwType", wintypes.DWORD),
        ("dwSize", wintypes.DWORD),
        ("hDevice", wintypes.HANDLE),
        ("wParam", wintypes.WPARAM),
    ]


class _RAWMOUSE_BTN_STRUCT(ctypes.Structure):
    _fields_ = [
        ("usButtonFlags", wintypes.USHORT),
        ("usButtonData", wintypes.USHORT),
    ]


class _RAWMOUSE_BUTTONS(ctypes.Union):
    """C 里是 union { ULONG ulButtons; struct { USHORT usButtonFlags; USHORT usButtonData; }; }。"""

    _fields_ = [
        ("ulButtons", wintypes.ULONG),
        ("btns", _RAWMOUSE_BTN_STRUCT),
    ]


class RAWMOUSE(ctypes.Structure):
    _fields_ = [
        ("usFlags", wintypes.USHORT),
        ("_pad", wintypes.USHORT),  # 对齐：union 从 offset 4 开始
        ("buttons", _RAWMOUSE_BUTTONS),
        ("ulRawButtons", wintypes.ULONG),
        ("lLastX", wintypes.LONG),
        ("lLastY", wintypes.LONG),
        ("ulExtraInformation", wintypes.ULONG),
    ]

    @property
    def usButtonFlags(self) -> int:
        return self.buttons.btns.usButtonFlags

    @property
    def usButtonData(self) -> int:
        return self.buttons.btns.usButtonData


class RAWINPUT(ctypes.Structure):
    _fields_ = [("header", RAWINPUTHEADER), ("mouse", RAWMOUSE)]


class POINT(ctypes.Structure):
    _fields_ = [("x", wintypes.LONG), ("y", wintypes.LONG)]


class RECT(ctypes.Structure):
    _fields_ = [
        ("left", wintypes.LONG),
        ("top", wintypes.LONG),
        ("right", wintypes.LONG),
        ("bottom", wintypes.LONG),
    ]


class KBDLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [
        ("vkCode", wintypes.DWORD),
        ("scanCode", wintypes.DWORD),
        ("flags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.c_void_p),
    ]


class MONITORINFO(ctypes.Structure):    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("rcMonitor", RECT),
        ("rcWork", RECT),
        ("dwFlags", wintypes.DWORD),
    ]


WNDPROC = ctypes.WINFUNCTYPE(
    ctypes.c_ssize_t, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM
)

HOOKPROC = ctypes.WINFUNCTYPE(
    ctypes.c_ssize_t, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM
)

user32.SetWindowsHookExW.restype = ctypes.c_void_p
user32.SetWindowsHookExW.argtypes = [
    ctypes.c_int, HOOKPROC, wintypes.HINSTANCE, wintypes.DWORD
]
user32.CallNextHookEx.restype = ctypes.c_ssize_t
user32.CallNextHookEx.argtypes = [
    ctypes.c_void_p, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM
]
user32.UnhookWindowsHookEx.restype = wintypes.BOOL
user32.UnhookWindowsHookEx.argtypes = [ctypes.c_void_p]

user32.DefWindowProcW.argtypes = [
    wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM
]
user32.DefWindowProcW.restype = ctypes.c_ssize_t
user32.GetMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT]
user32.GetMessageW.restype = ctypes.c_int
user32.CreateWindowExW.argtypes = [
    wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD,
    ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
    wintypes.HWND, wintypes.HMENU, wintypes.HINSTANCE, wintypes.LPVOID,
]
user32.CreateWindowExW.restype = wintypes.HWND
user32.RegisterRawInputDevices.argtypes = [
    ctypes.POINTER(RAWINPUTDEVICE), wintypes.UINT, wintypes.UINT
]
user32.RegisterRawInputDevices.restype = wintypes.BOOL
user32.GetRawInputData.restype = wintypes.UINT
user32.GetCursorPos.argtypes = [ctypes.POINTER(POINT)]
user32.GetCursorPos.restype = wintypes.BOOL
user32.SetCursorPos.argtypes = [ctypes.c_int, ctypes.c_int]
user32.SetCursorPos.restype = wintypes.BOOL
user32.SetCursor.argtypes = [wintypes.HANDLE]
user32.SetCursor.restype = wintypes.HANDLE
user32.ClipCursor.argtypes = [ctypes.POINTER(RECT)]
user32.ClipCursor.restype = wintypes.BOOL
user32.MonitorFromPoint.argtypes = [POINT, wintypes.DWORD]
user32.MonitorFromPoint.restype = wintypes.HMONITOR
user32.GetMonitorInfoW.argtypes = [wintypes.HMONITOR, ctypes.POINTER(MONITORINFO)]
user32.GetMonitorInfoW.restype = wintypes.BOOL
user32.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
                                ctypes.c_int, ctypes.c_int, wintypes.UINT]
user32.SetWindowPos.restype = wintypes.BOOL
user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
user32.ShowWindow.restype = wintypes.BOOL
kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
kernel32.GetModuleHandleW.restype = wintypes.HMODULE
user32.UnregisterClassW.argtypes = [wintypes.LPCWSTR, wintypes.HINSTANCE]
user32.UnregisterClassW.restype = wintypes.BOOL
user32.DestroyWindow.argtypes = [wintypes.HWND]
user32.DestroyWindow.restype = wintypes.BOOL
user32.RegisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int, wintypes.UINT, wintypes.UINT]
user32.UnregisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int]
user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
user32.AddClipboardFormatListener.argtypes = [wintypes.HWND]
user32.AddClipboardFormatListener.restype = wintypes.BOOL
user32.RemoveClipboardFormatListener.argtypes = [wintypes.HWND]
user32.RemoveClipboardFormatListener.restype = wintypes.BOOL


class WNDCLASSW(ctypes.Structure):
    _fields_ = [
        ("style", wintypes.UINT),
        ("lpfnWndProc", WNDPROC),
        ("cbClsExtra", ctypes.c_int),
        ("cbWndExtra", ctypes.c_int),
        ("hInstance", wintypes.HINSTANCE),
        ("hIcon", wintypes.HICON),
        ("hCursor", wintypes.HANDLE),
        ("hbrBackground", wintypes.HBRUSH),
        ("lpszMenuName", wintypes.LPCWSTR),
        ("lpszClassName", wintypes.LPCWSTR),
    ]


user32.RegisterClassW.argtypes = [ctypes.POINTER(WNDCLASSW)]
user32.RegisterClassW.restype = wintypes.ATOM


def _monitor_rect_from_handle(hmon) -> tuple[int, int, int, int]:
    if not hmon:
        return (0, 0, user32.GetSystemMetrics(0), user32.GetSystemMetrics(1))
    mi = MONITORINFO()
    mi.cbSize = ctypes.sizeof(MONITORINFO)
    if not user32.GetMonitorInfoW(hmon, ctypes.byref(mi)):
        return (0, 0, user32.GetSystemMetrics(0), user32.GetSystemMetrics(1))
    r = mi.rcMonitor
    return (r.left, r.top, r.right, r.bottom)


def _monitor_rect(x: int, y: int) -> tuple[int, int, int, int]:
    """返回 (left, top, right, bottom)，单位是虚拟桌面坐标（可含负数）。"""
    hmon = user32.MonitorFromPoint(POINT(x, y), MONITOR_DEFAULTTONEAREST)
    return _monitor_rect_from_handle(hmon)


def _primary_monitor_rect() -> tuple[int, int, int, int]:
    """主显示器矩形；用于判断光标是否在扩展屏上。"""
    hmon = user32.MonitorFromPoint(POINT(0, 0), MONITOR_DEFAULTTOPRIMARY)
    return _monitor_rect_from_handle(hmon)


class InputCapture:
    """后台消息窗口线程：捕获鼠标原始位移/按键，按需转发给安卓端。

    回调：on_move(dx, dy, buttons) / on_button(buttons) / on_wheel(v, h) /
          on_toggle(armed) / on_edge(edge, entered)
    """

    def __init__(self, on_move=None, on_button=None, on_wheel=None, on_toggle=None,
                 on_edge=None, on_key=None, on_clipboard=None,
                 hotkey_mods: int = MOD_CONTROL | MOD_ALT, hotkey_vk: int = VK_Q,
                 edge_switch: bool = True, edge: int = EDGE_RIGHT, edge_zone: int = 6,
                 return_px: int = 96, allow_extended_edge: bool = False):
        self.on_move = on_move or (lambda dx, dy, buttons: None)
        self.on_button = on_button or (lambda buttons: None)
        self.on_wheel = on_wheel or (lambda v, h: None)
        self.on_toggle = on_toggle or (lambda armed: None)
        self.on_edge = on_edge or (lambda edge, entered: None)
        self.on_key = on_key or (lambda vk, scan, extended, up: None)
        self.on_clipboard = on_clipboard or (lambda text: None)
        self.can_capture = lambda: True
        self.hotkey_mods = hotkey_mods
        self.hotkey_vk = hotkey_vk
        self.edge_switch = bool(edge_switch)
        self.allow_extended_edge = bool(allow_extended_edge)
        self.edge = int(edge)
        self.edge_zone = max(int(edge_zone), 1)
        self.return_px = max(int(return_px), 24)
        self.buttons = 0
        self.armed = False
        self.edge_mode = False
        self._hwnd = None
        self._trap = None
        self._thread: threading.Thread | None = None
        self._ready = threading.Event()
        self._running = True
        self._anchor = (0, 0)
        self._acc_x = 0
        self._acc_y = 0
        self._kb_mods = 0
        self._hook = None
        suffix = uuid.uuid4().hex
        self._class_names = ("inputsharex_capture_" + suffix, "inputsharex_trap_" + suffix)

    # -- window thread ---------------------------------------------
    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name="input-capture", daemon=True)
        self._thread.start()
        if not self._ready.wait(5) or not self._hwnd or not self._trap:
            raise RuntimeError("无法建立鼠标捕获窗口，未接管鼠标")

    def _run(self) -> None:
        hinstance = kernel32.GetModuleHandleW(None)
        for cls_name in self._class_names:
            wndclass = WNDCLASSW()
            wndclass.lpfnWndProc = self._wndproc_ref()
            wndclass.hInstance = hinstance
            wndclass.lpszClassName = cls_name
            if not user32.RegisterClassW(ctypes.byref(wndclass)):
                self._ready.set()
                return

        hwnd = user32.CreateWindowExW(
            0, self._class_names[0], "inputsharex", 0, 0, 0, 0, 0, None, None, hinstance, None
        )
        self._hwnd = hwnd
        # 剪贴板变化监听：双向同步用（只需要文本变化通知）
        if not user32.AddClipboardFormatListener(hwnd):
            print("[!] AddClipboardFormatListener 失败，剪贴板同步不可用")
        # 陷阱窗口：接管时贴在锚点上的 4x4 置顶窗口，光标被 ClipCursor 关在里面，
        # 并在 WM_SETCURSOR 里返回空光标 → 用户看不到鼠标。
        self._trap = user32.CreateWindowExW(
            WS_EX_TOPMOST | WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE,
            self._class_names[1], "inputsharex-trap", WS_POPUP, 0, 0, 4, 4,
            None, None, hinstance, None,
        )

        device = RAWINPUTDEVICE(1, 2, RIDEV_INPUTSINK, hwnd)  # generic desktop / mouse
        if not user32.RegisterRawInputDevices(ctypes.byref(device), 1, ctypes.sizeof(RAWINPUTDEVICE)):
            print("[!] RegisterRawInputDevices 失败，鼠标位移可能收不到")
        if not user32.RegisterHotKey(hwnd, 1, self.hotkey_mods | MOD_NOREPEAT, self.hotkey_vk):
            print("[!] 全局热键注册失败（可能被其他程序占用），可换 --hotkey")
        try:
            self._hook = user32.SetWindowsHookExW(WH_KEYBOARD_LL, self._hook_ref(), hinstance, 0)
            if not self._hook:
                print("[!] 键盘钩子安装失败，键盘不会转发到安卓端")
        except Exception:
            self._hook = None
        self._ready.set()

        msg = wintypes.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))
        if self._hook:
            try:
                user32.UnhookWindowsHookEx(self._hook)
            except Exception:
                pass
            self._hook = None
        user32.UnregisterHotKey(hwnd, 1)
        try:
            user32.RemoveClipboardFormatListener(hwnd)
        except Exception:
            pass
        if self._trap:
            user32.DestroyWindow(self._trap)
            self._trap = None
        self._hwnd = None
        for cls_name in self._class_names:
            user32.UnregisterClassW(cls_name, hinstance)

    def _wndproc_ref(self):
        if not hasattr(self, "_proc"):
            self._proc = WNDPROC(self._wndproc)
        return self._proc

    def _wndproc(self, hwnd, msg, wparam, lparam):
        try:
            if msg == WM_INPUT:
                self._handle_raw_input(lparam)
            elif msg == WM_HOTKEY:
                self.toggle()
            elif msg == WM_CLIPBOARDUPDATE:
                self._handle_clipboard_update()
            elif msg == WM_SETCURSOR and hwnd == self._trap:
                # 陷阱窗口不画光标：这是"看不到鼠标"的关键
                user32.SetCursor(None)
                return 1
            elif msg == WM_DESTROY:
                user32.PostQuitMessage(0)
        except Exception:
            pass
        return user32.DefWindowProcW(hwnd, msg, wparam, lparam)

    # -- 剪贴板 -----------------------------------------------------
    def _handle_clipboard_update(self) -> None:
        """剪贴板内容变化：读文本交给上层同步。

        WM_CLIPBOARDUPDATE 到达时剪贴板可能还在更新（格式或数据尚未就绪），
        单次读取会拿到空串，所以短暂重试几次。
        """
        for _ in range(6):
            try:
                text = clipboard.read_text()
            except Exception:
                text = ""
            if text:
                self.on_clipboard(text)
                return
            time.sleep(0.02)

    # -- 键盘转发 ---------------------------------------------------
    def _hook_ref(self):
        if not hasattr(self, "_ll_proc"):
            self._ll_proc = HOOKPROC(self._low_level_keyboard)
        return self._ll_proc

    def _low_level_keyboard(self, n_code, wparam, lparam):
        """接管时截获键盘：修饰键放给系统并转发，其余键转发后拦下本地输入。

        - 修饰键（Ctrl/Shift/Alt）放给系统，RegisterHotKey 才能看到 Ctrl+Alt+Q；
          同时转发给安卓，Ctrl+C / Ctrl+V 才有 Ctrl。
        - 热键键本身放给系统，让 WM_HOTKEY 触发切换，不转发到安卓。
        - 其它键全部拦下（PC 前台应用收不到），只发到安卓。
        - 不在这里判断 LLKHF_INJECTED：本项目自己不会注入按键，保留注入键便于
          自动化验证；实际使用中来自其它软件的注入键也会一并转发。
        """
        passthrough = 0
        try:
            if n_code < 0 or not self.armed:
                return user32.CallNextHookEx(None, n_code, wparam, lparam)
            data = ctypes.cast(lparam, ctypes.POINTER(KBDLLHOOKSTRUCT)).contents
            up = bool(data.flags & LLKHF_UP)
            extended = bool(data.flags & LLKHF_EXTENDED)
            vk = int(data.vkCode)
            scan = int(data.scanCode) & 0xFF
            bit = _modifier_bit(vk, scan, extended)
            if bit:
                self._kb_mods = (self._kb_mods & ~bit) if up else (self._kb_mods | bit)
                self.on_key(vk, scan, extended, up)
                passthrough = 1
            elif not up and self._hotkey_chord(vk):
                passthrough = 1
            else:
                self.on_key(vk, scan, extended, up)
        except Exception:
            passthrough = 1
        if passthrough:
            return user32.CallNextHookEx(None, n_code, wparam, lparam)
        return 1

    def _hotkey_chord(self, vk: int) -> bool:
        """当前按下的修饰键是否正好构成配置的热键组合。"""
        if vk != self.hotkey_vk:
            return False
        ctrl = bool(self._kb_mods & _KB_CTRL_BITS)
        alt = bool(self._kb_mods & _KB_ALT_BITS)
        shift = bool(self._kb_mods & _KB_SHIFT_BITS)
        gui = bool(self._kb_mods & _KB_GUI_BITS)
        return (
            ctrl == bool(self.hotkey_mods & MOD_CONTROL)
            and alt == bool(self.hotkey_mods & MOD_ALT)
            and shift == bool(self.hotkey_mods & MOD_SHIFT)
            and not gui
        )

    # -- raw input -------------------------------------------------
    def _handle_raw_input(self, lparam) -> None:
        size = ctypes.c_uint(ctypes.sizeof(RAWINPUT))
        raw = RAWINPUT()
        got = user32.GetRawInputData(
            ctypes.c_void_p(lparam), RID_INPUT, ctypes.byref(raw), ctypes.byref(size),
            ctypes.sizeof(RAWINPUTHEADER)
        )
        if got == 0xFFFFFFFF:
            return
        flags = raw.mouse.usButtonFlags
        data = raw.mouse.usButtonData

        if flags & RI_MOUSE_LEFT_BUTTON_DOWN:
            self.buttons |= BTN_LEFT
        if flags & RI_MOUSE_LEFT_BUTTON_UP:
            self.buttons &= ~BTN_LEFT
        if flags & RI_MOUSE_RIGHT_BUTTON_DOWN:
            self.buttons |= BTN_RIGHT
        if flags & RI_MOUSE_RIGHT_BUTTON_UP:
            self.buttons &= ~BTN_RIGHT
        if flags & RI_MOUSE_MIDDLE_BUTTON_DOWN:
            self.buttons |= BTN_MIDDLE
        if flags & RI_MOUSE_MIDDLE_BUTTON_UP:
            self.buttons &= ~BTN_MIDDLE
        if flags & RI_MOUSE_BUTTON_4_DOWN:
            self.buttons |= BTN_SIDE
        if flags & RI_MOUSE_BUTTON_4_UP:
            self.buttons &= ~BTN_SIDE
        if flags & RI_MOUSE_BUTTON_5_DOWN:
            self.buttons |= BTN_EXTRA
        if flags & RI_MOUSE_BUTTON_5_UP:
            self.buttons &= ~BTN_EXTRA

        dx, dy = raw.mouse.lLastX, raw.mouse.lLastY

        if not self.armed:
            if self.edge_switch and (dx or dy):
                self._check_edge_trigger(dx, dy)
            return

        if flags & (RI_MOUSE_WHEEL | RI_MOUSE_HWHEEL):
            delta = ctypes.c_short(data).value
            steps = int(delta / 120)
            if steps:
                if flags & RI_MOUSE_WHEEL:
                    self.on_wheel(steps, 0)
                else:
                    self.on_wheel(0, steps)
            return

        self.on_button(self.buttons)
        if dx or dy:
            self.on_move(dx, dy, self.buttons)
            self._warp()
            # 边缘切换与手动接管都支持「反向推回原侧边缘」返回电脑
            self._acc_x += dx
            self._acc_y += dy

    # -- 边缘切换（按光标所在显示器判定）----------------------------
    def _check_edge_trigger(self, dx: int, dy: int,
                            pos: tuple[int, int] | None = None) -> None:
        if pos is None:
            point = POINT()
            if not user32.GetCursorPos(ctypes.byref(point)):
                return
            pos = (point.x, point.y)
        left, top, right, bottom = _monitor_rect(pos[0], pos[1])
        primary = _primary_monitor_rect()
        if not self.allow_extended_edge and (left, top, right, bottom) != primary:
            # 默认不把扩展显示器上撞边当成切换意图，避免外接屏用户频繁误触发。
            _dbg(f"edge-skip-ext pos={pos} monitor={(left, top, right, bottom)} primary={primary}")
            return
        z = self.edge_zone
        edge = self.edge
        near = pos[0] >= right - 1 - z * 4 or pos[0] <= left + z * 4 or pos[1] >= bottom - 1 - z * 4 or pos[1] <= top + z * 4
        if near:
            _dbg(f"edge-check pos={pos} dx={dx} dy={dy} monitor={(left, top, right, bottom)} edge={edge} z={z} can={self.can_capture()}")
        if edge == EDGE_RIGHT and dx > 0 and pos[0] >= right - 1 - z:
            self._enter(pos[0], pos[1], edge_mode=True)
        elif edge == EDGE_LEFT and dx < 0 and pos[0] <= left + z:
            self._enter(pos[0], pos[1], edge_mode=True)
        elif edge == EDGE_BOTTOM and dy > 0 and pos[1] >= bottom - 1 - z:
            self._enter(pos[0], pos[1], edge_mode=True)
        elif edge == EDGE_TOP and dy < 0 and pos[1] <= top + z:
            self._enter(pos[0], pos[1], edge_mode=True)

    def _should_return(self) -> bool:
        # Raw counts do not describe Android's accelerated, clamped cursor position.
        # Return is requested after verifying Android's actual cursor position.
        return False

    def return_from_device(self, fraction: float | None = None) -> None:
        _dbg(f"return-from-device armed={self.armed} fraction={fraction}")
        if self.armed:
            self._return_to_pc(fraction)

    @staticmethod
    def _edge_return_point(edge: int, fraction: float | None,
                           rect: tuple[int, int, int, int],
                           anchor: tuple[int, int], inset: int = 48) -> tuple[int, int]:
        """切回电脑时的落点：fraction 为安卓端沿共享边的比例（0=上/左，1=下/右）。

        用于让电脑端光标落在同比例的边上，实现两边等比例衔接；None 时退回锚点。
        """
        left, top, right, bottom = rect
        ax, ay = anchor
        frac = None if fraction is None else min(max(float(fraction), 0.0), 1.0)
        if edge == EDGE_RIGHT:
            tx = right - inset
            ty = ay if frac is None else top + round(frac * (bottom - top - 1))
        elif edge == EDGE_LEFT:
            tx = left + inset
            ty = ay if frac is None else top + round(frac * (bottom - top - 1))
        elif edge == EDGE_BOTTOM:
            ty = bottom - inset
            tx = ax if frac is None else left + round(frac * (right - left - 1))
        else:
            ty = top + inset
            tx = ax if frac is None else left + round(frac * (right - left - 1))
        tx = min(max(tx, left + 1), right - 1)
        ty = min(max(ty, top + 1), bottom - 1)
        return tx, ty

    def _return_to_pc(self, fraction: float | None = None) -> None:
        """从安卓端切回电脑：光标出现在原来那侧显示器边缘的内侧。"""
        ax, ay = self._anchor
        rect = _monitor_rect(ax, ay)
        tx, ty = self._edge_return_point(self.edge, fraction, rect, (ax, ay))
        _dbg(f"return-mapped edge={self.edge} fraction={fraction} anchor={(ax, ay)} monitor={rect} target={(tx, ty)}")
        self._leave((tx, ty))
        self.on_edge(self.edge, False)

    # -- arm/disarm ------------------------------------------------
    def toggle(self) -> None:
        self.disarm() if self.armed else self.arm()

    def arm(self) -> None:
        """手动接管（热键/按钮）：锚点=当前光标位置，不自动返回。"""
        if self.armed:
            return
        pos = POINT()
        user32.GetCursorPos(ctypes.byref(pos))
        self._enter(pos.x, pos.y, edge_mode=False)

    def _enter(self, x: int, y: int, edge_mode: bool) -> None:
        if self.armed or not self.can_capture():
            _dbg(f"enter-rejected x={x} y={y} edge_mode={edge_mode} armed={self.armed} can={self.can_capture()}")
            return
        _dbg(f"enter-accepted x={x} y={y} edge_mode={edge_mode}")
        self._anchor = (x, y)
        self._acc_x = 0
        self._acc_y = 0
        self.edge_mode = edge_mode
        if self._trap:
            user32.SetWindowPos(
                self._trap, wintypes.HWND(HWND_TOPMOST), x, y, 4, 4,
                SWP_NOACTIVATE | SWP_SHOWWINDOW
            )
        user32.SetCursorPos(x, y)
        self._clip_anchor()
        user32.SetCursor(None)
        self.armed = True
        self._kb_mods = 0
        self.on_toggle(True)
        if edge_mode:
            self.on_edge(self.edge, True)

    def disarm(self) -> None:
        if not self.armed:
            return
        self._leave(self._exit_point())

    def _exit_point(self) -> tuple[int, int]:
        """切回电脑时的落点：落在边缘区内会被下一次移动立刻重新接管，所以向内退开。"""
        ax, ay = self._anchor
        left, top, right, bottom = _monitor_rect(ax, ay)
        inset = 48
        tx, ty = ax, ay
        if self.edge_switch:
            z = max(self.edge_zone, 2)
            if ax >= right - 1 - z:
                tx = right - inset
            elif ax <= left + z:
                tx = left + inset
            elif ay >= bottom - 1 - z:
                ty = bottom - inset
            elif ay <= top + z:
                ty = top + inset
        tx = min(max(tx, left + 1), right - 1)
        ty = min(max(ty, top + 1), bottom - 1)
        return tx, ty

    def _leave(self, pos: tuple[int, int]) -> None:
        if not self.armed:
            return
        _dbg(f"leave-armed pos={pos} edge_mode={self.edge_mode}")
        self.armed = False
        self.edge_mode = False
        self._kb_mods = 0
        user32.ClipCursor(None)
        if self._trap:
            user32.ShowWindow(self._trap, SW_HIDE)
        user32.SetCursorPos(int(pos[0]), int(pos[1]))
        self.on_toggle(False)

    def _clip_anchor(self) -> None:
        """把系统光标关在锚点 1px 的小框里：光标已被陷阱窗口隐藏，用户看不到它。"""
        x, y = self._anchor
        box = RECT(x, y, x + 1, y + 1)
        user32.ClipCursor(ctypes.byref(box))

    def _warp(self) -> None:
        x, y = self._anchor
        user32.SetCursorPos(x, y)

    # -- config ----------------------------------------------------
    def set_edge(self, edge: int) -> None:
        self.edge = int(edge)

    def set_edge_switch(self, enabled: bool) -> None:
        self.edge_switch = bool(enabled)

    def set_allow_extended_edge(self, enabled: bool) -> None:
        """是否允许在扩展显示器上撞边触发切换（关闭时只看主显示器）。"""
        self.allow_extended_edge = bool(enabled)

    # -- lifecycle -------------------------------------------------
    def stop(self) -> None:
        user32.ClipCursor(None)
        if self._trap:
            user32.ShowWindow(self._trap, SW_HIDE)
        if self.armed:
            self.armed = False
            self._kb_mods = 0
            self.on_toggle(False)
        if self._hwnd:
            user32.PostMessageW(self._hwnd, WM_CLOSE, 0, 0)
        if self._thread:
            self._thread.join(timeout=3)
