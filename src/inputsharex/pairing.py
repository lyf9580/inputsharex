"""二维码配对：电脑生成二维码，安卓无线调试扫码后反向广播配对服务。

与 Android Studio 的「使用二维码配对」流程一致：

1. 电脑生成随机服务名与配对密码，二维码内容为
   ``WIFI:T:ADB;S:<服务名>;P:<密码>;;``；
2. 安卓端在「开发者选项 → 无线调试 → 使用二维码配对设备」中扫码，
   随后以该服务名广播 ``_adb-tls-pairing._tcp``；
3. 电脑用 ``adb mdns services`` 发现该服务，执行
   ``adb pair <ip:端口> <密码>``；
4. 安卓端再广播 ``_adb-tls-connect._tcp``，电脑 ``adb connect`` 完成接入。

这里不依赖 Qt，便于单元测试；Qt 侧只负责显示二维码和进度。
"""
from __future__ import annotations

import secrets
import threading
import time
from dataclasses import dataclass
from typing import Callable

PAIRING_SERVICE = "_adb-tls-pairing._tcp"
CONNECT_SERVICE = "_adb-tls-connect._tcp"

DEFAULT_NAME_PREFIX = "InputShareX-"
DEFAULT_POLL = 1.0
DEFAULT_PAIRING_TIMEOUT = 180.0
DEFAULT_CONNECT_TIMEOUT = 120.0
DEFAULT_CONNECT_WAIT = 10.0
DEFAULT_WATCH_TIMEOUT = 300.0

# Wi-Fi 二维码规范要求转义这些字符
_WIFI_ESCAPE_CHARS = ("\\", ";", ",", ":", '"')


def escape_wifi_value(value: str) -> str:
    """按 Wi-Fi 二维码规范转义 ``\\ ; , : "``。"""
    return "".join(("\\" + ch) if ch in _WIFI_ESCAPE_CHARS else ch for ch in value)


def build_payload(name: str, password: str) -> str:
    """生成安卓无线调试可识别的二维码内容。"""
    return f"WIFI:T:ADB;S:{escape_wifi_value(name)};P:{escape_wifi_value(password)};;"


def make_credentials(prefix: str = DEFAULT_NAME_PREFIX) -> tuple[str, str]:
    """生成一次配对用的服务名与随机密码。"""
    name = prefix + secrets.token_hex(4).upper()
    password = secrets.token_urlsafe(12)[:12]
    return name, password


def parse_services(text: str) -> list[tuple[str, str, str]]:
    """解析 ``adb mdns services`` 输出为 ``[(实例名, 服务类型, 地址)]``。

    输出形如（首行是表头）::

        List of discovered mdns services
        adb-xxxx-pIdmUM	_adb-tls-connect._tcp	192.168.1.20:37123
    """
    services: list[tuple[str, str, str]] = []
    for line in (text or "").splitlines()[1:]:
        parts = line.strip().split("\t")
        if len(parts) < 3:
            continue
        name, service, addr = (part.strip() for part in parts[:3])
        if service in (PAIRING_SERVICE, CONNECT_SERVICE) and _valid_address(addr):
            services.append((name, service, addr))
    return services


def _valid_address(addr: str) -> bool:
    host, sep, port = addr.rpartition(":")
    return bool(sep and host and port.isdigit() and 1 <= int(port) <= 65535)


def address_host(addr: str) -> str:
    """取 ``ip:port`` 中的 IP（兼容 ``[ipv6]:port``）。"""
    return addr.rpartition(":")[0].strip("[]")


def _name_matches(instance: str, name: str) -> bool:
    # 设备一般原样广播服务名；少数系统会追加后缀，因此允许前缀匹配
    return instance == name or instance.startswith(name)


def find_endpoint(
    services: list[tuple[str, str, str]],
    service: str,
    *,
    name: str | None = None,
    host: str | None = None,
) -> str | None:
    """在已解析的 mDNS 服务里找一个地址，找不到返回 None。"""
    for instance, svc, addr in services:
        if svc != service:
            continue
        if name is not None and not _name_matches(instance, name):
            continue
        if host is not None and address_host(addr) != host:
            continue
        return addr
    return None


