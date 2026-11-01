"""scrcpy-server 生命周期：推送 jar、端口转发、启动、握手。

只开控制通道（video=false audio=false），这是低延迟的关键：
没有视频编码/解码，也没有 UI 进程，鼠标事件直接进 UHID。
"""
from __future__ import annotations

import os
import socket
import time
import subprocess
import uuid

from .adb import Adb, AdbError, adb_env

SERVER_VERSION = "3.3.3"
SERVER_CLASS = "com.genymobile.scrcpy.Server"
REMOTE_JAR = "/data/local/tmp/inputsharex-server.jar"
DEFAULT_LOCAL_PORT = 27183


class ServerError(RuntimeError):
    pass


def default_jar_path() -> str:
    here = os.path.dirname(os.path.abspath(__file__))
    for rel in (
        os.path.join(here, "..", "..", "vendor", "scrcpy-server.jar"),
        os.path.join(here, "..", "..", "..", "vendor", "scrcpy-server.jar"),
    ):
        candidate = os.path.abspath(rel)
        if os.path.isfile(candidate):
            return candidate
    raise FileNotFoundError("vendor/scrcpy-server.jar 不存在（见 README）")


def build_command(remote_jar: str = REMOTE_JAR, log_level: str = "info") -> str:
    options = [
        SERVER_VERSION,
        "tunnel_forward=true",
        "video=false",
        "audio=false",
        "control=true",
        "send_dummy_byte=true",
        "cleanup=false",
        f"log_level={log_level}",
    ]
    return (
        f"CLASSPATH={remote_jar} app_process / {SERVER_CLASS} " + " ".join(options)
    )


