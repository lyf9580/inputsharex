import os
import runpy
import secrets
import socket
import subprocess
import time
from inputsharex.adb import adb_env
from inputsharex.server import ScrcpyServer
from inputsharex.session import ControlSession

original_init = ControlSession.__init__
def session_init(self, *args, **kwargs):
    original_init(self, *args, **kwargs)
ControlSession.__init__ = session_init

token = secrets.token_hex(32)
relay = None

def stop_relays(adb, matching_token=None):
    for row in adb.shell('ps -A -o PID,ARGS').splitlines():
        parts = row.split()
        if len(parts) >= 6 and parts[0].isdigit() and parts[1:4] == ['app_process', '/', 'ControlRelay']:
            if matching_token is None or parts[4] == matching_token:
                adb.shell('kill ' + parts[0], check=False)

def direct_connect(self, timeout=10.0):
    global relay
    stop_relays(self.adb)
    relay = subprocess.Popen(
        self.adb._base() + ['-s', self.serial, 'shell',
            f'CLASSPATH=/data/local/tmp/inputsharex-relay.jar app_process / ControlRelay {token} 27185'],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=adb_env())
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            s = socket.create_connection((self.serial.rsplit(':', 1)[0], 27185), timeout=2)
            s.sendall(token.encode('ascii'))
            if s.recv(1) != b'\0':
                s.close()
                raise RuntimeError('direct relay handshake failed')
            s.settimeout(None)
            return s
        except OSError:
            time.sleep(.1)
    raise RuntimeError('direct relay unavailable')

ScrcpyServer._connect = direct_connect
try:
    runpy.run_path(os.environ.get('INPUTSHAREX_DIAGNOSTIC', 'tools/diagnostics/fast_motion_jitter.py'), run_name='__main__')
finally:
    if relay is not None:
        relay.terminate()
    from inputsharex.adb import Adb
    stop_relays(Adb(serial=os.environ['INPUTSHAREX_SERIAL']), token)
    if relay is not None:
        relay.wait(timeout=3)
