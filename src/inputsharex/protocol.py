"""scrcpy 控制通道二进制协议（v3.3.3，按服务端 ControlMessageReader 核对）。

全部大端序；每条消息 = type(u8) + payload。
"""
from __future__ import annotations

import struct

# --- message types ---
INJECT_KEYCODE = 0
INJECT_TEXT = 1
INJECT_TOUCH_EVENT = 2
INJECT_SCROLL_EVENT = 3
BACK_OR_SCREEN_ON = 4
EXPAND_NOTIFICATION_PANEL = 5
EXPAND_SETTINGS_PANEL = 6
COLLAPSE_PANELS = 7
GET_CLIPBOARD = 8
SET_CLIPBOARD = 9
SET_DISPLAY_POWER = 10
ROTATE_DEVICE = 11
UHID_CREATE = 12
UHID_INPUT = 13
UHID_DESTROY = 14
OPEN_HARD_KEYBOARD_SETTINGS = 15
START_APP = 16
RESET_VIDEO = 17


def _u8(v: int) -> bytes:
    return struct.pack(">B", v & 0xFF)


def _u16(v: int) -> bytes:
    return struct.pack(">H", v & 0xFFFF)


def _u32(v: int) -> bytes:
    return struct.pack(">I", v & 0xFFFFFFFF)


def _u64(v: int) -> bytes:
    return struct.pack(">Q", v & 0xFFFFFFFFFFFFFFFF)


def _i16(v: int) -> bytes:
    return struct.pack(">h", max(-32768, min(32767, v)))


def _string(text: str, length_bytes: int = 1) -> bytes:
    raw = text.encode("utf-8")
    if length_bytes == 1:
        if len(raw) > 0xFF:
            raise ValueError("string too long for 1-byte length")
        return _u8(len(raw)) + raw
    return _u16(len(raw)) + raw


def _byte_array(data: bytes, length_bytes: int = 2) -> bytes:
    if len(data) > 0xFFFF:
        raise ValueError("byte array too long")
    return _u16(len(data)) + data


def uhid_create(hid_id: int, name: str, descriptor: bytes, vendor: int = 0, product: int = 0) -> bytes:
    return (
        _u8(UHID_CREATE)
        + _u16(hid_id)
        + _u16(vendor)
        + _u16(product)
        + _string(name, 1)
        + _byte_array(descriptor, 2)
    )


def uhid_input(hid_id: int, data: bytes) -> bytes:
    return _u8(UHID_INPUT) + _u16(hid_id) + _byte_array(data, 2)


def uhid_destroy(hid_id: int) -> bytes:
    return _u8(UHID_DESTROY) + _u16(hid_id)


def inject_keycode(action: int, keycode: int, repeat: int = 0, meta_state: int = 0) -> bytes:
    return _u8(INJECT_KEYCODE) + _u8(action) + _u32(keycode) + _u32(repeat) + _u32(meta_state)


def inject_text(text: str) -> bytes:
    return _u8(INJECT_TEXT) + _string(text, 1)


def inject_touch(action: int, pointer_id: int, x: int, y: int, screen_w: int, screen_h: int,
                 pressure: int = 0xFFFF, action_button: int = 0, buttons: int = 0) -> bytes:
    position = ((x & 0xFFFFFFFF) << 32) | (y & 0xFFFFFFFF)
    return (
        _u8(INJECT_TOUCH_EVENT)
        + _u8(action)
        + _u64(pointer_id)
        + _u64(position)
        + _u16(screen_w)
        + _u16(screen_h)
        + _u16(pressure)
        + _u32(action_button)
        + _u32(buttons)
    )


def inject_scroll(x: int, y: int, h_scroll: float, v_scroll: float, buttons: int = 0) -> bytes:
    # 位置是 16.16 定点，客户端按浮点写低 16 位；这里用整数像素 <<16
    position = ((x & 0xFFFFFFFF) << 32) | (y & 0xFFFFFFFF)
    return (
        _u8(INJECT_SCROLL_EVENT)
        + _u64(position)
        + _i16(int(h_scroll * 32767 / 16))
        + _i16(int(v_scroll * 32767 / 16))
        + _u32(buttons)
    )


def set_display_power(on: bool) -> bytes:
    return _u8(SET_DISPLAY_POWER) + _u8(1 if on else 0)


def set_clipboard(text: str, paste: bool = False, sequence: int = 0) -> bytes:
    return (
        _u8(SET_CLIPBOARD)
        + _u64(sequence)
        + _u8(1 if paste else 0)
        + _string(text, 1)
    )


def get_clipboard(copy_key: int = 0) -> bytes:
    return _u8(GET_CLIPBOARD) + _u8(copy_key)


def back_or_screen_on(action: int = 0) -> bytes:
    return _u8(BACK_OR_SCREEN_ON) + _u8(action)


EMPTY_MESSAGES = {
    "expand_notification_panel": EXPAND_NOTIFICATION_PANEL,
    "expand_settings_panel": EXPAND_SETTINGS_PANEL,
    "collapse_panels": COLLAPSE_PANELS,
    "rotate_device": ROTATE_DEVICE,
    "open_hard_keyboard_settings": OPEN_HARD_KEYBOARD_SETTINGS,
    "reset_video": RESET_VIDEO,
}


def empty(name: str) -> bytes:
    return _u8(EMPTY_MESSAGES[name])
