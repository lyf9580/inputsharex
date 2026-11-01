import copy
import json
import threading
import time
from unittest.mock import Mock, patch

from PySide6.QtCore import QCoreApplication

from inputsharex.app import AppController, DEFAULT_CONFIG
from inputsharex.capture import InputCapture
from inputsharex.link import DeviceLink

APP = QCoreApplication.instance() or QCoreApplication([])


def controller():
    with patch("inputsharex.app.load_config", return_value=copy.deepcopy(DEFAULT_CONFIG)):
        value = AppController()
    value._stats_timer.stop()
    value.device_link = Mock()
    value.device_link.sharing_state = {}
    value.serial = "192.168.1.2:37123"
    value._auto_start_requested = True
    value.start_sharing = Mock()
    return value


def incoming(value, message):
    value._handle_android_message({"source": value.device_link, "message": message})


def test_next_connection_auto_share_preference_is_respected():
    value = controller()
    incoming(value, {"type": "hello", "auto_share": False})
    value.start_sharing.assert_not_called()
    incoming(value, {"type": "hello", "auto_share": True})
    value.start_sharing.assert_called_once()


def test_changing_next_connection_setting_does_not_start_current_sharing():
    value = controller()
    incoming(value, {"type": "config", "auto_share": True})
    value.start_sharing.assert_not_called()


def test_android_stop_closes_input_without_stopping_auxiliary_connection():
    value = controller()
    value.session = Mock(closed=False)
    value._sharing = True
    incoming(value, {"type": "sharing_request", "enabled": False})
    value.session.close.assert_called_once()
    value.device_link.stop.assert_not_called()
    assert not value._sharing


def test_return_only_accepts_current_generation_matching_edge_and_no_drag():
    value = controller()
    value._capture_generation = 42
    value.capture = Mock(armed=True, buttons=0)
    for generation, placement in [(41, 0), (42, 1)]:
        value._on_verified_edge({"generation": generation, "placement": placement, "x": 0, "y": 500, "max_x": 2135, "max_y": 3199})
    value.capture.return_from_device.assert_not_called()
    value._on_verified_edge({"generation": 42, "placement": 0, "x": 0, "y": 500, "max_x": 2135, "max_y": 3199})
    value.capture.return_from_device.assert_called_once()
    value.capture.buttons = 1
    value._on_verified_edge({"generation": 42, "placement": 0, "x": 0, "y": 500, "max_x": 2135, "max_y": 3199})
    value.capture.return_from_device.assert_called_once()


def test_reverse_raw_distance_alone_never_returns_to_pc():
    capture = InputCapture()
    for edge in range(4):
        capture.edge = edge
        capture._acc_x = capture._acc_y = -100000
        assert not capture._should_return()
        capture._acc_x = capture._acc_y = 100000
        assert not capture._should_return()


def test_near_edge_or_invalid_coordinate_never_returns():
    value = controller()
    value._capture_generation = 42
    value.capture = Mock(armed=True, buttons=0)
    for x in (2, 54, 100, float('nan'), -1):
        value._on_verified_edge({"generation":42, "placement":0, "x":x, "y":500, "max_x":2135, "max_y":3199})
    value.capture.return_from_device.assert_not_called()


def test_all_four_edges_use_real_coordinates():
    value=controller(); value._capture_generation=42
    value.capture=Mock(armed=True,buttons=0)
    for edge,(x,y) in enumerate([(0,500),(2135,500),(500,0),(500,3199)]):
        value.config['placement']=edge
        value._on_verified_edge({'generation':42,'placement':edge,'x':x,'y':y,'max_x':2135,'max_y':3199})
    assert value.capture.return_from_device.call_count==4


def test_manual_profiles_survive_edit_and_deletion_does_not_reappear():
    from inputsharex.devices import build_device_entries
    value=controller()
    value.device_link=None
    value.devices=build_device_entries([{'serial':'192.168.1.2:37123','name':'Tablet','state':'device','wired':False}],[],[])
    snapshots=[]
    with patch('inputsharex.app.save_config',side_effect=lambda cfg:snapshots.append(copy.deepcopy(cfg))):
        key=value.save_device('My tablet','192.168.1.2:37123','tls')
        value.save_device('Renamed tablet','192.168.1.2:37123','tls',previous_id=key)
        assert value.config['device_profiles'][0]['name']=='Renamed tablet'
        value.delete_device(key)
    assert snapshots[0]['device_profiles'][0]['name']=='My tablet'
    assert not snapshots[-1]['device_profiles']
    rebuilt=build_device_entries([{'serial':'192.168.1.2:37123','name':'Tablet','state':'device','wired':False}],value.config['device_profiles'],value.config['hidden_profiles'])
    assert [entry['transport'] for entry in rebuilt]==['adb']


