"""命令行入口。

  python -m inputsharex run            # 接管鼠标（Ctrl+Alt+Q 激活/释放）
  python -m inputsharex probe          # 只验证链路：创建鼠标并发几帧位移
  python -m inputsharex devices        # 列出设备
"""
from __future__ import annotations

import argparse
import sys
import time

from . import __version__
from .adb import Adb, AdbError
from .capture import InputCapture, MOD_ALT, MOD_CONTROL, VK_Q
from .server import DEFAULT_LOCAL_PORT, ScrcpyServer, default_jar_path
from .session import ControlSession


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="inputsharex", description="Windows→Android 键鼠共享（无线 ADB）")
    parser.add_argument("--version", action="version", version=f"inputsharex {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--serial", help="adb serial，如 192.168.1.2:43175")
    common.add_argument("--adb", help="adb.exe 路径")
    common.add_argument("--adb-port", type=int, default=5038, help="adb server 端口（默认复用原应用的 5038）")
    common.add_argument("--local-port", type=int, default=DEFAULT_LOCAL_PORT, help="本地转发端口")
    common.add_argument("--jar", default=None, help="scrcpy-server.jar 路径")
    common.add_argument("--keepalive-ms", type=int, default=8, help="空闲保活间隔（毫秒，0=关闭）")
    common.add_argument("--no-keepalive", action="store_true")

    run = sub.add_parser("run", parents=[common], help="开始共享（热键 Ctrl+Alt+Q 接管/释放）")
    run.add_argument("--hotkey", default="ctrl+alt+q", choices=["ctrl+alt+q", "ctrl+alt+f9", "ctrl+alt+f10", "f12"])
    run.add_argument("--start-armed", action="store_true", help="启动即接管")

    probe = sub.add_parser("probe", parents=[common], help="链路自检：注入几帧鼠标位移")
    probe.add_argument("--dx", type=int, default=5)
    probe.add_argument("--dy", type=int, default=3)
    probe.add_argument("--count", type=int, default=10)

    auto = sub.add_parser("auto", parents=[common], help="自动找设备并开始共享（推荐日常用这个）")
    auto.add_argument("--ip", default=None, help="只匹配这个前缀的设备，如 192.168.1.")
    auto.add_argument("--hotkey", default="ctrl+alt+q", choices=["ctrl+alt+q", "ctrl+alt+f9", "ctrl+alt+f10", "f12"])
    auto.add_argument("--start-armed", action="store_true", help="启动即接管")

    sub.add_parser("devices", parents=[common], help="列出 adb 设备")
    diag = sub.add_parser("diag", parents=[common], help="测量链路 RTT / 引擎注入延迟 / 发送频率")
    diag.add_argument("--count", type=int, default=400, help="注入的位移次数")
    diag.add_argument("--ping-count", type=int, default=40, help="ping 包数")
    diag.add_argument("--cadence-ms", type=float, default=4.0, help="发送合并周期（毫秒）")
    sub.add_parser("gui", help="打开图形界面（需要 PySide6）")
    return parser


HOTKEYS = {
    "ctrl+alt+q": (MOD_CONTROL | MOD_ALT, VK_Q),
    "ctrl+alt+f9": (MOD_CONTROL | MOD_ALT, 0x78),
    "ctrl+alt+f10": (MOD_CONTROL | MOD_ALT, 0x79),
    "f12": (0, 0x7B),
}


def _open(args) -> tuple[Adb, str]:
    adb = Adb(adb_path=args.adb, port=args.adb_port, serial=args.serial)
    serial = adb.resolve_serial(args.serial)
    adb.serial = serial
    return adb, serial


def cmd_devices(args) -> int:
    adb = Adb(adb_path=args.adb, port=args.adb_port, serial=args.serial)
    for serial, state in adb.devices():
        print(f"{serial}\t{state}")
    return 0


def cmd_probe(args) -> int:
    adb, serial = _open(args)
    jar = args.jar or default_jar_path()
    print(f"[*] 设备 {serial}，服务端 {jar}")
    server = ScrcpyServer(adb, serial, local_port=args.local_port, jar_path=jar)
    try:
        sock = server.start()
        print("[*] 控制通道已连接（dummy 握手通过）")
        session = ControlSession(sock, keepalive_interval=max(args.keepalive_ms, 0) / 1000)
        session.create_mouse("inputsharex")
        session.start()
        time.sleep(0.5)  # 等设备创建 /dev/input 节点
        for index in range(args.count):
            session.mouse_move(args.dx, args.dy)
            time.sleep(0.08)
        time.sleep(0.3)
        print(f"[*] 已发送 {args.count} 帧相对位移 ({args.dx},{args.dy})，"
              f"{session.sent_messages} 条消息 / {session.sent_bytes} 字节")
        session.destroy_devices()
        session.close()
        return 0
    finally:
        server.stop()


def _pct(samples: list[float], q: float) -> float:
    ordered = sorted(samples)
    if not ordered:
        return 0.0
    index = min(len(ordered) - 1, int(len(ordered) * q))
    return ordered[index]


