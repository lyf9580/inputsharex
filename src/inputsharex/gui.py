"""桌面端入口：``python -m inputsharex.gui`` 或 ``inputsharex-gui``。

CLI（``python -m inputsharex``）仍然走 cli.py，不受这里影响。
"""
from __future__ import annotations

import argparse
import sys


def _ensure_package_importable() -> None:
    """允许直接 ``python src/inputsharex/gui.py`` 运行。"""
    try:
        import inputsharex  # noqa: F401
        return
    except ImportError:
        pass
    from pathlib import Path

    src = Path(__file__).resolve().parents[1]
    if str(src) not in sys.path:
        sys.path.insert(0, str(src))


_ensure_package_importable()

from PySide6.QtCore import Qt, QTimer  # noqa: E402
from PySide6.QtGui import QFont, QGuiApplication  # noqa: E402
from PySide6.QtWidgets import QApplication, QMessageBox  # noqa: E402

from inputsharex import __version__  # noqa: E402
from inputsharex.app import AppController  # noqa: E402
from inputsharex.instance import GuiInstance  # noqa: E402
from inputsharex.ui import icons  # noqa: E402
from inputsharex.ui.main_window import MainWindow  # noqa: E402
from inputsharex.ui_theme import DARK, LIGHT, current_theme  # noqa: E402


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="inputsharex-gui",
        description="输入流转X：把 Windows 键鼠共享给安卓端",
    )
    parser.add_argument("--minimized", action="store_true", help="启动后只显示托盘图标")
    parser.add_argument("--start-sharing", action="store_true", help="启动后立即开始共享（默认即开启）")
    parser.add_argument("--no-share", action="store_true", help="启动后只连接、不开始共享")
    parser.add_argument(
        "--no-update-check", action="store_true", help="启动时不检查 GitHub 更新"
    )
    parser.add_argument("--light", action="store_true", help="强制使用浅色主题")
    parser.add_argument("--dark", action="store_true", help="强制使用深色主题")
    parser.add_argument(
        "--version", action="version", version=f"输入流转X {__version__} (PySide6)"
    )
    return parser.parse_args(argv)


def _apply_font(app: QApplication) -> None:
    font = QFont()
    font.setFamilies(
        ["Microsoft YaHei UI", "Microsoft YaHei", "Segoe UI", "Noto Sans SC"]
    )
    font.setPointSizeF(9.5)
    app.setFont(font)


def main(argv: list[str] | None = None) -> int:
    """GUI 主函数；返回进程退出码。"""
    args = _parse_args(list(sys.argv[1:] if argv is None else argv))

    QGuiApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
    )
    app = QApplication.instance()
    if app is None:
        app = QApplication(sys.argv[:1])
    if not isinstance(app, QApplication):
        raise RuntimeError("已有非 GUI 的 QCoreApplication，无法启动桌面界面")

    app.setApplicationName("InputShareX")
    app.setApplicationDisplayName("输入流转X")
    app.setOrganizationName("InputShareX")
    app.setApplicationVersion(__version__)
    app.setDesktopFileName("inputsharex")
    app.setStyle("Fusion")
    _apply_font(app)
    try:
        instance = GuiInstance()
    except (OSError, RuntimeError) as exc:
        QMessageBox.warning(None, "输入流转X", str(exc))
        return 1
    if not instance.primary:
        return 0
    app.setWindowIcon(icons.app_icon())

    theme = DARK if args.dark else (LIGHT if args.light else current_theme())

    try:
        controller = AppController()
    except Exception as exc:  # 初始化失败也要给用户一个可读提示
        QMessageBox.critical(None, "输入流转X", f"初始化失败：\n{exc}")
        return 1

    window = MainWindow(
        controller,
        version=__version__,
        theme=theme,
        check_updates=not args.no_update_check,
    )
    instance.bind_window(window)
    if args.minimized and window.tray_available:
        window.hide()
        window.notify_minimized()
    else:
        window.show()

    # 默认启动即开始共享（但鼠标仍留在电脑上：start_armed 走配置，默认 False）。
    # 需要先不动手的场景可加 --no-share。
    if not args.no_share:
        QTimer.singleShot(700, controller.start_sharing_on_connect)

    try:
        return int(app.exec())
    finally:
        try:
            controller.shutdown()
        except Exception:
            pass
        instance.close()


if __name__ == "__main__":
    raise SystemExit(main())