class ScrcpyServer:
    """管理一次服务端运行。用法：with ScrcpyServer(adb, serial) as server: server.sock"""

    def __init__(self, adb: Adb, serial: str, local_port: int = DEFAULT_LOCAL_PORT,
                 jar_path: str | None = None, log_level: str = "info", direct: bool = False):
        self.adb = adb
        self.serial = serial
        adb.serial = serial  # 后续所有 adb 命令都带上 -s，避免多设备/多 serial 歧义
        self.local_port = local_port
        self.jar_path = jar_path or default_jar_path()
        self.log_level = log_level
        self.proc: subprocess.Popen | None = None
        self.sock: socket.socket | None = None
        self.direct = direct and ":" in serial
        self.transport = "adb"
        self.direct_error = ""
        self.relay = None
        self._pidfile = f"/data/local/tmp/inputsharex-server-{uuid.uuid4().hex}.pid"

    # -- helpers ---------------------------------------------------
    def _kill_stale(self) -> None:
        # Own the app_process PID explicitly: matching CLASSPATH with pkill only
        # kills the shell wrapper and can leave its Java child running.
        try:
            pid = self.adb.shell(f"cat {self._pidfile} 2>/dev/null", timeout=3, check=False).strip()
            if pid.isdecimal():
                args = self.adb.run_for(self.serial, "shell", f"cat /proc/{pid}/cmdline 2>/dev/null", check=False, timeout=3).stdout
                if args.split(b"\0")[:3] == [b"app_process", b"/", SERVER_CLASS.encode()]:
                    self.adb.shell(f"kill {pid}", timeout=3, check=False)
            self.adb.shell(f"rm -f {self._pidfile}", timeout=3, check=False)
        except (AdbError, OSError, subprocess.SubprocessError):
            pass

    def _push(self) -> None:
        self.adb.push(self.jar_path, REMOTE_JAR)

    def _start(self) -> None:
        command = build_command(REMOTE_JAR, self.log_level).replace(" app_process ", " exec app_process ", 1)
        command = f"echo $$ > {self._pidfile}; {command}"
        cmd = self.adb._base() + ["-s", self.serial, "shell", command]
        self.proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            env=adb_env(),
        )

    def _connect(self, timeout: float = 10.0) -> socket.socket:
        """连上控制端口并完成 dummy 握手。

        注意：adb forward 的监听口在服务端 app_process 完全起来之前就可能 accept，
        此时连上会立刻读到 EOF，因此每次都要验证握手字节，失败就重试。
        """
        deadline = time.time() + timeout
        last_error: Exception | None = None
        while time.time() < deadline:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(1.0)
            try:
                sock.connect(("127.0.0.1", self.local_port))
                dummy = sock.recv(1)
                if dummy == b"\x00":
                    sock.settimeout(None)
                    return sock
                last_error = ServerError(f"握手收到 {dummy!r}（服务端未就绪）")
                sock.close()
            except OSError as exc:
                last_error = exc
                sock.close()
            time.sleep(0.1)
        raise ServerError(f"连不上 scrcpy-server 控制端口 {self.local_port}: {last_error}")

    def _read_device_meta(self, sock: socket.socket, consume_dummy: bool) -> None:
        """读掉控制通道开头的握手数据。

        video/audio 都关闭时，控制 socket 是服务端的"首个 socket"：
        服务端会先发 1 字节 dummy（send_dummy_byte）和 64 字节设备名，
        之后才是 CLIPBOARD/ACK/UHID_OUTPUT 等设备消息。不读掉的话，
        剪贴板接收线程会把设备名当成消息类型（例如 '2'）导致会话被关闭。
        """
        total = (1 if consume_dummy else 0) + 64
        sock.settimeout(5.0)
        buf = b""
        try:
            while len(buf) < total:
                chunk = sock.recv(total - len(buf))
                if not chunk:
                    break
                buf += chunk
        except OSError:
            pass
        finally:
            sock.settimeout(None)

    # -- public ----------------------------------------------------
    def start(self) -> socket.socket:
        self.transport = "adb"
        self._kill_stale()
        self._push()
        # 本机端口可能被上一次没退干净的 adb forward 占着（adb server 常驻，
        # forward 规则不会随客户端退出），所以被占就顺延端口。
        requested = self.local_port
        last_error: Exception | None = None
        for offset in range(11):
            self.local_port = requested + offset
            try:
                self.adb.forward(self.local_port, "localabstract:scrcpy")
                break
            except AdbError as exc:
                last_error = exc
        else:
            raise ServerError(
                f"本地端口 {requested}–{requested + 10} 都被占用，无法建立转发：{last_error}"
            )
        if self.local_port != requested:
            print(f"[*] 本地端口 {requested} 被占用，改用 {self.local_port}")
        self._start()
        if self.direct:
            from .direct import DirectControlRelay
            self.relay = DirectControlRelay(self.adb, self.serial,
                os.path.join(os.path.dirname(self.jar_path), "inputsharex-relay.jar"))
            try:
                self.sock = self.relay.start()
                self._read_device_meta(self.sock, consume_dummy=True)
                self.transport = "tls-direct"
                return self.sock
            except (OSError, RuntimeError, ValueError, ImportError, subprocess.SubprocessError) as exc:
                self.direct_error = str(exc)
                self.stop()
                self.direct = False
                return self.start()
        self.sock = self._connect()
        self._read_device_meta(self.sock, consume_dummy=False)
        return self.sock

    def stop(self) -> None:
        if self.sock is not None:
            try:
                self.sock.close()
            except OSError:
                pass
            self.sock = None
        if self.relay is not None:
            self.relay.stop()
            self.relay = None
        self._kill_stale()
        if self.proc is not None and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait(timeout=3)
        if self.proc is not None and self.proc.stdout is not None:
            self.proc.stdout.close()
        self.proc = None
        self.adb.remove_forward(self.local_port)

    def log_tail(self) -> str:
        if self.proc is None or self.proc.stdout is None:
            return ""
        try:
            return self.proc.stdout.read1(8192).decode("utf-8", "replace")
        except Exception:
            return ""

    def __enter__(self) -> "ScrcpyServer":
        self.start()
        return self

    def __exit__(self, *exc) -> None:
        self.stop()
