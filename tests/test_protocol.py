"""协议层单元测试：帧结构与拆帧逻辑。"""
import struct

from inputsharex import hid, protocol


def test_uhid_create_layout():
    msg = protocol.uhid_create(1, "m", b"\x05\x01", vendor=0x1234, product=0x5678)
    assert msg[0] == protocol.UHID_CREATE
    hid_id, vendor, product = struct.unpack(">HHH", msg[1:7])
    assert (hid_id, vendor, product) == (1, 0x1234, 0x5678)
    name_len = msg[7]
    assert name_len == 1 and msg[8:9] == b"m"
    (desc_len,) = struct.unpack(">H", msg[9:11])
    assert desc_len == 2 and msg[11:] == b"\x05\x01"


def test_uhid_input_layout():
    msg = protocol.uhid_input(2, b"\x01\x02")
    assert msg[0] == protocol.UHID_INPUT
    assert struct.unpack(">H", msg[1:3])[0] == 2
    assert struct.unpack(">H", msg[3:5])[0] == 2
    assert msg[5:] == b"\x01\x02"


def test_mouse_report_clamps():
    report = hid.mouse_report(hid.BTN_LEFT, 200, -300)
    assert report == bytes([hid.BTN_LEFT, 127, 0x81, 0, 0])


def test_mouse_reports_split():
    reports = hid.mouse_reports(0, 300, -260)
    total_x = sum(struct.unpack("b", bytes([r[1]]))[0] for r in reports)
    total_y = sum(struct.unpack("b", bytes([r[2]]))[0] for r in reports)
    assert total_x == 300 and total_y == -260
    assert len(reports) == 3


def test_mouse_reports_keep_buttons():
    reports = hid.mouse_reports(hid.BTN_RIGHT, 0, 0)
    assert reports == [bytes([hid.BTN_RIGHT, 0, 0, 0, 0])]


def test_descriptors_nonempty():
    assert hid.MOUSE_DESCRIPTOR and hid.KEYBOARD_DESCRIPTOR
    assert hid.MOUSE_DESCRIPTOR[-1] == 0xC0