def test_android_configuration_cannot_overwrite_desktop_owned_fields():
    link = DeviceLink()
    link._config = {"placement": 2, "lock_refresh": True}
    link._handle(json.dumps({"type": "config", "placement": 0, "lock_refresh": False, "auto_share": False}).encode())
    assert link._config == {"placement": 2, "lock_refresh": True}
    assert link.device_info["auto_share"] is False


def test_manual_profile_does_not_create_another_transport_or_leave_old_address():
    value = controller()
    value.device_link = None
    with patch('inputsharex.app.save_config'):
        key = value.save_device('Offline tablet', '192.168.1.3:37123', 'tls')
        assert len(value.devices) == 1
        value.save_device('New address', '192.168.1.4:37123', 'tls', previous_id=key)
    assert len(value.devices) == 1
    assert value.devices[0]['serial'] == '192.168.1.4:37123'
    assert value.devices[0]['name'] == 'New address'


def test_restart_waits_for_previous_worker_cleanup():
    value = controller()
    value.start_sharing = AppController.start_sharing.__get__(value)
    release = threading.Event()
    previous = threading.Thread(target=lambda: release.wait(3))
    previous.start()
    value._sharing_thread = previous
    started = threading.Event()
    value._sharing_worker = lambda _: started.set()
    value.start_sharing()
    value.start_sharing()
    assert not started.is_set()
    release.set()
    assert started.wait(3)
    value._sharing_thread.join(3)


def test_stop_cancels_restart_waiting_for_cleanup():
    value = controller()
    value.start_sharing = AppController.start_sharing.__get__(value)
    release = threading.Event()
    previous = threading.Thread(target=lambda: release.wait(3))
    previous.start()
    value._sharing_thread = previous
    started = threading.Event()
    value._sharing_worker = lambda _: started.set()
    value.start_sharing()
    value.stop_sharing()
    release.set()
    previous.join(3)
    deadline = time.monotonic() + 3
    while value._restart_waiting and time.monotonic() < deadline:
        time.sleep(.01)
    assert not value._restart_waiting
    assert not started.is_set()


def test_unready_control_channel_cannot_clip_mouse():
    capture = InputCapture()
    capture.can_capture = lambda: False
    with patch('inputsharex.capture.user32') as windows:
        capture._enter(100, 100, True)
    assert not capture.armed
    windows.ClipCursor.assert_not_called()


def test_repeated_native_capture_start_uses_current_callback_and_cleans_windows():
    from inputsharex.capture import user32, WM_INPUT
    for index in range(6):
        seen = []
        capture = InputCapture(edge_switch=False)
        capture._handle_raw_input = lambda value, target=seen: target.append(value)
        try:
            capture.start()
            user32.PostMessageW(capture._hwnd, WM_INPUT, 0, 1000 + index)
            deadline = time.monotonic() + 2
            while not seen and time.monotonic() < deadline:
                time.sleep(.01)
            assert seen == [1000 + index]
        finally:
            capture.stop()
        assert not capture._thread.is_alive()
        assert capture._hwnd is None and capture._trap is None


