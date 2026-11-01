"""二维码配对的纯逻辑测试：不启动 adb、不需要真实设备。"""
from __future__ import annotations

from inputsharex import pairing


HEADER = "List of discovered mdns services\n"
PAIRING_LINE = "InputShareX-ABCD1234\t_adb-tls-pairing._tcp\t192.168.1.5:37777\n"
CONNECT_LINE = "adb-abc-xyz\t_adb-tls-connect._tcp\t192.168.1.5:41001\n"


class FakeAdb:
    """按调用顺序返回 mdns 页面；pair/connect 记录调用并模拟结果。"""

    def __init__(self, mdns_pages, pair_output="Successfully paired to 192.168.1.5 -- ok", devices_after_connect=True):
        self._pages = list(mdns_pages)
        self._pair_output = pair_output
        self.doc = devices_after_connect
        self.pair_calls: list[tuple] = []
        self.connect_calls: list[str] = []
        self._devices: dict[str, str] = {}

    def text(self, *args, check=True, **kwargs):
        if args[:2] == ("mdns", "services"):
            if len(self._pages) > 1:
                return self._pages.pop(0)
            return self._pages[0] if self._pages else HEADER
        if args[0] == "pair":
            self.pair_calls.append(args)
            return self._pair_output
        return ""

    def connect(self, address: str) -> str:
        self.connect_calls.append(address)
        if self.doc:
            self._devices[address] = "device"
        return f"connected to {address}\n"

    def devices(self):
        return list(self._devices.items())


def _session(adb, **kwargs):
    options = dict(poll=0.02, pairing_timeout=1.0, connect_timeout=0.5, connect_wait=0.5)
    options.update(kwargs)
    return pairing.QrPairing(adb, "InputShareX-ABCD1234", "pa55word", **options)


def test_build_payload():
    payload = pairing.build_payload("InputShareX-ABCD1234", "pa55word")
    assert payload == "WIFI:T:ADB;S:InputShareX-ABCD1234;P:pa55word;;", payload


def test_payload_escapes_wifi_special_chars():
    payload = pairing.build_payload('a;b,c:d"e\\f', 'p;w')
    assert payload == 'WIFI:T:ADB;S:a\\;b\\,c\\:d\\"e\\\\f;P:p\\;w;;', payload


def test_make_credentials():
    name, password = pairing.make_credentials()
    assert name.startswith("InputShareX-") and len(name) == len("InputShareX-") + 8, name
    assert 8 <= len(password) <= 32, password
    # 生成值不应包含需要转义的 Wi-Fi 特殊字符
    for value in (name, password):
        assert not any(ch in value for ch in '\\;,:"'), value
    assert pairing.make_credentials() != (name, password)


def test_parse_services():
    text = (
        HEADER
        + "someone\t_adb-tls-connect._tcp\t192.168.1.9:40000\n"
        + "not-adb\tsomething-else\t192.168.1.9:1\n"
        + PAIRING_LINE
        + "broken\t_adb-tls-pairing._tcp\tnot-an-address\n"
    )
    services = pairing.parse_services(text)
    assert services == [
        ("someone", "_adb-tls-connect._tcp", "192.168.1.9:40000"),
        ("InputShareX-ABCD1234", "_adb-tls-pairing._tcp", "192.168.1.5:37777"),
    ], services


def test_find_endpoint_matches_name_and_host():
    services = pairing.parse_services(HEADER + PAIRING_LINE + CONNECT_LINE)
    assert pairing.find_endpoint(services, pairing.PAIRING_SERVICE, name="InputShareX-ABCD1234") == "192.168.1.5:37777"
    assert pairing.find_endpoint(services, pairing.PAIRING_SERVICE, name="other") is None
    assert pairing.find_endpoint(services, pairing.CONNECT_SERVICE, host="192.168.1.5") == "192.168.1.5:41001"
    assert pairing.find_endpoint(services, pairing.CONNECT_SERVICE, host="10.0.0.1") is None
    assert pairing.address_host("192.168.1.5:37777") == "192.168.1.5"
    assert pairing.address_host("[fe80::1]:37777") == "fe80::1"


