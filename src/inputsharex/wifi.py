"""Restore background-capable Wi-Fi locks on Android 14+ devices."""
from __future__ import annotations

from .adb import Adb

KEY = "high_perf_lock_deprecated"


def enable_background_wifi_lock(adb: Adb, serial: str) -> bool:
    """Enable legacy high-performance locks; callers must reacquire existing locks.

    This device-wide compatibility flag survives app restarts. Unsupported or
    permission-denied devices return False without interrupting input sharing.
    Restore the Android default with: device_config put wifi high_perf_lock_deprecated true
    """
    def shell(command: str) -> str | None:
        result = adb.run_for(serial, "shell", command, check=False, timeout=6)
        if result.returncode:
            return None
        return result.stdout.decode("utf-8", "replace").strip()

    # Do not create an unknown flag on older/unsupported systems.
    current = shell(f"device_config get wifi {KEY}")
    if current == "false":
        return True
    if current != "true":
        return False
    if shell(f"device_config put wifi {KEY} false") is None:
        return False
    return shell(f"device_config get wifi {KEY}") == "false"