def test_hotkey_disarm_moves_cursor_inward_from_edge():
    from inputsharex.capture import _monitor_rect

    capture = InputCapture(edge_switch=True, edge=0, edge_zone=6)
    left, top, right, bottom = _monitor_rect(0, 0)
    border = right - 2
    capture._anchor = (border, (top + bottom) // 2)
    target = capture._exit_point()
    assert target[0] <= right - 48
    assert not (target[0] >= right - 1 - 6)

    capture._anchor = (left + 200, top + 200)
    assert capture._exit_point() == (left + 200, top + 200)


def test_android_config_syncs_desktop_settings():
    value = controller()
    value.capture = Mock()
    value.pointer_monitor = Mock()
    changed = []
    value.configChanged.connect(changed.append)
    with patch("inputsharex.app.save_config") as save:
        incoming(value, {"type": "config", "placement": 2, "keep_awake": False,
                         "lock_refresh": False, "auto_share": True})
    assert value.config["placement"] == 2          # 协议里的 placement 已是电脑端语义
    assert value.config["android_keep_awake"] is False
    assert value.config["lock_refresh"] is False
    value.capture.set_edge.assert_called_once_with(2)
    value.pointer_monitor.set_enabled.assert_called_once_with(False, value._capture_generation, 2)
    assert changed and changed[0]["placement"] == 2
    save.assert_called_once()
    sent = value.device_link.send_config.call_args[0][0]
    assert sent["placement"] == 2
    assert sent["keep_awake"] is False
    assert sent["lock_refresh"] is False
    assert "deadzone" not in sent


def test_android_placement_is_taken_as_desktop_semantics():
    value = controller()
    with patch("inputsharex.app.save_config"):
        incoming(value, {"type": "config", "placement": 2, "auto_share": True})
    assert value.config["placement"] == 2


def test_android_config_invalid_placement_is_ignored():
    value = controller()
    changed = []
    value.configChanged.connect(changed.append)
    with patch("inputsharex.app.save_config") as save:
        incoming(value, {"type": "config", "placement": 9, "auto_share": True})
    assert value.config["placement"] == 0
    assert changed == []
    save.assert_not_called()
    value.device_link.send_config.assert_called_once()


def test_arrow_switch_images_uses_swipe_when_enabled():
    value = controller()
    value.config = dict(value.config, arrow_switch_images=True)
    value.session = Mock(closed=False)
    value.session.swipe_horizontal = Mock(return_value=True)
    value.session.keyboard_event = Mock()
    value._screen_size = (1000, 2000)

    value._on_key(0x27, 0x4D, True, False)  # VK_RIGHT 按下
    value.session.swipe_horizontal.assert_called_once_with(forward=True)
    value.session.keyboard_event.assert_not_called()

    # 按住产生的重复 down 不应该连续翻页。
    value._on_key(0x27, 0x4D, True, False)
    assert value.session.swipe_horizontal.call_count == 1

    # 松开后再次按下可以翻下一张。
    value._on_key(0x27, 0x4D, True, True)
    value._on_key(0x27, 0x4D, True, False)
    assert value.session.swipe_horizontal.call_count == 2

    # 左方向键是上一张。
    value._on_key(0x25, 0x4B, True, False)
    assert value.session.swipe_horizontal.call_args.kwargs["forward"] is False

    # 非方向键仍然走 HID 键盘。
    value._on_key(0x41, 0x1E, False, False)
    value.session.keyboard_event.assert_called_once()


def test_arrow_keys_stay_hid_when_switch_disabled_or_unknown_size():
    value = controller()
    value.config = dict(value.config, arrow_switch_images=False)
    value.session = Mock(closed=False)
    value.session.swipe_horizontal = Mock(return_value=True)
    value.session.keyboard_event = Mock()
    value._screen_size = (1000, 2000)

    value._on_key(0x27, 0x4D, True, False)
    value.session.keyboard_event.assert_called_once_with(0x27, 0x4D, True, False)
    value.session.swipe_horizontal.assert_not_called()

    # 开启但拿不到屏幕分辨率时回退普通方向键，避免手势落点错误。
    value.config = dict(value.config, arrow_switch_images=True)
    value._screen_size = None
    value.session.keyboard_event.reset_mock()
    value._on_key(0x25, 0x4B, True, False)
    value.session.keyboard_event.assert_called_once_with(0x25, 0x4B, True, False)
    value.session.swipe_horizontal.assert_not_called()


def test_extended_monitor_edge_switch_disabled_by_default():
    from inputsharex import capture as capture_module

    value = InputCapture(edge_switch=True, edge=capture_module.EDGE_RIGHT, edge_zone=6)
    entered = []
    value._enter = lambda x, y, edge_mode: entered.append((x, y, edge_mode))
    secondary = (1920, 0, 3840, 1080)
    primary = (0, 0, 1920, 1080)
    with patch.object(capture_module, "_monitor_rect", return_value=secondary), \
            patch.object(capture_module, "_primary_monitor_rect", return_value=primary):
        value._check_edge_trigger(10, 0, pos=(3838, 500))
    assert entered == []

    value.set_allow_extended_edge(True)
    with patch.object(capture_module, "_monitor_rect", return_value=secondary), \
            patch.object(capture_module, "_primary_monitor_rect", return_value=primary):
        value._check_edge_trigger(10, 0, pos=(3838, 500))
    assert entered == [(3838, 500, True)]


def test_primary_monitor_edge_switch_works_when_extended_disabled():
    from inputsharex import capture as capture_module

    value = InputCapture(edge_switch=True, edge=capture_module.EDGE_RIGHT, edge_zone=6)
    entered = []
    value._enter = lambda x, y, edge_mode: entered.append((x, y, edge_mode))
    primary = (0, 0, 1920, 1080)
    with patch.object(capture_module, "_monitor_rect", return_value=primary), \
            patch.object(capture_module, "_primary_monitor_rect", return_value=primary):
        value._check_edge_trigger(10, 0, pos=(1918, 500))
    assert entered == [(1918, 500, True)]


def test_extended_edge_setting_defaults_off_and_updates_capture():
    value = controller()
    assert DEFAULT_CONFIG["edge_on_extended"] is False
    value.capture = Mock()
    with patch("inputsharex.app.save_config") as save:
        value.update_config(edge_on_extended=True)
    save.assert_called_once()
    value.capture.set_allow_extended_edge.assert_called_once_with(True)
