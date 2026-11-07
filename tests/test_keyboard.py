"""键盘转发：Windows 键位映射、HID 报文与 UHID 键盘发送。"""
from __future__ import annotations

from inputsharex import hid, keyboard, protocol
from inputsharex.capture import InputCapture, MOD_ALT, MOD_CONTROL
from inputsharex.session import ControlSession


class FakeSock:
    def __init__(self):
        self.data = bytearray()
        self.shutdown_called = False

    def setsockopt(self, *args):
        pass

    def sendall(self, payload):
        self.data += payload

    def shutdown(self, *args):
        self.shutdown_called = True

    def close(self):
        pass


def _session():
    sock = FakeSock()
    return sock, ControlSession(sock)


def test_letter_and_digit_usages():
    assert keyboard.usage_for(0x41, 0x1E, False) == 0x04  # A
    assert keyboard.usage_for(0x5A, 0x2C, False) == 0x1D  # Z
    assert keyboard.usage_for(0x31, 0x02, False) == 0x1E  # 1
    assert keyboard.usage_for(0x30, 0x0B, False) == 0x27  # 0
    assert keyboard.usage_for(0x20, 0x39, False) == 0x2C  # space
    assert keyboard.usage_for(0xBD, 0x0C, False) == 0x2D  # -
    assert keyboard.usage_for(0xBE, 0x34, False) == 0x37  # .


def test_numpad_uses_scan_code_regardless_of_numlock():
    assert keyboard.usage_for(0x67, 0x47, False) == 0x5F  # 小键盘 7
    assert keyboard.usage_for(0x61, 0x4F, False) == 0x59  # 小键盘 1（NumLock 开时 VK_NUMPAD1）
    assert keyboard.usage_for(0x24, 0x47, False) == 0x5F  # NumLock 关时 VK_HOME，仍按小键盘 7 发
    assert keyboard.usage_for(0x60, 0x52, False) == 0x62  # 小键盘 0
    assert keyboard.usage_for(0x6E, 0x53, False) == 0x63  # 小键盘 .
    assert keyboard.usage_for(0x6B, 0x4E, False) == 0x57  # 小键盘 +
    assert keyboard.usage_for(0x6D, 0x4A, False) == 0x56  # 小键盘 -


def test_arrows_and_edit_keys_use_extended_scan_codes():
    assert keyboard.usage_for(0x26, 0x48, True) == 0x52  # 上
    assert keyboard.usage_for(0x28, 0x50, True) == 0x51  # 下
    assert keyboard.usage_for(0x25, 0x4B, True) == 0x50  # 左
    assert keyboard.usage_for(0x27, 0x4D, True) == 0x4F  # 右
    assert keyboard.usage_for(0x2D, 0x52, True) == 0x49  # Insert
    assert keyboard.usage_for(0x2E, 0x53, True) == 0x4C  # Delete
    assert keyboard.usage_for(0x24, 0x47, True) == 0x4A  # Home
    assert keyboard.usage_for(0x23, 0x4F, True) == 0x4D  # End
    assert keyboard.usage_for(0x0D, 0x1C, True) == 0x58  # 小键盘 Enter
    assert keyboard.usage_for(0x0D, 0x1C, False) == 0x28  # 主键盘 Enter


def test_function_keys_and_unknown_media_keys():
    assert keyboard.usage_for(0x70, 0x3B, False) == 0x3A  # F1
    assert keyboard.usage_for(0x7B, 0x57, False) == 0x45  # F12
    assert keyboard.usage_for(0xAD, 0x20, True) is None  # 静音键：不转发


def test_modifier_bit():
    assert keyboard.modifier_bit(keyboard.VK_SHIFT, 0x2A, False) == hid.KB_MOD_LEFT_SHIFT
    assert keyboard.modifier_bit(keyboard.VK_SHIFT, 0x36, False) == hid.KB_MOD_RIGHT_SHIFT
    assert keyboard.modifier_bit(keyboard.VK_CONTROL, 0x1D, False) == hid.KB_MOD_LEFT_CTRL
    assert keyboard.modifier_bit(keyboard.VK_CONTROL, 0x1D, True) == hid.KB_MOD_RIGHT_CTRL
    assert keyboard.modifier_bit(keyboard.VK_MENU, 0x38, False) == hid.KB_MOD_LEFT_ALT
    assert keyboard.modifier_bit(keyboard.VK_MENU, 0x38, True) == hid.KB_MOD_RIGHT_ALT
    assert keyboard.modifier_bit(0x41, 0x1E, False) == 0


def test_keyboard_report_layout():
    assert hid.keyboard_report() == bytes([0, 0, 0, 0, 0, 0, 0, 0])
    assert hid.keyboard_report(0x01, [0x06]) == bytes([0x01, 0, 0x06, 0, 0, 0, 0, 0])
    assert len(hid.keyboard_report(0, list(range(1, 10)))) == hid.KEYBOARD_REPORT_SIZE


def test_session_creates_keyboard_device():
    sock, session = _session()
    session.create_keyboard("inputsharex")
    expected = protocol.uhid_create(hid.HID_KEYBOARD_ID, "inputsharex-kbd", hid.KEYBOARD_DESCRIPTOR)
    assert bytes(sock.data) == expected


