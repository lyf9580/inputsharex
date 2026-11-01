"""adb 进程封装。

默认使用已经和无线调试配对过的那台 adb server。原应用把 daemon 起在 5038（避开 5037），
用 `adb -P 5038` 就能直接复用它的配对关系，避免重新配对。
"""
from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
CACHE_DIR = REPO_ROOT / ".cache"

# 这些 adb 子命令不接受 -s（否则会报 more than one device）
_NO_SERIAL_COMMANDS = {
    "connect", "disconnect", "devices", "devices-l", "mdns", "start-server",
    "kill-server", "pair", "version", "help", "reconnect",
}

DEFAULT_CANDIDATES = [
    str(REPO_ROOT / "vendor" / "platform-tools" / "adb.exe"),
    r"C:\platform-tools\adb.exe",
]


def adb_env() -> dict[str, str]:
    """adb 需要把日志写进 TEMP；本机安全软件会拦截它写系统 TEMP，
    所以统一把 adb 的 TEMP/TMP 指到仓库内的可写目录。"""
    env = dict(os.environ)
    tmp = CACHE_DIR / "tmp"
    try:
        tmp.mkdir(parents=True, exist_ok=True)
    except OSError:
        tmp = Path(tempfile.mkdtemp(prefix="inputsharex-"))
    env["TEMP"] = str(tmp)
    env["TMP"] = str(tmp)
    return env


def find_adb(explicit: str | None = None) -> str:
    if explicit:
        return explicit
    env = os.environ.get("INPUTSHAREX_ADB")
    if env and os.path.isfile(env):
        return env
    for candidate in DEFAULT_CANDIDATES:
        if os.path.isfile(candidate):
            return candidate
    found = shutil.which("adb")
    if found:
        return found
    raise FileNotFoundError(
        "找不到 adb.exe；用 --adb 指定路径，或把 platform-tools 加到 PATH"
    )


class AdbError(RuntimeError):
    pass


