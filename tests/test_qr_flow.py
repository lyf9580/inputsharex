"""二维码配对会话的取消结果不应打扰新界面（回归测试）。

用真实 AppController + FakeAdb：不启动 adb，只验证旧/取消会话的结果不会被
qrPairFinished 发到当前对话框。
"""
import copy
import time
from unittest.mock import patch

from PySide6.QtCore import QCoreApplication

from inputsharex.app import DEFAULT_CONFIG, AppController

APP = QCoreApplication.instance() or QCoreApplication([])


class FakeAdb:
    def __init__(self):
        self.start_calls = 0

    def start_server(self):
        self.start_calls += 1

    def text(self, *args, check=True, **kwargs):
        return ""

    def connect(self, address):
        return ""

    def devices(self):
        return []


class BoomAdb(FakeAdb):
    def start_server(self):
        raise RuntimeError("start boom")


def controller(adb):
    with patch("inputsharex.app.load_config", return_value=copy.deepcopy(DEFAULT_CONFIG)):
        value = AppController()
    value._stats_timer.stop()
    value.adb = adb
    return value


def pump():
    # 跨线程信号是队列投递，需要泵主线程事件才能看到（或确认没有）结果
    for _ in range(5):
        APP.processEvents()
        time.sleep(0.02)


def wait_thread(value, timeout=4.0):
    thread = value._qr_thread
    if thread is not None:
        thread.join(timeout)
    pump()


def test_cancelled_pairing_emits_nothing_and_logs_nothing():
    value = controller(FakeAdb())
    finished = []
    logs = []
    value.qrPairFinished.connect(lambda *args: finished.append(args))
    value.logMessage.connect(logs.append)

    value.start_qr_pairing()
    time.sleep(0.05)
    assert value._qr_thread is not None
    thread = value._qr_thread
    value.cancel_qr_pairing()
    thread.join(4.0)

    assert finished == []
    assert not any("取消" in line for line in logs)


def test_superseded_pairing_result_is_discarded():
    value = controller(FakeAdb())
    finished = []
    value.qrPairFinished.connect(lambda *args: finished.append(args))

    value.start_qr_pairing()
    time.sleep(0.05)
    old_thread = value._qr_thread
    value.start_qr_pairing()  # 取消旧会话并替换
    time.sleep(0.05)
    new_thread = value._qr_thread
    value.cancel_qr_pairing()

    old_thread.join(4.0)
    new_thread.join(4.0)

    assert finished == []


def test_real_failure_is_still_reported():
    value = controller(BoomAdb())
    finished = []
    messages = []
    value.qrPairFinished.connect(lambda ok, serial, message: finished.append((ok, serial, message)))
    value.qrPairStatus.connect(messages.append)

    value.start_qr_pairing()
    wait_thread(value)

    assert len(finished) == 1
    ok, serial, message = finished[0]
    assert ok is False
    assert "二维码配对失败" in message
