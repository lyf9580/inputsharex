"""Windows 虚拟键码 / 扫描码 → USB HID 键盘用法（usage）。"""
from __future__ import annotations

from . import hid

# -- 虚拟键码 ----------------------------------------------------------
VK_BACK = 0x08
VK_TAB = 0x09
VK_RETURN = 0x0D
VK_SHIFT = 0x10
VK_CONTROL = 0x11
VK_MENU = 0x12
VK_PAUSE = 0x13
VK_CAPITAL = 0x14
VK_ESCAPE = 0x1B
VK_SPACE = 0x20
VK_PRIOR = 0x21
VK_NEXT = 0x22
VK_END = 0x23
VK_HOME = 0x24
VK_LEFT = 0x25
VK_UP = 0x26
VK_RIGHT = 0x27
VK_DOWN = 0x28
VK_SNAPSHOT = 0x2C
VK_INSERT = 0x2D
VK_DELETE = 0x2E
VK_LWIN = 0x5B
VK_RWIN = 0x5C
VK_APPS = 0x5D
VK_NUMPAD0 = 0x60
VK_NUMPAD1 = 0x61
VK_NUMPAD2 = 0x62
VK_NUMPAD3 = 0x63
VK_NUMPAD4 = 0x64
VK_NUMPAD5 = 0x65
VK_NUMPAD6 = 0x66
VK_NUMPAD7 = 0x67
VK_NUMPAD8 = 0x68
VK_NUMPAD9 = 0x69
VK_MULTIPLY = 0x6A
VK_ADD = 0x6B
VK_SUBTRACT = 0x6D
VK_DECIMAL = 0x6E
VK_DIVIDE = 0x6F
VK_F1 = 0x70
VK_F12 = 0x7B
VK_NUMLOCK = 0x90
VK_SCROLL = 0x91
VK_LSHIFT = 0xA0
VK_RSHIFT = 0xA1
VK_LCONTROL = 0xA2
VK_RCONTROL = 0xA3
VK_LMENU = 0xA4
VK_RMENU = 0xA5
VK_OEM_1 = 0xBA
VK_OEM_PLUS = 0xBB
VK_OEM_COMMA = 0xBC
VK_OEM_MINUS = 0xBD
VK_OEM_PERIOD = 0xBE
VK_OEM_2 = 0xBF
VK_OEM_3 = 0xC0
VK_OEM_4 = 0xDB
VK_OEM_5 = 0xDC
VK_OEM_6 = 0xDD
VK_OEM_7 = 0xDE
VK_OEM_102 = 0xE2

# 非扩展扫描码下的数字小键盘（NumLock 开关不影响扫描码，统一按小键盘数字发）。
_NUMPAD_SCAN = {
    0x47: 0x5F,  # 7
    0x48: 0x60,  # 8
    0x49: 0x61,  # 9
    0x4A: 0x56,  # -
    0x4B: 0x5C,  # 4
    0x4C: 0x5D,  # 5
    0x4D: 0x5E,  # 6
    0x4E: 0x57,  # +
    0x4F: 0x59,  # 1
    0x50: 0x5A,  # 2
    0x51: 0x5B,  # 3
    0x52: 0x62,  # 0
    0x53: 0x63,  # .
    0x37: 0x55,  # *
}

_NUMPAD_VK = {
    VK_NUMPAD0: 0x62,
    VK_NUMPAD1: 0x59,
    VK_NUMPAD2: 0x5A,
    VK_NUMPAD3: 0x5B,
    VK_NUMPAD4: 0x5C,
    VK_NUMPAD5: 0x5D,
    VK_NUMPAD6: 0x5E,
    VK_NUMPAD7: 0x5F,
    VK_NUMPAD8: 0x60,
    VK_NUMPAD9: 0x61,
    VK_MULTIPLY: 0x55,
    VK_ADD: 0x57,
    VK_SUBTRACT: 0x56,
    VK_DECIMAL: 0x63,
    VK_DIVIDE: 0x54,
}

