"""Saved connection profiles and discovery, separated by actual transport."""
import ipaddress
import re

TRANSPORTS = {"tls": "Wi-Fi 加密直连", "adb": "无线 ADB", "usb": "有线 USB"}


def profile_key(serial, transport):
    return f"{transport}|{serial}"


def validate_profile(name, serial, transport):
    name, serial = name.strip(), serial.strip()
    if not name:
        raise ValueError("请输入设备名称")
    if transport not in TRANSPORTS:
        raise ValueError("请选择连接方式")
    if transport == "usb":
        if not re.fullmatch(r"[\w.-]+", serial) or "_adb-tls" in serial:
            raise ValueError("请输入有线设备序列号")
    else:
        try:
            host, port = serial.rsplit(":", 1)
            ipaddress.ip_address(host.strip("[]"))
            if not 1 <= int(port) <= 65535:
                raise ValueError()
        except (ValueError, TypeError):
            raise ValueError("请输入无线调试地址，例如 192.168.1.20:37123") from None
    return {"id": profile_key(serial, transport), "name": name, "serial": serial, "transport": transport}


def build_device_entries(discovered, saved, hidden):
    entries = {}
    for device in discovered:
        serial = device["serial"]
        for transport in (("usb",) if device.get("wired") else ("tls", "adb")):
            key = profile_key(serial, transport)
            if key not in hidden:
                entries[key] = {**device, "id": key, "transport": transport}
    for profile in saved:
        key = profile_key(profile["serial"], profile["transport"])
        entries[key] = {
            "state": "offline", "addr": profile["serial"], "wired": profile["transport"] == "usb",
            **entries.get(key, {}), **profile, "id": key, "saved": True,
        }
    return sorted(entries.values(), key=lambda d: (
        list(TRANSPORTS).index(d["transport"]), d["state"] != "device", d["name"], d["serial"]))
