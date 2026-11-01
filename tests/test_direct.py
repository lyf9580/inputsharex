import ssl

from cryptography import x509
from cryptography.hazmat.primitives.serialization import pkcs12

from inputsharex.direct import make_credentials
from inputsharex.server import ScrcpyServer


def test_session_certificate_is_pinned_and_checks_ip():
    context, bundle, password = make_credentials("192.168.1.2")
    key, certificate, _ = pkcs12.load_key_and_certificates(bundle, password.encode())
    assert key is not None
    assert context.verify_mode == ssl.CERT_REQUIRED
    assert context.check_hostname
    assert context.minimum_version >= ssl.TLSVersion.TLSv1_2
    assert context.cert_store_stats()["x509"] == 1
    addresses = certificate.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
    assert str(addresses.get_values_for_type(x509.IPAddress)[0]) == "192.168.1.2"


def test_each_session_uses_independent_credentials():
    _, one, password_one = make_credentials("192.168.1.2")
    _, two, password_two = make_credentials("192.168.1.2")
    assert one != two and password_one != password_two


def test_usb_does_not_enable_wireless_relay():
    class Adb:
        pass
    server = ScrcpyServer(Adb(), "local-serial", jar_path="server.jar", direct=True)
    assert not server.direct
    assert server.transport == "adb"
