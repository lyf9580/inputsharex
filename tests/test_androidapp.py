"""androidapp 单元测试（纯逻辑，不启动 adb）。"""
from __future__ import annotations

import os
import tempfile
import types

from inputsharex import androidapp
from inputsharex.androidapp import PACKAGE, MAIN_ACTIVITY

INSTALLED_TEXT = f"""Packages:
  Package [{PACKAGE}] (5f8f3d2):
    userId=10321
    versionCode=9 minSdk=31 targetSdk=36
    versionName=0.2.2
"""

MISSING_TEXT = "Unable to find package: com.inputsharex.app\n"


class FakeAdb:
    """按顺序返回 dumpsys 结果，并记录 install / am start 调用。"""

    def __init__(self, dumpsys_outputs, install_result=(0, "Success\n", "")):
        self.serial = None
        self.calls = []
        self.dumpsys_outputs = list(dumpsys_outputs)
        self.install_result = install_result
        self._dumpsys_index = 0

    def run_for(self, serial, *args, check=True, timeout=None):
        self.calls.append([serial, *args])
        if len(args) >= 2 and args[0] == "install":
            code, out, err = self.install_result
            return types.SimpleNamespace(returncode=code,
                                         stdout=out.encode("utf-8"),
                                         stderr=err.encode("utf-8"))
        if len(args) >= 2 and args[0] == "shell" and args[1] == "dumpsys":
            index = min(self._dumpsys_index, len(self.dumpsys_outputs) - 1)
            self._dumpsys_index += 1
            return types.SimpleNamespace(returncode=0,
                                         stdout=self.dumpsys_outputs[index].encode("utf-8"),
                                         stderr=b"")
        return types.SimpleNamespace(returncode=0, stdout=b"", stderr=b"")

    @property
    def install_calls(self):
        return [call for call in self.calls if len(call) >= 2 and call[1] == "install"]


def _make_apk() -> str:
    handle = tempfile.NamedTemporaryFile(prefix="inputsharex-", suffix=".apk", delete=False)
    handle.write(b"fake apk")
    handle.close()
    return handle.name


def test_parse_dumpsys_installed():
    installed, version = androidapp.parse_dumpsys(INSTALLED_TEXT)
    assert installed is True
    assert version == "0.2.2", version


def test_parse_dumpsys_missing():
    assert androidapp.parse_dumpsys(MISSING_TEXT) == (False, "")
    assert androidapp.parse_dumpsys("") == (False, "")


def test_installed_version_uses_dumpsys_command():
    adb = FakeAdb([INSTALLED_TEXT])
    installed, version = androidapp.installed_version(adb, "192.168.1.5:5555")
    assert installed is True
    assert version == "0.2.2"
    assert adb.calls == [["192.168.1.5:5555", "shell", "dumpsys", "package", PACKAGE]], adb.calls


def test_ensure_installed_already_present_does_nothing():
    adb = FakeAdb([INSTALLED_TEXT])
    logs = []
    result = androidapp.ensure_installed(adb, "s", apk_path="/nonexistent.apk", log=logs.append)
    assert result is True
    assert adb.install_calls == []
    assert any("已安装" in line and "0.2.2" in line for line in logs), logs


def test_ensure_installed_missing_installs_and_launches():
    apk = _make_apk()
    try:
        adb = FakeAdb([MISSING_TEXT, INSTALLED_TEXT])
        logs = []
        result = androidapp.ensure_installed(adb, "s", apk_path=apk, log=logs.append)
        assert result is True, logs
        install_calls = adb.install_calls
        assert len(install_calls) == 1, adb.calls
        assert install_calls[0] == ["s", "install", "-r", apk], install_calls[0]
        launch_calls = [
            c for c in adb.calls
            if c[1:] == ["shell", "am", "start", "--activity-clear-task", "-n", MAIN_ACTIVITY,
                         "--ez", androidapp.EXTRA_CONNECT, "true"]
        ]
        assert len(launch_calls) == 1, adb.calls
        assert any("自动安装" in line for line in logs), logs
        assert any("已自动安装" in line for line in logs), logs
    finally:
        os.unlink(apk)


def test_launch_uses_connect_extra_and_logs():
    adb = FakeAdb([INSTALLED_TEXT])
    logs = []
    result = androidapp.launch(adb, "192.168.1.5:5555", log=logs.append)
    assert result is True
    assert adb.calls == [[
        "192.168.1.5:5555", "shell", "am", "start", "--activity-clear-task",
        "-n", MAIN_ACTIVITY,
        "--ez", androidapp.EXTRA_CONNECT, "true",
    ]], adb.calls
    assert androidapp.EXTRA_CONNECT == "inputsharex_connect"
    assert any("自动打开" in line for line in logs), logs


def test_launch_reports_am_start_failure():
    class FailingAdb(FakeAdb):
        def run_for(self, serial, *args, check=True, timeout=None):
            self.calls.append([serial, *args])
            return types.SimpleNamespace(returncode=1, stdout=b"",
                                         stderr=b"Error: Activity not started")

    adb = FailingAdb([INSTALLED_TEXT])
    logs = []
    assert androidapp.launch(adb, "s", log=logs.append) is False
    assert any("失败" in line for line in logs), logs


def test_ensure_installed_install_failure_reports_reason():
    apk = _make_apk()
    try:
        adb = FakeAdb([MISSING_TEXT],
                      install_result=(1, "", "Failure [INSTALL_FAILED_INSUFFICIENT_STORAGE]"))
        logs = []
        result = androidapp.ensure_installed(adb, "s", apk_path=apk, log=logs.append)
        assert result is False
        assert any("失败" in line and "INSTALL_FAILED" in line for line in logs), logs
    finally:
        os.unlink(apk)


def test_ensure_installed_missing_apk_reports():
    adb = FakeAdb([MISSING_TEXT])
    logs = []
    result = androidapp.ensure_installed(adb, "s", apk_path="Z:/nope/inputsharex-android.apk",
                                        log=logs.append)
    assert result is False
    assert adb.install_calls == []
    assert any("不存在" in line for line in logs), logs


def test_default_apk_path_points_at_vendor_apk():
    try:
        path = androidapp.default_apk_path()
    except FileNotFoundError:
        return  # 源码快照里没有 vendor APK 时跳过
    assert path.endswith(androidapp.APK_NAME), path
    assert os.path.isfile(path), path
