"""Per-session TLS control transport, bootstrapped over the existing adb connection."""
from __future__ import annotations

import base64
from datetime import datetime, timedelta, timezone
import ipaddress
from pathlib import Path
import queue
import secrets
import socket
import ssl
import subprocess
import threading

from .adb import Adb, adb_env


def make_credentials(host: str):
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.hazmat.primitives.serialization import pkcs12
    from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

    address = ipaddress.ip_address(host)
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "InputShareX session")])
    now = datetime.now(timezone.utc)
    certificate = (
        x509.CertificateBuilder().subject_name(name).issuer_name(name)
        .public_key(key.public_key()).serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=5)).not_valid_after(now + timedelta(days=2))
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .add_extension(x509.KeyUsage(True, False, True, False, False, True, False, None, None), critical=True)
        .add_extension(x509.SubjectAlternativeName([x509.IPAddress(address)]), critical=False)
        .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False)
        .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(key.public_key()), critical=False)
        .sign(key, hashes.SHA256())
    )
    password = secrets.token_hex(32)
    # Android's PKCS12 implementation supports this broadly; transport remains TLS.
    encryption = (serialization.PrivateFormat.PKCS12.encryption_builder()
                  .kdf_rounds(20000)
                  .key_cert_algorithm(pkcs12.PBES.PBESv1SHA1And3KeyTripleDESCBC)
                  .hmac_hash(hashes.SHA1()).build(password.encode()))
    bundle = pkcs12.serialize_key_and_certificates(b"inputsharex", key, certificate, None, encryption)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.load_verify_locations(cadata=certificate.public_bytes(serialization.Encoding.PEM).decode())
    return context, bundle, password


class DirectControlRelay:
    def __init__(self, adb: Adb, serial: str, jar_path: str):
        self.adb, self.serial, self.jar_path = adb, serial, jar_path
        self.proc = None
        self.sock = None
        self.remote_pid = None

    def start(self):
        host = str(ipaddress.ip_address(self.serial.rsplit(":", 1)[0].strip("[]")))
        if not Path(self.jar_path).is_file():
            raise FileNotFoundError("Encrypted control relay is not bundled")
        context, bundle, password = make_credentials(host)
        token = secrets.token_hex(32)
        remote = "/data/local/tmp/inputsharex-tls-relay.jar"
        self.adb.push(self.jar_path, remote)
        self.proc = subprocess.Popen(
            self.adb._base() + ["-s", self.serial, "shell", "-T",
                f"CLASSPATH={remote} app_process / InputShareXRelay {host}"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            env=adb_env(), creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        ready = queue.Queue()

        def read_output():
            for line in self.proc.stdout:
                if line.startswith(b"READY "):
                    ready.put(line.decode().split())
                    return
            ready.put(None)

        threading.Thread(target=read_output, name="relay-startup", daemon=True).start()
        self.proc.stdin.write(base64.b64encode(bundle) + b"\n" + password.encode() + b"\n" + token.encode() + b"\n")
        self.proc.stdin.close()
        try:
            result = ready.get(timeout=12)
        except queue.Empty as exc:
            raise RuntimeError("Encrypted relay startup timed out") from exc
        if not result or len(result) != 3:
            raise RuntimeError("Encrypted relay did not start")
        port, self.remote_pid = int(result[1]), int(result[2])
        raw = socket.create_connection((host, port), timeout=4)
        try:
            raw.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            self.sock = context.wrap_socket(raw, server_hostname=host)
            self.sock.sendall(token.encode())
            if self.sock.recv(1) != b"\0":
                raise RuntimeError("Encrypted control handshake failed")
            self.sock.settimeout(None)
            return self.sock
        except BaseException:
            raw.close()
            if self.sock is not None:
                self.sock.close()
            raise

    def stop(self):
        if self.sock is not None:
            try:
                self.sock.close()
            except OSError:
                pass
            self.sock = None
        if self.remote_pid is not None:
            pid, self.remote_pid = self.remote_pid, None
            try:
                command = self.adb.run_for(self.serial, "shell", f"cat /proc/{pid}/cmdline 2>/dev/null", check=False, timeout=3).stdout
                if command.split(b"\0")[:3] == [b"app_process", b"/", b"InputShareXRelay"]:
                    self.adb.run_for(self.serial, "shell", f"kill {pid}", check=False, timeout=3)
            except (OSError, subprocess.SubprocessError):
                pass
        if self.proc is not None:
            try:
                self.proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self.proc.terminate()
                self.proc.wait(timeout=3)
            self.proc.stdout.close()
            self.proc = None
