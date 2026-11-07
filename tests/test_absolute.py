import struct

from inputsharex import protocol
from inputsharex.geometry import absolute_entry
from inputsharex.session import ControlSession


class Socket:
    def __init__(self):
        self.packets = []

    def setsockopt(self, *args):
        pass

    def sendall(self, payload):
        self.packets.append(payload)


def decode(packet):
    assert int.from_bytes(packet[:4], "big") == len(packet) - 4
    return struct.unpack(">BBIiiHHff", packet[4:])


def session():
    sock = Socket()
    value = ControlSession(sock, absolute_mouse=True)
    value.set_screen_size(3200, 2136)
    value.set_pointer_speed(1.0)
    return value, sock


def test_first_packet_is_new_proportional_position_without_uhid_mouse():
    value, sock = session()
    value.create_mouse()
    assert not sock.packets
    for fraction in (0, 0.25, 0.75, 1):
        value.enter_pointer(0, fraction)
        frame = decode(sock.packets[-1])
        assert frame[1:7] == (protocol.ABS_ENTER, 0, 1, round(fraction * 2135), 3200, 2136)
        value.leave_pointer()


def test_all_entry_edges_and_full_range():
    assert absolute_entry(0, 0, 3200, 2136) == (1, 0)
    assert absolute_entry(1, 1, 3200, 2136) == (3198, 2135)
    assert absolute_entry(2, 1, 3200, 2136) == (3199, 1)
    assert absolute_entry(3, 0, 3200, 2136) == (0, 2134)


def test_click_flushes_motion_before_button_and_drag_keeps_position():
    value, sock = session()
    value.enter_pointer(0, 0.5)
    value.mouse_move(100, 50)
    value.mouse_button(1)
    before, click = map(decode, sock.packets[-2:])
    assert before[1:5] == (protocol.ABS_MOVE, 0, 101, 1118)
    assert click[1:5] == (protocol.ABS_BUTTONS, 1, 101, 1118)
    value.mouse_move(40, -10, 1)
    value.mouse_button(0)
    drag, release = map(decode, sock.packets[-2:])
    assert drag[1:5] == (protocol.ABS_MOVE, 1, 141, 1108)
    assert release[1:5] == (protocol.ABS_BUTTONS, 0, 141, 1108)


def test_return_requires_outward_movement_and_released_buttons():
    value, _ = session()
    value.enter_pointer(0, 0.75)
    assert value.return_fraction(0, 1, 0) is None
    value.mouse_move(-50, 0, 1)
    assert value.return_fraction(0, -50, 0) is None
    value.mouse_button(0)
    assert abs(value.return_fraction(0, -1, 0) - round(0.75 * 2135) / 2135) < 1e-9


def test_large_motion_clamps_without_relative_report_backlog():
    value, _ = session()
    value.enter_pointer(0, 0)
    value.mouse_move(100000, 100000)
    frame = struct.unpack(">BBIiiHHff", value._build_tick_locked())
    assert frame[3:5] == (3199, 2135)
    assert value._dx == value._dy == 0
    assert not value._dirty


def test_scroll_and_leave_share_current_position():
    value, sock = session()
    value.enter_pointer(0, 0.5)
    value.scroll(2, -1)
    scroll = struct.unpack(">BBIiiHHff", value._build_tick_locked())
    assert scroll[1] == protocol.ABS_SCROLL and scroll[-2:] == (-1.0, 2.0)
    value.leave_pointer()
    assert decode(sock.packets[-1])[1:3] == (protocol.ABS_LEAVE, 0)
    value.mouse_move(500, 500)
    assert value.return_fraction(0, -1, 0) is None


def test_rotation_preserves_normalized_position():
    value, _ = session()
    value.enter_pointer(0, 0.5)
    value.set_screen_size(2136, 3200)
    assert abs(value._pointer_y / 3199 - 0.5) < 0.001


def test_fractional_speed_retains_subpixel_motion_and_direction():
    value, _ = session()
    value.set_pointer_speed(0.25)
    value.enter_pointer(0, 0.5)
    for _ in range(40):
        value.mouse_move(1, -1)
    assert (value._pointer_x, value._pointer_y) == (11, 1058)
    for _ in range(40):
        value.mouse_move(-1, 1)
    assert (value._pointer_x, value._pointer_y) == (1, 1068)


def test_speed_change_while_dragging_preserves_buttons_and_entry_mapping():
    value, _ = session()
    value.set_pointer_speed(3.0)
    value.enter_pointer(0, 0.5)
    assert (value._pointer_x, value._pointer_y) == (1, 1068)
    value.mouse_button(1)
    value.mouse_move(100, 10, 1)
    value.set_pointer_speed(2.0)
    value.mouse_move(100, 10, 1)
    assert (value._pointer_x, value._pointer_y, value.mouse_buttons) == (501, 1118, 1)


def test_size_change_sends_configuration_without_moving_or_releasing_mouse():
    value, sock = session()
    value.enter_pointer(0, 0.5)
    value.mouse_button(1)
    value.set_cursor_scale(3.0)
    packet = decode(sock.packets[-1])
    assert packet[1:5] == (protocol.ABS_CONFIG, 1, 1, 1068)
    assert packet[-2] == 3.0
    assert value._pointer_active and value.mouse_buttons == 1


def test_invalid_speed_and_size_are_rejected_before_send():
    value, sock = session()
    for setter, values in ((value.set_pointer_speed, (0, 10, float("nan"))),
                           (value.set_cursor_scale, (0, 5, float("inf")))):
        for invalid in values:
            try:
                setter(invalid)
            except ValueError:
                pass
            else:
                raise AssertionError("invalid pointer configuration accepted")
    assert not sock.packets