@dataclass
class PairResult:
    ok: bool
    serial: str = ""
    message: str = ""


def connect_endpoints(services: list[tuple[str, str, str]]) -> list[str]:
    """列出所有 ``_adb-tls-connect._tcp`` 的地址（保持顺序、去重）。"""
    result: list[str] = []
    for _instance, service, addr in services:
        if service == CONNECT_SERVICE and addr not in result:
            result.append(addr)
    return result


def candidate_connect_addresses(
    services: list[tuple[str, str, str]],
    pairing_addr: str,
    baseline: set[str] | None = None,
) -> list[str]:
    """列出扫码配对后值得尝试连接的地址。

    优先与配对服务同 IP 的 connect 服务；其次是配对期间新出现的 connect
    服务（手机换网络接口时 IP 可能不同）。baseline 里早先已存在的其它设备
    服务只做同 IP 匹配，避免把电脑上原本连着的设备误当成本次配对目标。
    """
    host = address_host(pairing_addr)
    known = baseline or set()
    ordered = connect_endpoints(services)
    same_host = [addr for addr in ordered if address_host(addr) == host]
    fresh = [addr for addr in ordered if addr not in known and addr not in same_host]
    return same_host + fresh


def try_connect(adb, addr: str, *, wait: float = 3.0, poll: float = 0.25) -> bool:
    """执行 ``adb connect`` 并等待设备进入 device 状态。"""
    try:
        adb.connect(addr)
    except Exception:  # noqa: BLE001 - 端口刚变化时 adb connect 可能失败
        pass
    deadline = time.monotonic() + wait
    while time.monotonic() < deadline:
        try:
            if dict(adb.devices()).get(addr) == "device":
                return True
        except Exception:  # noqa: BLE001
            pass
        time.sleep(max(0.05, poll))
    return False


