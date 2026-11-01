"""测量 UHID 鼠标事件到达安卓内核的抖动（getevent 时间戳间隔）。

用法: python scratch/jitter_test.py <label> [秒数]
"""
import os
import re
import subprocess
import sys
import threading
import time
from pathlib import Path

from inputsharex.adb import Adb
from inputsharex.server import ScrcpyServer, default_jar_path
from inputsharex.session import ControlSession

REPO = Path(__file__).resolve().parents[2]
ADB_EXE = str(REPO / "vendor" / "platform-tools" / "adb.exe")
SERIAL = os.environ.get("INPUTSHAREX_SERIAL", "192.168.1.2:37123")
LABEL = sys.argv[1] if len(sys.argv) > 1 else "test"
SECONDS = float(sys.argv[2]) if len(sys.argv) > 2 else 3.0
CADENCE = float(os.environ.get("INPUTSHAREX_CADENCE", "4"))
OUT = Path("jitter")
OUT.mkdir(parents=True, exist_ok=True)

ENV = os.environ.copy()
ENV.update({"TEMP": str(REPO / ".cache" / "tmp"), "TMP": str(REPO / ".cache" / "tmp")})


def shell(cmd):
    p = subprocess.run([ADB_EXE, "-P", "5038", "-s", SERIAL, "shell", cmd],
                       capture_output=True, text=True, env=ENV)
    return p.stdout or ""


def find_event_node():
    for _ in range(10):
        out = shell("getevent -pl 2>/dev/null | grep -B3 'inputsharex'")
        nums = re.findall(r"/dev/input/event(\d+)", out)
        if nums:
            return f"/dev/input/event{nums[0]}"
        time.sleep(0.5)
    return None


adb = Adb(port=5038)
adb.start_server()
adb.serial = SERIAL
server = ScrcpyServer(adb, SERIAL, jar_path=default_jar_path())
sock = server.start()
tos = int(os.environ.get("INPUTSHAREX_TOS", "0"))
if tos:
    import socket as _socket
    sock.setsockopt(_socket.IPPROTO_IP, _socket.IP_TOS, tos)
    print(f"tos=0x{tos:02x}", flush=True)
session = ControlSession(sock, cadence_ms=CADENCE, keepalive_interval=0.008)
if os.environ.get("INPUTSHAREX_OLD_BUTTONS") == "1":
    from inputsharex import hid, protocol
    def old_button(buttons):
        session.mouse_buttons = buttons & 0x1f
        session.send(protocol.uhid_input(hid.HID_MOUSE_ID, hid.mouse_report(buttons, 0, 0)))
    session.mouse_button = old_button
wire = []
original_send = session._send_locked
def traced_send(payload):
    before = time.perf_counter()
    original_send(payload)
    wire.append((before, time.perf_counter()))
session._send_locked = traced_send
shell('input keyevent KEYCODE_HOME')
session.create_mouse("inputsharex")
session.start()
if os.environ.get('INPUTSHAREX_BOOST') == '1':
    print('boost:', shell('for p in $(pidof app_process); do for t in /proc/$p/task/*; do case $(cat "$t/comm") in control-recv|control-send|UHidManager) renice -n -10 -p ${t##*/};; esac; done; done'), flush=True)
time.sleep(0.5)
node = find_event_node()
print("node:", node, flush=True)
if not node:
    print("no inputsharex node", flush=True)
    sys.exit(1)

cap_path = OUT / f"getevent-{LABEL}.txt"
sys.path.insert(0, str(REPO / "tools" / "diagnostics"))
from remote_capture import RemoteEventCapture
cap = RemoteEventCapture(adb, SERIAL, node, cap_path, SECONDS + 2)
time.sleep(1.0)

sent = [0]
stop = threading.Event()


def ticker():
    while not stop.is_set():
        session.mouse_button(0)
        session.mouse_move(16, 0)
        sent[0] += 1
        time.sleep(0.001)


t = threading.Thread(target=ticker, daemon=True)
input_started = time.perf_counter()
t.start()
time.sleep(3)
launch_host = time.perf_counter()
launch = shell('cat /proc/uptime; am start -W -n com.ss.android.ugc.aweme/.main.MainActivity')
launch_device = float(launch.splitlines()[0].split()[0])
print('launch:', launch, flush=True)
displays = []
for offset in (0, 1, 2, 4, 7, 10):
    remaining = launch_host + offset - time.perf_counter()
    if remaining > 0:
        time.sleep(remaining)
    displays.append((round(time.perf_counter()-launch_host, 3), shell('dumpsys display | grep -m2 -E "mActiveModeId|mActiveSfDisplayMode"')))
    print('display:', displays[-1], flush=True)
time.sleep(2)
stop.set()
t.join()
time.sleep(0.5)
cap.stop()
time.sleep(0.5)

lines = cap_path.read_text(encoding="utf-8", errors="ignore").splitlines()
ts = []
for ln in lines:
    m = re.match(r"\[\s*([0-9.]+)\]\s+EV_REL\s+REL_X", ln)
    if m:
        ts.append(float(m.group(1)))
gaps = [(b - a) * 1000 for a, b in zip(ts, ts[1:])]
if not gaps:
    print("no events captured", flush=True)
else:
    gaps.sort()
    n = len(gaps)
    p50 = gaps[n // 2]
    p90 = gaps[int(n * 0.9)]
    p99 = gaps[int(n * 0.99)] if n > 100 else gaps[-1]
    print(f"{LABEL}: events={len(ts)} sent={sent[0]} gap_ms p50={p50:.1f} p90={p90:.1f} p99={p99:.1f} max={gaps[-1]:.1f}", flush=True)
    (OUT / f"jitter-{LABEL}.txt").write_text(f"{p50:.1f} {p90:.1f} {p99:.1f} {gaps[-1]:.1f}\n", encoding="utf-8")

print("packets:", session.sent_packets, "reports:", session.sent_messages, flush=True)
import json
launch_device = ts[0] + launch_host - input_started
phase_results = []
for title, lower, upper in [('home', -3, 0), ('launch_0_2s', 0, 2), ('launch_2_5s', 2, 5), ('stable_5_12s', 5, 12)]:
    device_times = [stamp for stamp in ts if lower <= stamp-launch_device < upper]
    host_times = [stamp for stamp, end in wire if lower <= stamp-launch_host < upper]
    def summary(values):
        gaps=sorted((b-a)*1000 for a,b in zip(values,values[1:]))
        if not gaps: return {}
        return {'n':len(gaps), 'p50':round(gaps[len(gaps)//2],2), 'p99':round(gaps[min(len(gaps)-1,int(len(gaps)*.99))],2), 'max':round(max(gaps),2)}
    row={'phase':title, 'device':summary(device_times), 'host':summary(host_times)}
    phase_results.append(row)
    print(row, flush=True)
(OUT / f'transition-{LABEL}.json').write_text(json.dumps({'launch':launch,'displays':displays,'phases':phase_results},ensure_ascii=False,indent=2),encoding='utf-8')
session.destroy_devices()
session.close()
server.stop()



