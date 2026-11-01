"""无线调试配对对话框：二维码配对（默认）+ 配对码配对。"""
from __future__ import annotations

from PySide6.QtCore import Qt, QRegularExpression, QTimer, Signal
from PySide6.QtGui import (
    QColor,
    QImage,
    QIntValidator,
    QPainter,
    QPixmap,
    QRegularExpressionValidator,
)
from PySide6.QtWidgets import (
    QDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from ..ui_theme import Theme
from .widgets import SegmentedControl, make_label

try:  # 纯 Python 依赖，缺失时给出可读提示而不是崩掉
    import segno
except ImportError:  # pragma: no cover - 依赖缺失环境
    segno = None


def render_qr(payload: str, size: int = 300) -> QPixmap | None:
    """把配对 payload 渲染成白底黑块的二维码图片；segno 缺失时返回 None。"""
    if segno is None:
        return None
    qr = segno.make(payload, error="m")
    modules = len(qr.matrix)
    border = 4
    total = modules + border * 2
    scale = max(1, size // total)
    image = QImage(total * scale, total * scale, QImage.Format.Format_ARGB32)
    image.fill(QColor(255, 255, 255))
    painter = QPainter(image)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor(0, 0, 0))
    for y, row in enumerate(qr.matrix):
        for x, on in enumerate(row):
            if on:
                painter.drawRect((x + border) * scale, (y + border) * scale, scale, scale)
    painter.end()
    return QPixmap.fromImage(image)


class PairDialog(QDialog):
    """两种配对方式：扫码（默认）或输入配对码。

    二维码页通过 controller.start_qr_pairing()/cancel_qr_pairing() 驱动，
    监听 controller.qrPairStatus / qrPairFinished；配对成功后发 pairedDevice(serial)，
    由主窗口在新设备出现时自动连接。
    """

    pairedDevice = Signal(str)

    def __init__(self, controller, theme: Theme, parent: QWidget | None = None):
        super().__init__(parent)
        self.controller = controller
        self._theme = theme
        self._finished = False
        self._qr_started = False
        self.setWindowTitle("配对安卓设备")
        self.setModal(True)
        self.setMinimumWidth(480)

        root = QVBoxLayout(self)
        root.setContentsMargins(22, 20, 22, 18)
        root.setSpacing(12)

        title = QLabel("配对安卓设备")
        title.setProperty("role", "cardTitle")
        root.addWidget(title)

        self.mode_tabs = SegmentedControl()
        self.mode_tabs.addTab("二维码配对")
        self.mode_tabs.addTab("配对码配对")
        root.addWidget(self.mode_tabs)

        self.pages = QStackedWidget()
        self.pages.addWidget(self._build_qr_page())
        self.pages.addWidget(self._build_code_page())
        root.addWidget(self.pages, 1)

        self.mode_tabs.currentChanged.connect(self._on_mode_changed)

        paired = getattr(controller, "paired", None)
        if paired is not None:
            try:
                paired.connect(self._on_paired)
            except (AttributeError, RuntimeError):
                pass
        qr_status = getattr(controller, "qrPairStatus", None)
        if qr_status is not None:
            try:
                qr_status.connect(self._on_qr_status)
            except (AttributeError, RuntimeError):
                pass
        qr_finished = getattr(controller, "qrPairFinished", None)
        if qr_finished is not None:
            try:
                qr_finished.connect(self._on_qr_finished)
            except (AttributeError, RuntimeError):
                pass
        qr_connected = getattr(controller, "qrDeviceConnected", None)
        if qr_connected is not None:
            try:
                qr_connected.connect(self._on_qr_device_connected)
            except (AttributeError, RuntimeError):
                pass

    # ------------------------------------------------------------------ UI
    def _build_qr_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 4, 0, 0)
        layout.setSpacing(12)

        steps = QLabel(
            "1. 手机/平板打开 设置 → 开发者选项 → 无线调试；\n"
            "2. 点「使用二维码配对设备」；\n"
            "3. 扫描下方二维码，扫码后保持此窗口打开。"
        )
        steps.setProperty("role", "faint")
        steps.setWordWrap(True)
        layout.addWidget(steps)

        self.qr_label = QLabel()
        self.qr_label.setObjectName("QrImage")
        self.qr_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.qr_label.setFixedSize(320, 320)
        holder = QHBoxLayout()
        holder.addStretch(1)
        holder.addWidget(self.qr_label)
        holder.addStretch(1)
        layout.addLayout(holder)

        self.qr_status = QLabel("")
        self.qr_status.setProperty("role", "faint")
        self.qr_status.setWordWrap(True)
        self.qr_status.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.qr_status)

        buttons = QHBoxLayout()
        buttons.setSpacing(8)
        buttons.addStretch(1)
        self.qr_regenerate_button = QPushButton("重新生成")
        self.qr_regenerate_button.setObjectName("GhostButton")
        self.qr_regenerate_button.clicked.connect(self._start_qr)
        buttons.addWidget(self.qr_regenerate_button)
        self.qr_cancel_button = QPushButton("取消")
        self.qr_cancel_button.setObjectName("GhostButton")
        self.qr_cancel_button.clicked.connect(self.reject)
        buttons.addWidget(self.qr_cancel_button)
        layout.addLayout(buttons)
        layout.addStretch(1)
        return page

    def _build_code_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 4, 0, 0)
        layout.setSpacing(12)

        desc = QLabel(
            "在手机/平板的「无线调试 → 使用配对码配对设备」中查看 IP、端口和 6 位配对码。"
            "配对只需做一次，之后靠 mDNS 自动发现。"
        )
        desc.setProperty("role", "faint")
        desc.setWordWrap(True)
        layout.addWidget(desc)

        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignRight | Qt.AlignVCenter)
        form.setFormAlignment(Qt.AlignLeft | Qt.AlignTop)
        form.setHorizontalSpacing(12)
        form.setVerticalSpacing(10)

        self.ip_edit = QLineEdit()
        self.ip_edit.setPlaceholderText("例如 192.168.1.42")
        self.ip_edit.setClearButtonEnabled(True)
        form.addRow(make_label("IP 地址", "dim"), self.ip_edit)

        self.port_edit = QLineEdit()
        self.port_edit.setPlaceholderText("例如 37123")
        self.port_edit.setValidator(QIntValidator(1, 65535, self))
        form.addRow(make_label("配对端口", "dim"), self.port_edit)

        self.code_edit = QLineEdit()
        self.code_edit.setPlaceholderText("6 位配对码")
        self.code_edit.setMaxLength(8)
        self.code_edit.setValidator(
            QRegularExpressionValidator(QRegularExpression(r"\d{0,8}"), self)
        )
        form.addRow(make_label("配对码", "dim"), self.code_edit)
        layout.addLayout(form)

        self.status = QLabel("")
        self.status.setProperty("role", "faint")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)

        buttons = QHBoxLayout()
        buttons.setSpacing(8)
        buttons.addStretch(1)
        self.cancel_button = QPushButton("取消")
        self.cancel_button.setObjectName("GhostButton")
        self.cancel_button.clicked.connect(self.reject)
        buttons.addWidget(self.cancel_button)
        self.submit_button = QPushButton("开始配对")
        self.submit_button.setObjectName("PrimaryButton")
        self.submit_button.setDefault(True)
        self.submit_button.clicked.connect(self._submit)
        buttons.addWidget(self.submit_button)
        layout.addLayout(buttons)
        layout.addStretch(1)
        return page

    # -------------------------------------------------------------- 模式切换
    def showEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        super().showEvent(event)
        if not self._qr_started:
            QTimer.singleShot(0, self._start_qr)

    def _on_mode_changed(self, index: int) -> None:
        if index == 0:
            self.pages.setCurrentIndex(0)
            if not self._finished:
                self._start_qr()
        else:
            self.pages.setCurrentIndex(1)
            self._cancel_qr()
            self.ip_edit.setFocus(Qt.OtherFocusReason)

    # ---------------------------------------------------------------- 二维码
    def _set_qr_status(self, text: str, tone: str = "dim") -> None:
        color = {
            "dim": self._theme.text_dim,
            "ok": self._theme.ok,
            "warn": self._theme.warn,
            "danger": self._theme.danger,
        }.get(tone, self._theme.text_dim)
        self.qr_status.setStyleSheet(f"color: {color};")
        self.qr_status.setText(text)

    def _cancel_qr(self) -> None:
        cancel = getattr(self.controller, "cancel_qr_pairing", None)
        if callable(cancel):
            try:
                cancel()
            except Exception:  # 取消失败不影响界面关闭
                pass

    def _start_qr(self) -> None:
        if self._finished:
            return
        if segno is None:
            self.qr_label.setText("缺少 segno 依赖，无法生成二维码。\n请安装：pip install segno")
            self._set_qr_status("二维码功能不可用，可改用「配对码配对」。", "danger")
            return
        start = getattr(self.controller, "start_qr_pairing", None)
        if not callable(start):
            self._set_qr_status("当前引擎不支持二维码配对。", "danger")
            return
        try:
            info = start() or {}
        except Exception as exc:  # 引擎异常直接展示
            self._set_qr_status(f"生成二维码失败：{exc}", "danger")
            return
        payload = str(info.get("payload", ""))
        if not payload:
            self._set_qr_status("生成二维码失败：缺少配对数据。", "danger")
            return
        pixmap = render_qr(payload, 300)
        if pixmap is None:
            self._set_qr_status("缺少 segno 依赖，无法生成二维码。", "danger")
            self.qr_label.setText("无法生成二维码")
            return
        self._qr_started = True
        self.qr_label.setPixmap(pixmap)
        self.qr_regenerate_button.setEnabled(True)
        self._set_qr_status("正在等待手机/平板扫描二维码…", "dim")

    def _on_qr_status(self, text: str) -> None:
        if self._finished:
            return
        if self.pages.currentIndex() == 0:
            self._set_qr_status(text or "")

    def _on_qr_finished(self, ok: bool, serial: str, message: str) -> None:
        if self._finished:
            return
        if not ok and "取消" in (message or ""):
            # 旧会话被切页/重开窗口取消的结果，不应显示在当前界面上
            return
        text = message or ("配对成功。" if ok else "配对失败，请重试。")
        if not ok:
            if self.pages.currentIndex() == 0:
                self._set_qr_status(text, "danger")
            else:
                self._set_status(text, danger=True)
            self.qr_regenerate_button.setEnabled(True)
            return
        if not serial:
            # 已配对但连接端口还没出现：保持窗口打开，后台会继续等待连接。
            if self.pages.currentIndex() == 0:
                self._set_qr_status(text, "warn")
            else:
                self._set_status(text)
            self.qr_regenerate_button.setEnabled(True)
            return
        if self.pages.currentIndex() == 0:
            self._set_qr_status(text, "ok")
        else:
            self._set_status(text)
        self._finished = True
        self.qr_regenerate_button.setEnabled(False)
        self.pairedDevice.emit(serial)
        QTimer.singleShot(900, self.accept)

    def _on_qr_device_connected(self, serial: str) -> None:
        """后台等待连接成功：对话框还开着时同步显示成功并关闭。"""
        if self._finished:
            return
        text = f"配对成功，已连接 {serial}。"
        if self.pages.currentIndex() == 0:
            self._set_qr_status(text, "ok")
        else:
            self._set_status(text)
        self._finished = True
        self.qr_regenerate_button.setEnabled(False)
        QTimer.singleShot(900, self.accept)

    # ---------------------------------------------------------------- 配对码
    def _set_status(self, text: str, danger: bool = False) -> None:
        color = self._theme.danger if danger else self._theme.text_dim
        self.status.setStyleSheet(f"color: {color};")
        self.status.setText(text)

    def _set_busy(self, busy: bool) -> None:
        self.submit_button.setEnabled(not busy)
        self.cancel_button.setEnabled(not busy)
        self.ip_edit.setEnabled(not busy)
        self.port_edit.setEnabled(not busy)
        self.code_edit.setEnabled(not busy)
        if busy:
            self.submit_button.setText("配对中…")
        else:
            self.submit_button.setText("开始配对")

    def _submit(self) -> None:
        ip = self.ip_edit.text().strip()
        port_text = self.port_edit.text().strip()
        code = self.code_edit.text().strip()
        if not ip or "." not in ip:
            self._set_status("请填写手机/平板上显示的 IP 地址。", danger=True)
            self.ip_edit.setFocus()
            return
        if not port_text.isdigit() or not (1 <= int(port_text) <= 65535):
            self._set_status("配对端口应为 1–65535 之间的数字。", danger=True)
            self.port_edit.setFocus()
            return
        if not (4 <= len(code) <= 8) or not code.isdigit():
            self._set_status("配对码应为 6 位数字（部分系统为 4–8 位）。", danger=True)
            self.code_edit.setFocus()
            return
        self._set_busy(True)
        self._set_status("正在配对，请留意手机/平板上的提示…")
        try:
            self.controller.pair(ip, int(port_text), code)
        except Exception as exc:  # 引擎异常直接展示
            self._set_busy(False)
            self._set_status(f"配对失败：{exc}", danger=True)

    def _on_paired(self, ok: bool, message: str) -> None:
        if self._finished:
            return
        text = message or ("配对成功，可以返回设备页重新扫描。" if ok else "配对失败，请检查 IP、端口和配对码。")
        self._set_status(text, danger=not ok)
        if ok:
            self._finished = True
            self.submit_button.setText("完成")
            self.submit_button.setEnabled(True)
            self.cancel_button.setEnabled(True)
            QTimer.singleShot(700, self.accept)
        else:
            self._set_busy(False)

    # ---------------------------------------------------------------- 生命周期
    def closeEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        self._cancel_qr()
        super().closeEvent(event)
