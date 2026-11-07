"""Lifecycle-bound Android cursor verifier; only samples while the PC owns the mouse."""
import os
import subprocess
import threading
import time

from .adb import adb_env


def _dbg(message: str) -> None:
    path = os.environ.get("INPUTSHAREX_DEBUG_LOG")
    if not path:
        return
    try:
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(time.strftime("%H:%M:%S ") + "pointer: " + message + "\n")
    except OSError:
        pass


class PointerMonitor:
    def __init__(self, adb, serial, jar_path, on_edge, on_error=None):
        self.adb, self.serial, self.jar_path = adb, serial, jar_path
        self.on_edge, self.on_error = on_edge, on_error
        self.proc = None
        self.pid = None
        self.thread = None
        self.lock = threading.Lock()
        self.desired = "0\n"
        self.available = False
        self.diagnostic = []
        self._position_sample = None

    def start(self):
        remote = "/data/local/tmp/inputsharex-pointer.jar"
        self.adb.push(self.jar_path, remote)
        self.proc = subprocess.Popen(
            self.adb._base() + ["-s", self.serial, "shell", "-T",
                f"CLASSPATH={remote} app_process / InputShareXPointerProbe"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            env=adb_env(), creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        self.thread = threading.Thread(target=self._read, name="pointer-monitor", daemon=True)
        self.thread.start()

    def _read(self):
        proc = self.proc
        try:
            for raw in proc.stdout:
                parts = raw.decode("utf-8", "replace").strip().split()
                if len(parts) == 2 and parts[0] == "READY":
                    self.pid = int(parts[1])
                    self.available = True
                    _dbg(f"READY pid={self.pid}")
                    self._write()
                elif len(parts) == 7 and parts[0] == "POSITION":
                    self._position_sample = (
                        int(parts[1]), time.monotonic(), tuple(map(float, parts[3:])),
                    )
                    _dbg('POSITION ' + ' '.join(parts[1:]))
                elif len(parts) == 7 and parts[0] == "EDGE":
                    _dbg("EDGE " + " ".join(parts[1:]))
                    self.on_edge({"generation": int(parts[1]), "placement": int(parts[2]),
                                  "x": float(parts[3]), "y": float(parts[4]),
                                  "max_x": float(parts[5]), "max_y": float(parts[6])})
                elif len(self.diagnostic) < 8:
                    self.diagnostic.append(raw.decode("utf-8", "replace").strip())
        except (OSError, ValueError):
            pass
        finally:
            self.available = False
            self._position_sample = None
            if self.on_error:
                self.on_error()

    def _write(self):
        with self.lock:
            if self.proc and self.proc.poll() is None and self.available:
                try:
                    self.proc.stdin.write(self.desired.encode())
                    self.proc.stdin.flush()
                except (OSError, ValueError):
                    pass

    def latest_position(self, max_age: float = 1.0, *, generation: int, since: float = 0):
        sample = self._position_sample
        if sample is None:
            return None
        sample_generation, timestamp, pos = sample
        if sample_generation != generation or timestamp < since or time.monotonic() - timestamp > max_age:
            return None
        return pos

    def set_enabled(self, enabled, generation, placement, *, aligning=False):
        mode = " align" if aligning else ""
        self.desired = f"1 {int(generation)} {int(placement)}{mode}\n" if enabled else "0\n"
        _dbg(f"set_enabled desired={self.desired.strip()} available={self.available} pid={self.pid}")
        self._write()

    def set_interval(self, interval_ms: int) -> None:
        """Temporarily change the probe sampling interval (milliseconds)."""
        line = "i %d\n" % int(interval_ms)
        with self.lock:
            if self.proc and self.proc.poll() is None and self.available:
                try:
                    self.proc.stdin.write(line.encode())
                    self.proc.stdin.flush()
                except (OSError, ValueError):
                    pass

    def stop(self):
        proc = self.proc
        if proc is None:
            return
        with self.lock:
            try:
                proc.stdin.close()
            except (OSError, ValueError):
                pass
        try:
            proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            if self.pid:
                try:
                    args = self.adb.run_for(self.serial, "shell", f"cat /proc/{self.pid}/cmdline 2>/dev/null", check=False, timeout=3).stdout.split(b"\0")
                    if args[:3] == [b"app_process", b"/", b"InputShareXPointerProbe"]:
                        self.adb.run_for(self.serial, "shell", f"kill {self.pid}", check=False, timeout=3)
                except (OSError, subprocess.SubprocessError):
                    pass
            proc.terminate()
            try:
                proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=2)
        if self.thread:
            self.thread.join(timeout=2)
        proc.stdout.close()
        self.proc = None
