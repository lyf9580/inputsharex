import struct

from inputsharex import hid
from inputsharex.session import ControlSession


class FakeSocket:
    def __init__(self):
        self.packets = []

    def setsockopt(self, *args):
        pass

    def sendall(self, payload):
        self.packets.append(payload)


def reports(payload):
    result = []
    while payload:
        assert payload[0] == 13
        length = int.from_bytes(payload[3:5], 'big')
        result.append(struct.unpack('Bbbbb', payload[5:5 + length]))
        payload = payload[5 + length:]
    return result


def test_raw_motion_button_callbacks_do_not_send_empty_packets():
    sock = FakeSocket()
    session = ControlSession(sock)
    for _ in range(1000):
        session.mouse_button(0)
        session.mouse_move(4, 1, 0)
    assert not sock.packets
    assert session._dx == 4000 and session._dy == 1000


def test_held_button_is_not_repeated_on_every_move():
    sock = FakeSocket()
    session = ControlSession(sock)
    session.mouse_button(hid.BTN_LEFT)
    for _ in range(1000):
        session.mouse_button(hid.BTN_LEFT)
    session.mouse_button(0)
    assert [reports(packet)[0][0] for packet in sock.packets] == [hid.BTN_LEFT, 0]


def test_mouse_click_still_sends_press_and_release():
    sock = FakeSocket()
    session = ControlSession(sock)
    session.mouse_click(hid.BTN_LEFT, hold=0)
    assert [reports(packet)[0][0] for packet in sock.packets] == [hid.BTN_LEFT, 0]


def test_click_does_not_release_another_held_button():
    sock = FakeSocket()
    session = ControlSession(sock)
    session.mouse_button(hid.BTN_RIGHT)
    session.mouse_click(hid.BTN_LEFT, hold=0)
    assert [reports(packet)[0][0] for packet in sock.packets] == [2, 3, 2]


def test_movement_before_click_keeps_old_button_state():
    sock = FakeSocket()
    session = ControlSession(sock)
    session.mouse_move(25, -17)
    session.mouse_button(hid.BTN_LEFT)
    assert reports(sock.packets[0]) == [(0, 25, -17, 0, 0)]
    assert reports(sock.packets[1]) == [(hid.BTN_LEFT, 0, 0, 0, 0)]


def test_large_motion_is_never_discarded_at_report_limit():
    for dx, dy in [(10000, -7231), (-10000, 173), (1017, 0), (0, -10000)]:
        session = ControlSession(FakeSocket())
        session.mouse_move(dx, dy)
        total_x = total_y = 0
        ticks = 0
        while session._dirty:
            batch = reports(session._build_tick_locked())
            assert len(batch) <= 8
            total_x += sum(report[1] for report in batch)
            total_y += sum(report[2] for report in batch)
            ticks += 1
            assert ticks < 20
        assert (total_x, total_y) == (dx, dy)


def test_large_pending_motion_is_flushed_before_button_release():
    sock = FakeSocket()
    session = ControlSession(sock)
    session.mouse_button(hid.BTN_LEFT)
    session.mouse_move(3000, -1700)
    session.mouse_button(0)
    movement = [report for packet in sock.packets[1:-1] for report in reports(packet)]
    assert sum(report[1] for report in movement) == 3000
    assert sum(report[2] for report in movement) == -1700
    assert all(report[0] == hid.BTN_LEFT for report in movement)
    assert not session._dirty
