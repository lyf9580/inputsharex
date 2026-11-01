"""离屏冒烟测试：用假控制器驱动界面，截图并打印控件统计。

运行（在仓库根目录）：
    QT_QPA_PLATFORM=offscreen PYTHONPATH=src PYTHONIOENCODING=utf-8 \
        python desktop/smoke_ui.py
"""
from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import QObject, QTimer, Signal
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QPushButton,
    QSpinBox,
    QWidget,
)

from inputsharex import __version__
from inputsharex.app import AppController, DEFAULT_CONFIG
from inputsharex.ui import main_window as mw
from inputsharex.ui.pair_dialog import PairDialog
from inputsharex.ui_theme import DARK, LIGHT, build_stylesheet
from inputsharex.ui.widgets import DeviceCard, MetricCard, Switch

SHOT_DIR = Path("desktop/smoke-shots")


class FakeController(QObject):
    stateChanged = Signal(str, str)
    devicesChanged = Signal(list)
    statsChanged = Signal(dict)
    logMessage = Signal(str)
    errorRaised = Signal(str)
    paired = Signal(bool, str)

    def __init__(self) -> None:
        super().__init__()
        self.state = "idle"
        self.serial: str | None = None
        self.config = dict(DEFAULT_CONFIG)
        self.calls: list[tuple] = []
        self._armed = False

    @property
    def armed(self) -> bool:
        return self._armed

    def start_discovery(self) -> None:
        self.calls.append(("start_discovery",))
        self.state = "discovering"
        self.stateChanged.emit("discovering", "")

    def connect(self, serial: str) -> None:
        self.calls.append(("connect", serial))
        self.serial = serial
        self.state = "connecting"
        self.stateChanged.emit("connecting", serial)

    def start_sharing(self, start_armed: bool | None = None) -> None:
        self.calls.append(("start_sharing", start_armed))
        self._armed = True if start_armed is None else bool(start_armed)
        self.state = "sharing"
        self.stateChanged.emit("sharing", self.serial or "")
        self._emit_stats()

    def stop_sharing(self) -> None:
        self.calls.append(("stop_sharing",))
        self.state = "connected"
        self.stateChanged.emit("connected", self.serial or "")
        self._emit_stats()

    def toggle_armed(self) -> None:
        self.calls.append(("toggle_armed",))
        self._armed = not self._armed
        self._emit_stats()

    def pair(self, ip: str, pair_port: int, code: str) -> None:
        self.calls.append(("pair", ip, pair_port, code))
        self.paired.emit(True, f"已与 {ip}:{pair_port} 配对")

    def update_config(self, **changes) -> None:
        self.calls.append(("update_config", tuple(sorted(changes.items()))))
        self.config.update(changes)

    def shutdown(self) -> None:
        self.calls.append(("shutdown",))

    def _emit_stats(self) -> None:
        self.statsChanged.emit(
            {
                "sent_bytes": 1_640_000,
                "sent_packets": 12400,
                "bytes_per_sec": 3200,
                "packets_per_sec": 61.8,
                "rtt_ms": 18.4,
                "armed": self._armed,
                "device_name": "Xiaomi Pad 6",
                "link_connected": True,
            }
        )


def _ensure_offscreen_fonts() -> None:
    """offscreen 平台没有内置字体目录，借 Windows 字体目录让中文正常渲染。"""
    import os

    if os.environ.get("QT_QPA_PLATFORM") not in ("offscreen", "minimal"):
        return
    if os.environ.get("QT_QPA_FONTDIR"):
        return
    win_fonts = Path("C:/Windows/Fonts")
    if win_fonts.is_dir():
        os.environ["QT_QPA_FONTDIR"] = str(win_fonts)