_SPECIAL = {
    VK_ESCAPE: 0x29,
    VK_TAB: 0x2B,
    VK_BACK: 0x2A,
    VK_SPACE: 0x2C,
    VK_CAPITAL: 0x39,
    VK_NUMLOCK: 0x53,
    VK_SCROLL: 0x47,
    VK_PAUSE: 0x48,
    VK_SNAPSHOT: 0x46,
    VK_INSERT: 0x49,
    VK_HOME: 0x4A,
    VK_PRIOR: 0x4B,
    VK_DELETE: 0x4C,
    VK_END: 0x4D,
    VK_NEXT: 0x4E,
    VK_RIGHT: 0x4F,
    VK_LEFT: 0x50,
    VK_DOWN: 0x51,
    VK_UP: 0x52,
    VK_APPS: 0x65,
    VK_OEM_1: 0x33,      # ;
    VK_OEM_PLUS: 0x2E,   # =
    VK_OEM_COMMA: 0x36,  # ,
    VK_OEM_MINUS: 0x2D,  # -
    VK_OEM_PERIOD: 0x37,  # .
    VK_OEM_2: 0x38,      # /
    VK_OEM_3: 0x35,      # `
    VK_OEM_4: 0x2F,      # [
    VK_OEM_5: 0x31,      # \
    VK_OEM_6: 0x30,      # ]
    VK_OEM_7: 0x34,      # '
    VK_OEM_102: 0x64,    # 非美键盘的 < >（键位右下）
}


def modifier_bit(vk: int, scan: int, extended: bool) -> int:
    """返回该键对应的 HID 修饰键位；不是修饰键时返回 0。"""
    if vk in (VK_SHIFT, VK_LSHIFT, VK_RSHIFT):
        right = vk == VK_RSHIFT or (vk == VK_SHIFT and scan == 0x36)
        return hid.KB_MOD_RIGHT_SHIFT if right else hid.KB_MOD_LEFT_SHIFT
    if vk in (VK_CONTROL, VK_LCONTROL, VK_RCONTROL):
        right = vk == VK_RCONTROL or (vk == VK_CONTROL and extended)
        return hid.KB_MOD_RIGHT_CTRL if right else hid.KB_MOD_LEFT_CTRL
    if vk in (VK_MENU, VK_LMENU, VK_RMENU):
        right = vk == VK_RMENU or (vk == VK_MENU and extended)
        return hid.KB_MOD_RIGHT_ALT if right else hid.KB_MOD_LEFT_ALT
    if vk == VK_LWIN:
        return hid.KB_MOD_LEFT_GUI
    if vk == VK_RWIN:
        return hid.KB_MOD_RIGHT_GUI
    return 0


def usage_for(vk: int, scan: int, extended: bool) -> int | None:
    """返回该键对应的 HID 键盘 usage；不想转发的特殊键返回 None。"""
    # 数字小键盘：只认非扩展扫描码（扩展扫描码的 0x47-0x53 是方向键/编辑键）。
    if not extended and scan in _NUMPAD_SCAN:
        return _NUMPAD_SCAN[scan]
    if vk in _NUMPAD_VK:
        return _NUMPAD_VK[vk]
    # 26 个字母
    if 0x41 <= vk <= 0x5A:
        return 0x04 + (vk - 0x41)
    # 主键盘区数字：1..9 是 0x1E..0x26，0 是 0x27
    if vk == 0x30:
        return 0x27
    if 0x31 <= vk <= 0x39:
        return 0x1E + (vk - 0x31)
    if vk == VK_RETURN:
        return 0x58 if extended else 0x28
    if VK_F1 <= vk <= VK_F12:
        return 0x3A + (vk - VK_F1)
    return _SPECIAL.get(vk)


def is_modifier(vk: int) -> bool:
    return vk in (
        VK_SHIFT, VK_CONTROL, VK_MENU, VK_LSHIFT, VK_RSHIFT,
        VK_LCONTROL, VK_RCONTROL, VK_LMENU, VK_RMENU, VK_LWIN, VK_RWIN,
    )