class Adb:
    def __init__(self, adb_path: str | None = None, port: int | None = 5038,
                 serial: str | None = None, timeout: int = 30):
        self.path = find_adb(adb_path)
        self.port = port
        self.serial = serial
        self.timeout = timeout

    # -- low level -------------------------------------------------
    def _base(self) -> list[str]:
        cmd = [self.path]
        if self.port:
            cmd += ["-P", str(self.port)]
        return cmd

    def _cmd(self, args: tuple[str, ...] | list[str]) -> list[str]:
        cmd = self._base()
        first = args[0] if args else ""
        if self.serial and first not in _NO_SERIAL_COMMANDS:
            cmd += ["-s", self.serial]
        return cmd + list(args)

    def run(self, *args: str, check: bool = True, timeout: int | None = None,
            binary: bool = False) -> subprocess.CompletedProcess:
        cmd = self._cmd(args)
        proc = subprocess.run(
            cmd,
            capture_output=True,
            timeout=timeout or self.timeout,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            env=adb_env(),
        )
        if check and proc.returncode != 0:
            err = proc.stderr.decode("utf-8", "replace") if not binary else ""
            raise AdbError(f"adb {' '.join(args)} 失败 ({proc.returncode}): {err.strip()}")
        return proc

    def text(self, *args: str, **kwargs) -> str:
        proc = self.run(*args, **kwargs)
        return proc.stdout.decode("utf-8", "replace")

    def run_for(self, serial: str, *args: str, check: bool = True,
                timeout: int | None = None) -> subprocess.CompletedProcess:
        """针对指定 serial 执行（不影响 self.serial），用于探测其它设备信息。"""
        cmd = self._base() + ["-s", serial] + list(args)
        proc = subprocess.run(
            cmd,
            capture_output=True,
            timeout=timeout or self.timeout,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            env=adb_env(),
        )
        if check and proc.returncode != 0:
            err = proc.stderr.decode("utf-8", "replace")
            raise AdbError(f"adb -s {serial} {' '.join(args)} 失败 ({proc.returncode}): {err.strip()}")
        return proc

    def model_name(self, serial: str, timeout: int | None = None) -> str | None:
        """读设备显示名（优先 ro.product.marketname，其次 ro.product.model），失败返回 None。"""
        try:
            out = self.run_for(serial, "shell", "getprop", "ro.product.marketname",
                               ";", "getprop", "ro.product.model",
                               timeout=timeout or 6).stdout.decode("utf-8", "replace")
        except (AdbError, OSError, subprocess.SubprocessError):
            return None
        for line in out.splitlines():
            name = line.strip()
            if name:
                return name
        return None

    # -- device ----------------------------------------------------
    def devices(self) -> list[tuple[str, str]]:
        out = self.text("devices")
        result = []
        for line in out.splitlines()[1:]:
            line = line.strip()
            if not line or "\t" not in line:
                continue
            serial, state = line.split("\t", 1)
            if "._adb-tls-connect._tcp" in serial or serial.endswith("._tcp"):
                # mDNS 伪设备（adb devices 会把它也列出来），不能当串号用
                continue
            result.append((serial, state.strip()))
        return result

    def start_server(self) -> None:
        self.run("start-server")

    def mdns_services(self) -> list[tuple[str, str]]:
        """返回 [(服务名, 地址)]，地址形如 192.168.1.2:43175。"""
        out = self.text("mdns", "services", check=False)
        result = []
        for line in out.splitlines()[1:]:
            parts = line.strip().split("\t")
            if len(parts) < 3:
                continue
            name, service, addr = parts[0], parts[1], parts[2].strip()
            if service == "_adb-tls-connect._tcp" and ":" in addr:
                result.append((name, addr))
        return result

    def discover(self, ip_hint: str | None = None, timeout: float = 25.0) -> str:
        """通过 mDNS 自动找到无线调试端口并连接，返回可用的 serial。"""
        self.start_server()
        deadline = time.time() + timeout
        last_error = ""
        while time.time() < deadline:
            addresses = [addr for _, addr in self.mdns_services()
                         if not ip_hint or addr.startswith(ip_hint)]
            for addr in addresses:
                if dict(self.devices()).get(addr) == "device":
                    return addr
                try:
                    last_error = self.connect(addr)
                except AdbError as exc:  # 端口刚变时常见，下一轮重试
                    last_error = str(exc)
                for _ in range(8):
                    if dict(self.devices()).get(addr) == "device":
                        return addr
                    time.sleep(0.25)
                time.sleep(0.5)
            time.sleep(1.0)
        raise AdbError(
            "没有发现可连接的无线调试设备：确认平板已开“无线调试”、"
            "且这台电脑之前和它配对过"
            + (f"；最近错误：{last_error}" if last_error else "")
        )

    def connect(self, address: str) -> str:
        return self.text("connect", address).strip()

    def resolve_serial(self, serial: str | None = None, connect: bool = True) -> str:
        """返回可用的 serial；必要时尝试 adb connect。"""
        target = serial or self.serial
        devices = dict(self.devices())
        if target and devices.get(target) == "device":
            return target
        if target and connect and ":" in target:
            self.connect(target)
            for _ in range(20):
                devices = dict(self.devices())
                if devices.get(target) == "device":
                    return target
                time.sleep(0.25)
        online = [s for s, st in devices.items() if st == "device"]
        if len(online) == 1:
            return online[0]
        if not online:
            raise AdbError("没有处于 device 状态的安卓设备；请先用原应用或 adb connect 连上")
        raise AdbError(f"有多台设备 {online}，请用 --serial 指定")

    # -- plumbing --------------------------------------------------
    def forward(self, local_port: int, remote: str) -> None:
        self.remove_forward(local_port)
        self.run("forward", f"tcp:{local_port}", remote)

    def remove_forward(self, local_port: int) -> None:
        self.run("forward", "--remove", f"tcp:{local_port}", check=False)

    def reverse(self, remote_port: int, local_port: int) -> None:
        """设备端口 → 本机端口（安卓 App 连 127.0.0.1:remote_port 就到我们）。"""
        self.run("reverse", "--remove", f"tcp:{remote_port}", check=False)
        self.run("reverse", f"tcp:{remote_port}", f"tcp:{local_port}")

    def reverse_remove(self, remote_port: int) -> None:
        self.run("reverse", "--remove", f"tcp:{remote_port}", check=False)

    def push(self, local_path: str, remote_path: str) -> None:
        self.run("push", local_path, remote_path)

    def shell(self, command: str, timeout: int = 20, check: bool = True) -> str:
        return self.text("shell", command, timeout=timeout, check=check)

    def shell_popen(self, command: str) -> subprocess.Popen:
        cmd = self._cmd(["shell", command])
        return subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            env=adb_env(),
        )