def test_session_sends_only_on_state_change():
    sock, session = _session()
    session.create_keyboard("inputsharex")
    start = len(sock.data)

    session.keyboard_event(0x11, 0x1D, False, False)  # Ctrl down
    session.keyboard_event(0x43, 0x2E, False, False)  # C down
    after_two = len(sock.data)
    session.keyboard_event(0x43, 0x2E, False, False)  # C 按住重复上报：不应再发
    assert len(sock.data) == after_two
    session.keyboard_event(0x43, 0x2E, False, True)   # C up
    session.keyboard_event(0x11, 0x1D, False, True)   # Ctrl up

    ctrl = protocol.uhid_input(hid.HID_KEYBOARD_ID, hid.keyboard_report(hid.KB_MOD_LEFT_CTRL))
    ctrl_c = protocol.uhid_input(
        hid.HID_KEYBOARD_ID, hid.keyboard_report(hid.KB_MOD_LEFT_CTRL, [0x06])
    )
    c_release = protocol.uhid_input(hid.HID_KEYBOARD_ID, hid.keyboard_report(hid.KB_MOD_LEFT_CTRL))
    ctrl_release = protocol.uhid_input(hid.HID_KEYBOARD_ID, hid.keyboard_report())
    assert bytes(sock.data[start:]) == ctrl + ctrl_c + c_release + ctrl_release


def test_session_release_keys_clears_stuck_keys():
    sock, session = _session()
    session.create_keyboard("inputsharex")
    session.keyboard_event(0x11, 0x1D, False, False)
    session.keyboard_event(0x41, 0x1E, False, False)
    session.release_keys()
    assert bytes(sock.data).endswith(
        protocol.uhid_input(hid.HID_KEYBOARD_ID, hid.keyboard_report())
    )
    before = len(sock.data)
    session.release_keys()  # 已经清空：不应重复发
    assert len(sock.data) == before


def test_six_key_rollover_limit():
    sock, session = _session()
    session.create_keyboard("inputsharex")
    start = len(sock.data)
    for i in range(7):
        session.keyboard_event(0x41 + i, 0x1E + i, False, False)
    # 前 6 个键各发一帧（类型1+id2+长度2+报文8=13 字节），第 7 个被 6KRO 上限忽略
    assert len(sock.data) - start == 6 * 13


def test_create_keyboard_is_idempotent():
    """重复创建只发一次 UHID_CREATE，避免安卓端出现重复键盘设备。"""
    sock, session = _session()
    session.create_keyboard("inputsharex")
    once = bytes(sock.data)
    session.create_keyboard("inputsharex")
    assert bytes(sock.data) == once


def test_keyboard_event_creates_device_lazily():
    """没有预创建键盘时，第一次按键前会自动补一条 UHID_CREATE。"""
    sock, session = _session()
    session.keyboard_event(0x41, 0x1E, False, False)
    expected = protocol.uhid_create(
        hid.HID_KEYBOARD_ID, "inputsharex-kbd", hid.KEYBOARD_DESCRIPTOR
    ) + protocol.uhid_input(hid.HID_KEYBOARD_ID, hid.keyboard_report(0, [0x04]))
    assert bytes(sock.data) == expected


def test_destroy_keyboard_removes_device_and_resets_keys():
    """切回电脑销毁键盘设备，安卓才会恢复自带软键盘。"""
    sock, session = _session()
    session.create_keyboard("inputsharex")
    session.keyboard_event(0x11, 0x1D, False, False)
    session.destroy_keyboard()
    assert bytes(sock.data).endswith(protocol.uhid_destroy(hid.HID_KEYBOARD_ID))
    before = len(sock.data)
    session.release_keys()  # 设备已销毁：不应再发按键报文
    session.destroy_keyboard()  # 重复销毁：不应重复发
    assert len(sock.data) == before
    # 再次接管可以重新创建
    session.create_keyboard("inputsharex")
    assert bytes(sock.data).endswith(
        protocol.uhid_create(hid.HID_KEYBOARD_ID, "inputsharex-kbd", hid.KEYBOARD_DESCRIPTOR)
    )


def test_release_keys_without_device_is_noop():
    """未接管时（还没有键盘设备）release_keys 不应发任何报文。"""
    sock, session = _session()
    session.release_keys()
    assert bytes(sock.data) == b""


def test_hotkey_chord_detection():
    cap = InputCapture(hotkey_mods=MOD_CONTROL | MOD_ALT, hotkey_vk=0x51)
    cap._kb_mods = hid.KB_MOD_LEFT_CTRL | hid.KB_MOD_LEFT_ALT
    cap._kb_mods = hid.KB_MOD_LEFT_CTRL | hid.KB_MOD_LEFT_ALT
    assert cap._hotkey_chord(0x51) is True
    assert cap._hotkey_chord(0x52) is False
    cap._kb_mods |= hid.KB_MOD_LEFT_SHIFT
    assert cap._hotkey_chord(0x51) is False
    cap._kb_mods = hid.KB_MOD_LEFT_CTRL
    assert cap._hotkey_chord(0x51) is False
