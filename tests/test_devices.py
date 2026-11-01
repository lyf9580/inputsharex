from inputsharex.devices import build_device_entries, validate_profile


def wireless():
    return {"serial": "192.168.1.2:37123", "name": "Tablet", "state": "device", "wired": False, "addr": "192.168.1.2:37123"}


def test_wireless_endpoint_has_two_independent_transport_profiles():
    entries = build_device_entries([wireless()], [], [])
    assert {d["transport"] for d in entries} == {"tls", "adb"}
    assert len({d["id"] for d in entries}) == 2


def test_deleting_one_transport_does_not_remove_the_other_or_reappear():
    entries = build_device_entries([wireless()], [], ["tls|192.168.1.2:37123"])
    assert [d["transport"] for d in entries] == ["adb"]


def test_saved_offline_device_remains_editable_and_keeps_alias():
    profile = validate_profile("My tablet", "192.168.1.2:37123", "tls")
    entry = build_device_entries([], [profile], [])[0]
    assert entry["name"] == "My tablet" and entry["state"] == "offline"
    online = build_device_entries([wireless()], [profile], [])
    assert next(d for d in online if d["transport"] == "tls")["name"] == "My tablet"


def test_invalid_profile_does_not_accept_an_unrelated_device_or_bad_endpoint():
    for serial, kind in [("192.168.1.2:99999", "tls"), ("tablet", "adb"), ("1.2.3.4:3", "usb")]:
        try:
            validate_profile("Tablet", serial, kind)
        except ValueError:
            pass
        else:
            raise AssertionError((serial, kind))