def main() -> int:
    _ensure_offscreen_fonts()
    errors: list[str] = []

    def guard(label: str, func) -> None:
        try:
            func()
        except Exception as exc:  # 收集所有失败，最后统一报告
            errors.append(f"{label}: {exc!r}")
            import traceback

            traceback.print_exc()

    app = QApplication(sys.argv[:1])
    app.setStyle("Fusion")
    app.setStyleSheet(build_stylesheet(LIGHT))
    app.setApplicationName("InputShareX")
    app.setOrganizationName("InputShareX")

    fake = FakeController()
    real_controller = None
    real_window = None
    window = None

    def boot() -> None:
        nonlocal real_controller, real_window, window
        # 1) 真实 AppController 只做构造与销毁，不启动发现线程（避免碰其他 agent 的 adb）。
        real_controller = AppController()
        assert hasattr(real_controller, "stateChanged")
        real_window = mw.MainWindow(real_controller, version=__version__, theme=LIGHT)
        real_window.show()
        app.processEvents()
        real_window.close()
        real_window.deleteLater()
        real_window = None
        real_controller.shutdown()
        real_controller = None
        print("[ok] AppController + MainWindow 实例化/销毁")

        # 2) 假控制器驱动完整视觉流程
        window = mw.MainWindow(fake, version=__version__, theme=LIGHT)
        window.show()
        app.processEvents()

    def feed_devices() -> None:
        fake.devices = [
            {
                "serial": "192.168.1.42:37123",
                "state": "device",
                "name": "Xiaomi Pad 6",
                "addr": "192.168.1.42:37123",
                "wired": False,
            },
            {
                "serial": "192.168.1.55:5555",
                "state": "offline",
                "name": "Pixel 8",
                "addr": "192.168.1.55:5555",
                "wired": True,
            },
        ]
        fake.devicesChanged.emit(fake.devices)
        fake.serial = "192.168.1.42:37123"
        fake.state = "connected"
        fake.stateChanged.emit("connected", fake.serial)
        fake.logMessage.emit("已发现 2 台设备")
        fake.logMessage.emit("已连接 Xiaomi Pad 6")
        fake._emit_stats()
        QTimer.singleShot(220, lambda: guard("start_sharing", lambda: fake.start_sharing(True)))
        QTimer.singleShot(620, screenshot_home)

    def screenshot_home() -> None:
        assert window is not None
        path = SHOT_DIR / "gui-preview.png"
        ok = window.grab().save(str(path))
        print(f"[shot] home -> {path} ok={ok}")
        stats = {
            "QPushButton": len(window.findChildren(QPushButton)),
            "QComboBox": len(window.findChildren(QComboBox)),
            "QSpinBox": len(window.findChildren(QSpinBox)),
            "DeviceCard": len(window.findChildren(DeviceCard)),
            "MetricCard": len(window.findChildren(MetricCard)),
            "Switch": len(window.findChildren(Switch)),
            "all_widgets": len(window.findChildren(QWidget)),
        }
        print("[counts]", stats)
        print("[labels]", {
            "title": window.header_name.text(),
            "addr": window.header_addr.text(),
            "primary": window.primary_button.text(),
            "arm": window.arm_card.value.text(),
            "rtt": window.rtt_card.value.text(),
            "pps": window.pps_card.value.text(),
            "bytes": window.bytes_card.value.text() + window.bytes_card.unit.text(),
            "log_lines": len(window.log_panel._entries),
            "nav_pages": window.stack.count(),
        })
        assert window.primary_button.text() == "停止共享", window.primary_button.text()
        assert window.arm_card.value.text() == "已接管", window.arm_card.value.text()
        assert len(window.findChildren(DeviceCard)) == 2
        assert dict(mw._PLACEMENTS) == {
            0: "右侧", 1: "左侧", 2: "下方", 3: "上方"
        }, mw._PLACEMENTS
        QTimer.singleShot(120, shoot_settings)

    def shoot_settings() -> None:
        window._switch_page(1)
        app.processEvents()
        path = SHOT_DIR / "gui-preview-settings.png"
        ok = window.grab().save(str(path))
        print(f"[shot] settings -> {path} ok={ok}")
        assert window.stack.currentIndex() == 1
        # 验证设置控件真的走 update_config，并同步关于页热键说明。
        fake.calls.clear()
        window.keepalive_combo.setCurrentIndex(window.keepalive_combo.findData(12))
        window.hotkey_combo.setCurrentIndex(window.hotkey_combo.findData("f12"))
        window.deadzone_spin.setValue(55)
        assert any(call[0] == "update_config" for call in fake.calls), fake.calls
        assert window._config["keepalive_ms"] == 12, window._config
        assert window._config["hotkey"] == "f12", window._config
        assert window._about_hotkey_cap.text() == "F12", window._about_hotkey_cap.text()
        print("[ok] 设置项 -> update_config:", [call[0] for call in fake.calls])
        bar = window.settings_page.verticalScrollBar()
        bar.setValue(bar.maximum())
        app.processEvents()
        path_bottom = SHOT_DIR / "gui-preview-settings-bottom.png"
        ok_bottom = window.grab().save(str(path_bottom))
        print(f"[shot] settings-bottom -> {path_bottom} ok={ok_bottom}")
        QTimer.singleShot(120, shoot_about)

    def shoot_about() -> None:
        window._switch_page(2)
        app.processEvents()
        path = SHOT_DIR / "gui-preview-about.png"
        ok = window.grab().save(str(path))
        print(f"[shot] about -> {path} ok={ok}")
        assert window.stack.currentIndex() == 2
        bar = window.about_page.verticalScrollBar()
        bar.setValue(bar.maximum())
        app.processEvents()
        path_bottom = SHOT_DIR / "gui-preview-about-bottom.png"
        ok_bottom = window.grab().save(str(path_bottom))
        print(f"[shot] about-bottom -> {path_bottom} ok={ok_bottom}")
        QTimer.singleShot(120, shoot_dark)

    def shoot_dark() -> None:
        window._switch_page(0)
        window._apply_theme(DARK)
        app.processEvents()
        path = SHOT_DIR / "gui-preview-dark.png"
        ok = window.grab().save(str(path))
        print(f"[shot] dark-home -> {path} ok={ok}")
        QTimer.singleShot(160, shoot_narrow)

    def shoot_narrow() -> None:
        window._apply_theme(LIGHT)
        window.resize(720, 520)
        app.processEvents()
        path = SHOT_DIR / "gui-preview-narrow.png"
        ok = window.grab().save(str(path))
        print(f"[shot] narrow-720x520 -> {path} ok={ok}")
        QTimer.singleShot(120, finish)

    def finish() -> None:
        try:
            dialog = PairDialog(fake, LIGHT, window)
            dialog.show()
            app.processEvents()
            print("[ok] PairDialog 实例化", dialog.minimumWidth())
            dialog.close()
            from inputsharex.ui import autostart

            print("[ok] autostart.is_enabled =", autostart.is_enabled())
            fake.logMessage.emit("冒烟测试完成")
            print("[calls]", [call[0] for call in fake.calls])
            print("[log tail]", window.log_panel._entries[-1][1] if window.log_panel._entries else "")
            if errors:
                print("[FAIL]", errors)
                app.exit(1)
            else:
                print("SMOKE OK")
                app.exit(0)
        except Exception as exc:
            import traceback

            traceback.print_exc()
            app.exit(1)

    QTimer.singleShot(0, lambda: guard("boot", boot))
    QTimer.singleShot(250, lambda: guard("feed_devices", feed_devices))
    QTimer.singleShot(3000, lambda: app.exit(2))  # 兜底：超时视为失败
    return int(app.exec())


if __name__ == "__main__":
    raise SystemExit(main())
