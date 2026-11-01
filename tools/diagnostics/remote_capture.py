"""Bounded getevent capture that owns and reaps the Android process as well as adb."""
from __future__ import annotations

import atexit
from pathlib import Path
import re
import shlex
import subprocess
import uuid

from inputsharex.adb import adb_env


class RemoteEventCapture:
    def __init__(self, adb, serial: str, node: str, path: Path, seconds: float):
        if not re.fullmatch(r"/dev/input/event\d+", node):
            raise ValueError("Invalid input node")
        self.adb, self.serial, self.node = adb, serial, node
        self.pidfile = f"/data/local/tmp/inputsharex-capture-{uuid.uuid4().hex}.pid"
        self.output = path.open("wb")
        duration = max(1, int(seconds) + 1)
        # The watchdog targets getevent itself. Killing a local adb or a remote
        # timeout wrapper alone can leave getevent reparented to init.
        command = (
            f"getevent -lt {node} & capture_pid=$!; "
            f"echo $capture_pid > {shlex.quote(self.pidfile)}; "
            f"(sleep {duration}; kill $capture_pid 2>/dev/null) "
            "</dev/null >/dev/null 2>&1 & guard_pid=$!; "
            "wait $capture_pid; kill $guard_pid 2>/dev/null; "
            f"rm -f {shlex.quote(self.pidfile)}"
        )
        try:
            self.proc = subprocess.Popen(
                adb._base() + ["-s", serial, "shell", command],
                stdout=self.output, stderr=subprocess.DEVNULL, env=adb_env(),
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except BaseException:
            self.output.close()
            raise
        self.closed = False
        atexit.register(self.stop)

    def stop(self):
        if self.closed:
            return
        self.closed = True
        atexit.unregister(self.stop)
        try:
            result = self.adb.run_for(
                self.serial, "shell", f"cat {shlex.quote(self.pidfile)} 2>/dev/null",
                check=False, timeout=3,
            )
            pid = result.stdout.decode().strip()
            if pid.isdecimal():
                cmd = self.adb.run_for(
                    self.serial, "shell", f"cat /proc/{pid}/cmdline 2>/dev/null",
                    check=False, timeout=3,
                ).stdout.rstrip(b"\0").split(b"\0")
                if (len(cmd) == 3 and cmd[0].split(b"/")[-1] == b"getevent"
                        and cmd[1:] == [b"-lt", self.node.encode()]):
                    self.adb.run_for(self.serial, "shell", f"kill {pid}", check=False, timeout=3)
        except (OSError, subprocess.SubprocessError):
            # If the device disconnects, the independent remote watchdog still expires.
            pass
        finally:
            try:
                self.proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self.proc.terminate()
                try:
                    self.proc.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    self.proc.kill()
                    self.proc.wait(timeout=2)
            self.output.close()