def test_success_flow_pairs_and_connects():
    adb = FakeAdb([HEADER, HEADER + PAIRING_LINE, HEADER + CONNECT_LINE])
    statuses: list[str] = []
    adb_session = _session(adb, on_status=statuses.append)
    result = adb_session.run()
    assert result.ok is True, result
    assert result.serial == "192.168.1.5:41001", result
    assert adb.pair_calls and adb.pair_calls[0][0] == "pair"
    assert adb.pair_calls[0][1] == "192.168.1.5:37777"
    assert adb.pair_calls[0][2] == "pa55word"
    assert adb.connect_calls == ["192.168.1.5:41001"]
    assert statuses and "配对" in "".join(statuses)


def test_pair_failure_reports_reason():
    adb = FakeAdb([HEADER, HEADER + PAIRING_LINE], pair_output="Failed: wrong password")
    result = _session(adb).run()
    assert result.ok is False
    assert "配对失败" in result.message and "wrong password" in result.message, result
    assert len(adb.pair_calls) >= 2  # 失败后按设计重试


def test_pairing_timeout():
    adb = FakeAdb([HEADER])
    result = _session(adb).run()
    assert result.ok is False
    assert "等待超时" in result.message, result
    assert adb.pair_calls == []


def test_cancel_before_waiting():
    adb = FakeAdb([HEADER + PAIRING_LINE])
    session = _session(adb)
    session.cancel()
    result = session.run()
    assert result.ok is False and "已取消" in result.message, result
    assert adb.pair_calls == []


def test_paired_but_no_connect_service():
    adb = FakeAdb([HEADER, HEADER + PAIRING_LINE, HEADER])
    result = _session(adb).run()
    assert result.ok is True
    assert result.serial == ""
    assert "连接端口" in result.message, result
    assert adb.pair_calls


def test_connect_service_appears_after_delay():
    # 配对成功后 connect 服务晚几轮才出现（手机上无线调试重新广播）
    adb = FakeAdb([HEADER, HEADER + PAIRING_LINE, HEADER, HEADER + CONNECT_LINE, HEADER])
    statuses: list[str] = []
    result = _session(adb, connect_timeout=3.0, on_status=statuses.append).run()
    assert result.ok is True, result
    assert result.serial == "192.168.1.5:41001", result
    assert adb.connect_calls == ["192.168.1.5:41001"]
    assert any("等待手机连接端口" in text for text in statuses), statuses


def test_connect_service_on_new_host_is_used():
    # 手机换网络接口后 connect 服务出现在另一个 IP 上
    other = "adb-new\t_adb-tls-connect._tcp\t192.168.1.6:41002\n"
    adb = FakeAdb([HEADER, HEADER + PAIRING_LINE, HEADER, HEADER + other, HEADER])
    result = _session(adb, connect_timeout=3.0).run()
    assert result.ok is True, result
    assert result.serial == "192.168.1.6:41002", result
    assert adb.connect_calls == ["192.168.1.6:41002"]


def test_candidate_connect_addresses_skips_unrelated_baseline():
    services = pairing.parse_services(
        HEADER
        + "old\t_adb-tls-connect._tcp\t10.0.0.9:40000\n"
        + "phone\t_adb-tls-connect._tcp\t192.168.1.6:41002\n"
        + CONNECT_LINE
    )
    candidates = pairing.candidate_connect_addresses(
        services, "192.168.1.5:37777", {"10.0.0.9:40000"}
    )
    assert candidates == ["192.168.1.5:41001", "192.168.1.6:41002"], candidates


def test_try_connect_reports_failure():
    adb = FakeAdb([HEADER], devices_after_connect=False)
    assert pairing.try_connect(adb, "192.168.1.5:41001", wait=0.1, poll=0.02) is False
    assert adb.connect_calls == ["192.168.1.5:41001"]
