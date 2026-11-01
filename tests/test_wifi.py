import subprocess

from inputsharex.wifi import enable_background_wifi_lock


class FakeAdb:
    def __init__(self, replies):
        self.replies = iter(replies)
        self.commands = []

    def run_for(self, serial, *args, **kwargs):
        self.commands.append((serial, args))
        code, output = next(self.replies)
        return subprocess.CompletedProcess(args, code, output.encode(), b"")


def test_enables_and_verifies_flag_on_selected_device():
    adb = FakeAdb([(0, "true\n"), (0, ""), (0, "false\n")])
    assert enable_background_wifi_lock(adb, "tablet:1234")
    assert all(serial == "tablet:1234" for serial, _ in adb.commands)
    assert "put wifi high_perf_lock_deprecated false" in adb.commands[1][1][1]


def test_existing_fix_does_not_write_again():
    adb = FakeAdb([(0, "false\n")])
    assert enable_background_wifi_lock(adb, "tablet")
    assert len(adb.commands) == 1


def test_unsupported_device_is_not_modified():
    adb = FakeAdb([(0, "null\n")])
    assert not enable_background_wifi_lock(adb, "tablet")
    assert len(adb.commands) == 1


def test_permission_failure_is_reported_as_unavailable():
    adb = FakeAdb([(0, "true\n"), (1, "")])
    assert not enable_background_wifi_lock(adb, "tablet")


def test_silent_write_failure_is_not_reported_as_success():
    adb = FakeAdb([(0, "true\n"), (0, ""), (0, "true\n")])
    assert not enable_background_wifi_lock(adb, "tablet")