class QrPairing:
    """一次二维码配对会话：等待扫码 → 配对 → 发现连接端口 → 连接。"""

    def __init__(
        self,
        adb,
        name: str,
        password: str,
        *,
        poll: float = DEFAULT_POLL,
        pairing_timeout: float = DEFAULT_PAIRING_TIMEOUT,
        connect_timeout: float = DEFAULT_CONNECT_TIMEOUT,
        connect_wait: float = DEFAULT_CONNECT_WAIT,
        on_status: Callable[[str], None] | None = None,
    ):
        self.adb = adb
        self.name = name
        self.password = password
        self.poll = max(0.05, poll)
        self.pairing_timeout = pairing_timeout
        self.connect_timeout = connect_timeout
        self.connect_wait = connect_wait
        self.on_status = on_status
        self._cancel = threading.Event()
        # 配对成功后记录，供「后台继续等待连接」复用
        self.pairing_addr = ""
        self.baseline: set[str] = set()

    # ------------------------------------------------------------------
    def cancel(self) -> None:
        self._cancel.set()

    @property
    def cancelled(self) -> bool:
        return self._cancel.is_set()

    def _status(self, text: str) -> None:
        if self.on_status is not None:
            try:
                self.on_status(text)
            except Exception:  # noqa: BLE001 - 进度回调不能影响配对
                pass

    def _services(self) -> list[tuple[str, str, str]]:
        return parse_services(self.adb.text("mdns", "services", check=False))

    def _wait_endpoint(
        self,
        service: str,
        *,
        name: str | None = None,
        host: str | None = None,
        timeout: float,
    ) -> str | None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.cancelled:
                return None
            endpoint = find_endpoint(self._services(), service, name=name, host=host)
            if endpoint is not None:
                return endpoint
            # 不用 Event.wait，方便测试传入很小的 poll
            time.sleep(self.poll)
        return None

    def _wait_connect(self, pairing_addr: str, timeout: float,
                      baseline: set[str]) -> str | None:
        """等 connect 服务出现并真正连上；期间不断更新进度。

        手机扫码配对后，connect 服务可能要几十秒才广播出来，而且端口/网卡
        地址可能变化，所以不只做一次 host 匹配，而是持续尝试候选地址。
        """
        deadline = time.monotonic() + timeout
        tried: set[str] = set()
        last_status = 0.0
        while time.monotonic() < deadline:
            if self.cancelled:
                return None
            services = self._services()
            for addr in candidate_connect_addresses(services, pairing_addr, baseline):
                if addr in tried:
                    continue
                tried.add(addr)
                remaining = max(1.0, deadline - time.monotonic())
                if try_connect(self.adb, addr, wait=min(self.connect_wait, remaining)):
                    return addr
            now = time.monotonic()
            if now - last_status >= 2.0:
                elapsed = int(timeout - (deadline - now))
                self._status(f"配对成功，正在等待手机连接端口…（已等待 {elapsed} 秒）")
                last_status = now
            time.sleep(self.poll)
        return None

    # ------------------------------------------------------------------
    def run(self) -> PairResult:
        try:
            return self._run()
        except Exception as exc:  # noqa: BLE001 - 配对失败要把原因带回界面
            return PairResult(False, "", f"二维码配对失败：{exc}")

    def _run(self) -> PairResult:
        if self.cancelled:
            return PairResult(False, "", "已取消配对。")
        self._status("正在等待安卓端扫描二维码…")
        pairing_addr = self._wait_endpoint(
            PAIRING_SERVICE, name=self.name, timeout=self.pairing_timeout
        )
        if pairing_addr is None:
            if self.cancelled:
                return PairResult(False, "", "已取消配对。")
            return PairResult(
                False,
                "",
                "等待超时：没有发现安卓端的配对请求。请确认安卓端和电脑在同一 Wi-Fi，"
                "并在「无线调试 → 使用二维码配对设备」中重新扫码。",
            )
        if self.cancelled:
            return PairResult(False, "", "已取消配对。")
        self._status("已发现设备，正在配对…")
        ok, detail = self._pair(pairing_addr)
        if not ok:
            return PairResult(False, "", f"配对失败：{detail}")
        host = address_host(pairing_addr)
        self.pairing_addr = pairing_addr
        self.baseline = set(connect_endpoints(self._services()))
        self._status("配对成功，正在等待手机连接端口…")
        connect_addr = self._wait_connect(pairing_addr, self.connect_timeout, self.baseline)
        if connect_addr is None:
            if self.cancelled:
                return PairResult(True, "", "配对成功（等待连接已取消）。")
            return PairResult(
                True, "",
                "配对成功，但电脑还没发现手机的连接端口。电脑会在后台继续等待"
                "（约 5 分钟）；也可以在设备页手动连接。若一直等不到，请在手机上"
                "重新打开「无线调试」后再扫一次二维码。",
            )
        if self.cancelled:
            return PairResult(True, "", "配对成功（连接已取消）。")
        self._status("正在连接…")
        # _wait_connect 已经执行 adb connect 并确认设备进入 device 状态
        if self._wait_device(connect_addr):
            return PairResult(True, connect_addr, f"配对成功，已连接 {connect_addr}。")
        return PairResult(
            True, connect_addr, "配对成功，设备正在连接；请稍后刷新设备列表。"
        )

    def _pair(self, addr: str) -> tuple[bool, str]:
        detail = ""
        for attempt in range(3):
            if self.cancelled:
                return False, "已取消"
            text = self.adb.text("pair", addr, self.password, check=False)
            detail = (text or "").strip()
            if "Successfully paired" in detail:
                return True, detail
            # 设备广播/端口可能刚就绪，稍等重试
            if attempt < 2:
                time.sleep(1.0)
        return False, detail or "adb pair 没有返回成功信息"

    def _wait_device(self, addr: str) -> bool:
        deadline = time.monotonic() + self.connect_wait
        while time.monotonic() < deadline:
            if self.cancelled:
                return False
            try:
                if dict(self.adb.devices()).get(addr) == "device":
                    return True
            except Exception:  # noqa: BLE001
                pass
            time.sleep(0.25)
        return False
