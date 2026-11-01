"""剪贴板协议与设备消息解析测试（不启动 adb / 不连接设备）。"""
import socket
import struct

from inputsharex import protocol
from inputsharex.session import ControlSession


def _u32(value: int) -> bytes:
    return struct.pack(">I", value)


def _u64(value: int) -> bytes:
    return struct.pack(">Q", value)


def _session() -> tuple[ControlSession, socket.socket]:
    ours, peer = socket.socketpair()
    session = ControlSession(ours, keepalive_interval=0.5, cadence_ms=50)
    return session, peer


def _read_exact(sock: socket.socket, size: int) -> bytes:
    """从 socketpair 对端读满 size 字节（发送是同步的，数据已经在缓冲区里）。"""
    chunks = []
    remaining = size
    while remaining > 0:
        chunk = sock.recv(min(remaining, 65536))
        if not chunk:
            raise AssertionError("socket closed early")
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def test_set_clipboard_frame_format():
    payload = protocol.set_clipboard("abc", paste=True, sequence=7)
    assert payload[0] == protocol.SET_CLIPBOARD
    assert payload[1:9] == _u64(7)
    assert payload[9] == 1
    assert payload[10:14] == _u32(3)
    assert payload[14:] == b"abc"


def test_set_clipboard_truncates_long_text_on_utf8_boundary():
    text = "汉" * 100000  # 300000 字节，超过 262144 上限
    payload = protocol.set_clipboard(text)
    length = struct.unpack(">I", payload[10:14])[0]
    assert length <= protocol.CLIPBOARD_TEXT_MAX_BYTES
    body = payload[14:]
    assert len(body) == length
    # 截断必须落在 UTF-8 字符边界上，不能出现半个汉字。
    assert body.decode("utf-8")


def test_device_clipboard_message_calls_callback():
    session, peer = _session()
    try:
        text = "你好，安卓".encode("utf-8")
        session._recv_buffer += bytes([protocol.DEVICE_MSG_CLIPBOARD]) + _u32(len(text)) + text
        received = []
        session.on_clipboard = received.append
        session._drain_device_messages()
        assert received == ["你好，安卓"]
        assert not session._recv_buffer
    finally:
        peer.close()
        session.sock.close()


def test_device_ack_and_uhid_output_are_parsed():
    session, peer = _session()
    try:
        ack = bytes([protocol.DEVICE_MSG_ACK_CLIPBOARD]) + _u64(42)
        uhid = (bytes([protocol.DEVICE_MSG_UHID_OUTPUT]) + struct.pack(">H", 2)
                + struct.pack(">H", 3) + b"xyz")
        session._recv_buffer += ack + uhid
        acks = []
        session.on_clipboard_ack = acks.append
        session._drain_device_messages()
        assert acks == [42]
        assert not session._recv_buffer
    finally:
        peer.close()
        session.sock.close()


def test_partial_device_message_waits_for_more_data():
    session, peer = _session()
    try:
        full = bytes([protocol.DEVICE_MSG_CLIPBOARD]) + _u32(5) + b"hello"
        session._recv_buffer += full[:6]  # 半条消息
        called = []
        session.on_clipboard = called.append
        session._drain_device_messages()
        assert called == []
        assert len(session._recv_buffer) == 6
        session._recv_buffer += full[6:]
        session._drain_device_messages()
        assert called == ["hello"]
    finally:
        peer.close()
        session.sock.close()


def test_unknown_device_message_raises():
    session, peer = _session()
    try:
        session._recv_buffer += bytes([99])
        try:
            session._drain_device_messages()
        except ValueError:
            pass
        else:
            raise AssertionError("unknown message type should raise")
    finally:
        peer.close()
        session.sock.close()


def test_set_clipboard_text_sends_frame():
    session, peer = _session()
    try:
        assert session.set_clipboard_text("hi", paste=False) is True
        payload = _read_exact(peer, session.sent_bytes)
        assert payload[0] == protocol.SET_CLIPBOARD
        length = struct.unpack(">I", payload[10:14])[0]
        assert length == 2
        assert payload[14:] == b"hi"
        assert session.sent_messages == 1
    finally:
        peer.close()
        session.sock.close()


def test_swipe_horizontal_sends_touch_frames():
    session, peer = _session()
    try:
        session.set_screen_size(1000, 2000)
        assert session.swipe_horizontal(forward=True, steps=4, interval=0.0) is True
        count = 1 + (4 - 1) + 1  # down + moves + up
        frame = 1 + 1 + 8 + 8 + 2 + 2 + 2 + 4 + 4
        payload = _read_exact(peer, count * frame)
        assert len(payload) == count * frame
        # 第一条：按下，手指从右侧 78% 开始（forward=True 表示下一张）
        assert payload[0] == protocol.INJECT_TOUCH_EVENT
        assert payload[1] == 0
        x_first = struct.unpack(">I", payload[10:14])[0]
        assert x_first == int(1000 * 0.78)
        # 最后一条：抬起，落在左侧 22%
        last = payload[(count - 1) * frame:]
        assert last[1] == 1
        x_last = struct.unpack(">I", last[10:14])[0]
        assert x_last == int(1000 * 0.22)
    finally:
        peer.close()
        session.sock.close()


def test_swipe_without_screen_size_returns_false():
    session, peer = _session()
    try:
        assert session.swipe_horizontal(forward=True) is False
        assert session.sent_messages == 0
    finally:
        peer.close()
        session.sock.close()


def test_control_channel_skips_dummy_and_device_name():
    """首 socket 是控制口时，服务端先发 dummy+64 字节设备名，不能被当成设备消息。"""
    from inputsharex.server import ScrcpyServer

    ours, peer = socket.socketpair()
    try:
        peer.sendall(b"\x00" + b"24091RPADC".ljust(64, b"\x00") + b"payload")
        server = ScrcpyServer.__new__(ScrcpyServer)
        server._read_device_meta(ours, consume_dummy=True)
        assert ours.recv(7) == b"payload"
    finally:
        peer.close()
        ours.close()


def test_clipboard_update_retries_when_read_is_empty():
    """WM_CLIPBOARDUPDATE 到达时剪贴板可能尚未就绪，处理器需要短暂重试。"""
    import types

    import inputsharex.capture as capture_module

    readings = iter(["", "", "hello-clip"])
    original = capture_module.clipboard.read_text
    calls = []
    holder = types.SimpleNamespace(on_clipboard=calls.append)
    try:
        capture_module.clipboard.read_text = lambda: next(readings, "")
        capture_module.InputCapture._handle_clipboard_update(holder)
    finally:
        capture_module.clipboard.read_text = original
    assert calls == ["hello-clip"]