def cmd_diag(args) -> int:
    """一条命令给出「链路 RTT / 引擎注入延迟 / 发送频率」，优化前后可对比。"""
    adb, serial = _open(args)
    print(f"[*] 设备 {serial}")
    host = serial.rsplit(":", 1)[0] if ":" in serial else None

    # 1) 网络往返（adb shell 往返，含 adb 与 shell 开销，作为上限参考）
    rtt: list[float] = []
    for _ in range(10):
        start = time.perf_counter()
        try:
            adb.shell("true", check=False)
        except Exception:
            pass
        rtt.append((time.perf_counter() - start) * 1000)
    print(f"[*] adb 往返  p50 {_pct(rtt, .5):.1f}ms  p95 {_pct(rtt, .95):.1f}ms  max {max(rtt):.1f}ms")

    if host:
        try:
            import subprocess
            text = subprocess.run(
                ["ping", "-n", str(args.ping_count), host],
                capture_output=True, text=True, timeout=60,
                encoding="mbcs", errors="replace",
            ).stdout
            values = [float(m) for m in __import__("re").findall(r"=\s*(\d+)ms", text)]
            if values:
                print(f"[*] ping {host}  p50 {_pct(values, .5):.0f}ms  "
                      f"p95 {_pct(values, .95):.0f}ms  max {max(values):.0f}ms")
        except (OSError, ValueError):
            pass

    # 2) 引擎注入延迟与发送速率
    jar = args.jar or default_jar_path()
    server = ScrcpyServer(adb, serial, local_port=args.local_port, jar_path=jar)
    try:
        sock = server.start()
        session = ControlSession(sock, keepalive_interval=max(args.keepalive_ms, 0) / 1000,
                                 cadence_ms=args.cadence_ms)
        session.create_mouse("inputsharex")
        session.start()
        time.sleep(0.4)
        events = args.count
        start = time.perf_counter()
        for index in range(events):
            session.mouse_move(3 if index % 2 else -2, 2)
            time.sleep(0.004)
        elapsed = time.perf_counter() - start
        time.sleep(0.25)
        stats = session.latency_stats()
        print(f"[*] 注入 {events} 次位移 / {elapsed:.2f}s → {session.sent_packets} 个 TCP 包，"
              f"{session.sent_bytes} 字节")
        if stats["n"]:
            print(f"[*] 引擎注入延迟  p50 {stats['p50']}ms  p95 {stats['p95']}ms  max {stats['max']}ms")
        session.destroy_devices()
        session.close()
        return 0
    finally:
        server.stop()


def cmd_run(args) -> int:
    adb, serial = _open(args)
    return _run_loop(args, adb, serial)


def cmd_auto(args) -> int:
    adb = Adb(adb_path=args.adb, port=args.adb_port, serial=args.serial)
    if args.serial:
        serial = adb.resolve_serial(args.serial)
    else:
        print("[*] 正在通过 mDNS 查找已配对的无线调试设备…")
        serial = adb.discover(ip_hint=args.ip)
    adb.serial = serial
    print(f"[*] 设备 {serial}")
    return _run_loop(args, adb, serial)


def _run_loop(args, adb: Adb, serial: str) -> int:
    jar = args.jar or default_jar_path()
    mods, vk = HOTKEYS[args.hotkey]
    interval = 0 if args.no_keepalive else max(args.keepalive_ms, 0) / 1000

    print(f"[*] inputsharex {__version__} → {serial}")
    server = ScrcpyServer(adb, serial, local_port=args.local_port, jar_path=jar)
    sock = server.start()
    session = ControlSession(sock, keepalive_interval=interval)
    session.create_mouse("inputsharex")
    session.start_keepalive()
    print(f"[*] UHID 鼠标已创建；保活 {'关闭' if interval == 0 else f'{args.keepalive_ms}ms'}")

    capture = InputCapture(
        on_move=lambda dx, dy, buttons: session.mouse_move(dx, dy, buttons),
        on_button=lambda buttons: session.mouse_button(buttons),
        on_wheel=lambda v, h: session.scroll(v, h),
        on_toggle=lambda armed: print("[*] 已接管鼠标" if armed else "[*] 已释放鼠标"),
        hotkey_mods=mods,
        hotkey_vk=vk,
    )
    capture.start()
    if args.start_armed:
        capture.arm()

    print(f"[*] 按 {args.hotkey.upper()} 接管/释放鼠标，Ctrl+C 退出")
    try:
        while not session.closed:
            time.sleep(0.2)
        print("[!] 控制通道已断开")
        return 1
    except KeyboardInterrupt:
        print("\n[*] 正在退出…")
        return 0
    finally:
        capture.stop()
        session.destroy_devices()
        session.close()
        server.stop()


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        if args.command == "devices":
            return cmd_devices(args)
        if args.command == "probe":
            return cmd_probe(args)
        if args.command == "diag":
            return cmd_diag(args)
        if args.command == "run":
            return cmd_run(args)
        if args.command == "auto":
            return cmd_auto(args)
        if args.command == "gui":
            from .gui import main as gui_main
            return gui_main([])
        return 1
    except (AdbError, FileNotFoundError, RuntimeError) as exc:
        print(f"[x] {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
