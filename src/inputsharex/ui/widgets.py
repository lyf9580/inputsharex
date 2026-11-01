"""可复用的小控件：状态点/徽标、设备卡片、指标卡、日志面板、导航栏、开关。

全部是「代码绘制 + QSS」：不依赖图片资源，主题切换时只需调用 set_theme()。
"""
from __future__ import annotations

import html
import math
import time

from PySide6.QtCore import (
    QEasingCurve,
    QPointF,
    QRectF,
    QSize,
    Qt,
    QTimer,
    QVariantAnimation,
    Signal,
)
from PySide6.QtGui import QColor, QIcon, QPainter, QPen
from PySide6.QtWidgets import (
    QAbstractButton,
    QButtonGroup,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QTextBrowser,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from .. import ui_theme
from ..ui_theme import Theme
from . import icons

__all__ = [
    "StatusDot",
    "StatusBadge",
    "SegmentedControl",
    "DeviceCard",
    "MetricCard",
    "LogPanel",
    "NavRail",
    "Card",
    "EmptyState",
    "Switch",
    "make_label",
    "repolish",
]


def repolish(widget: QWidget) -> None:
    """动态属性变化后让 QSS 重新生效。"""
    style = widget.style()
    style.unpolish(widget)
    style.polish(widget)
    widget.update()


def make_label(text: str = "", role: str = "body", parent: QWidget | None = None) -> QLabel:
    label = QLabel(text, parent)
    label.setProperty("role", role)
    return label


def _mix(a: QColor, b: QColor, t: float) -> QColor:
    t = max(0.0, min(1.0, t))
    return QColor(
        int(round(a.red() + (b.red() - a.red()) * t)),
        int(round(a.green() + (b.green() - a.green()) * t)),
        int(round(a.blue() + (b.blue() - a.blue()) * t)),
    )


# ---------------------------------------------------------------- 状态
class StatusDot(QWidget):
    """带呼吸光晕的小圆点。"""

    def __init__(self, size: int = 12, parent: QWidget | None = None):
        super().__init__(parent)
        self._size = size
        self.setFixedSize(size, size)
        self._color = QColor("#697386")
        self._pulse = False
        self._phase = 0.0
        self._timer = QTimer(self)
        self._timer.setInterval(90)
        self._timer.timeout.connect(self._tick)

    def _tick(self) -> None:
        self._phase = (self._phase + 0.12) % 1.0
        self.update()

    def set_color(self, color: str, pulse: bool = False) -> None:
        self._color = QColor(color)
        if pulse and not self._timer.isActive():
            self._timer.start()
        elif not pulse and self._timer.isActive():
            self._timer.stop()
            self._phase = 0.0
        self._pulse = pulse
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        painter = QPainter(self)
        try:
            painter.setRenderHint(QPainter.Antialiasing, True)
            width = float(self.width())
            height = float(self.height())
            color = QColor(self._color)
            if self._pulse:
                halo = QColor(color)
                alpha = 0.10 + 0.16 * (0.5 + 0.5 * math.sin(self._phase * 2 * math.pi))
                halo.setAlphaF(alpha)
                painter.setPen(Qt.NoPen)
                painter.setBrush(halo)
                painter.drawEllipse(QRectF(0, 0, width, height))
            core = QRectF(2.0, 2.0, width - 4.0, height - 4.0)
            painter.setPen(Qt.NoPen)
            painter.setBrush(color)
            painter.drawEllipse(core)
        finally:
            painter.end()


class StatusBadge(QFrame):
    """顶部状态徽标：圆点 + 文案。"""

    _STATES = {
        "starting": ("准备共享", "warn", True),
        "idle": ("未连接", "text_dim", False),
        "discovering": ("正在发现设备", "primary", True),
        "connecting": ("正在连接", "warn", True),
        "connected": ("已连接", "primary", False),
        "sharing": ("共享中", "ok", False),
        "error": ("连接出错", "danger", False),
    }

    def __init__(self, theme: Theme, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("StatusBadge")
        self.setFixedHeight(27)
        self.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Fixed)
        self._theme = theme
        self._state = "idle"
        layout = QHBoxLayout(self)
        layout.setContentsMargins(11, 0, 13, 0)
        layout.setSpacing(7)
        self.dot = StatusDot(10)
        self.label = QLabel("未连接")
        self.label.setStyleSheet("background: transparent; font-size: 8.5pt;")
        layout.addWidget(self.dot)
        layout.addWidget(self.label)
        self._apply()

    def set_theme(self, theme: Theme) -> None:
        self._theme = theme
        self._apply()

    def set_state(self, state: str) -> None:
        self._state = state if state in self._STATES else "idle"
        self._apply()

    def _apply(self) -> None:
        text, color_key, pulse = self._STATES[self._state]
        color = getattr(self._theme, color_key)
        self.label.setText(text)
        self.label.setStyleSheet(f"color: {color}; background: transparent; font-size: 8.5pt;")
        self.dot.set_color(color, pulse)


# ---------------------------------------------------------------- 分段切换
class SegmentedControl(QFrame):
    """自绘分段切换：外框胶囊 + 等宽按钮，替换原生 QTabBar 的灰色标签条。"""

    currentChanged = Signal(int)

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("SegmentedControl")
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self._layout = QHBoxLayout(self)
        self._layout.setContentsMargins(4, 4, 4, 4)
        self._layout.setSpacing(4)
        self._group = QButtonGroup(self)
        self._group.setExclusive(True)
        self._buttons: list[QPushButton] = []
        self._current = -1

    def addTab(self, text: str) -> int:
        button = QPushButton(text)
        button.setObjectName("SegmentButton")
        button.setCheckable(True)
        button.setCursor(Qt.PointingHandCursor)
        button.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        index = len(self._buttons)
        self._buttons.append(button)
        self._group.addButton(button, index)
        self._layout.addWidget(button, 1)
        button.clicked.connect(lambda _checked=False, i=index: self.setCurrentIndex(i))
        if self._current < 0:
            self.setCurrentIndex(0)
        return index

    def count(self) -> int:
        return len(self._buttons)

    def currentIndex(self) -> int:
        return self._current

    def setCurrentIndex(self, index: int) -> None:
        if not 0 <= index < len(self._buttons):
            return
        changed = index != self._current
        self._current = index
        self._buttons[index].setChecked(True)
        if changed:
            self.currentChanged.emit(index)

    def set_theme(self, theme: Theme) -> None:
        del theme  # 颜色由全局 QSS 负责，主题切换时重新 polish 即可
        for button in self._buttons:
            repolish(button)
        repolish(self)


# ---------------------------------------------------------------- 设备卡片
class DeviceCard(QFrame):
    """一台设备：图标 + 名称/地址 + 状态 chip，整卡可点。"""

    clicked = Signal(str)
    editRequested = Signal(str)
    deleteRequested = Signal(str)

    def __init__(self, theme: Theme, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("DeviceCard")
        self.setCursor(Qt.PointingHandCursor)
        self.setMinimumHeight(64)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self._theme = theme
        self.serial = ""
        layout = QHBoxLayout(self)
        layout.setContentsMargins(14, 10, 14, 10)
        layout.setSpacing(12)
        self.icon = QLabel()
        self.icon.setFixedSize(30, 30)
        self.icon.setPixmap(icons.device_pixmap(theme.text_dim, 30))
        layout.addWidget(self.icon)
        column = QVBoxLayout()
        column.setSpacing(2)
        self.name_label = QLabel("—")
        self.name_label.setProperty("role", "deviceName")
        self.addr_label = QLabel("")
        self.addr_label.setProperty("role", "deviceAddr")
        column.addWidget(self.name_label)
        column.addWidget(self.addr_label)
        layout.addLayout(column, 1)
        self.chip = QLabel("离线")
        self.chip.setProperty("role", "chip")
        self.chip.setAlignment(Qt.AlignCenter)
        layout.addWidget(self.chip, 0, Qt.AlignVCenter)
        edit = QPushButton("编辑")
        edit.setObjectName("GhostButton")
        edit.clicked.connect(lambda: self.editRequested.emit(self.serial))
        layout.addWidget(edit)
        delete = QPushButton("删除")
        delete.setObjectName("GhostButton")
        delete.clicked.connect(lambda: self.deleteRequested.emit(self.serial))
        layout.addWidget(delete)
        self._active = False
        self._online = False

    def set_theme(self, theme: Theme) -> None:
        self._theme = theme
        self.icon.setPixmap(icons.device_pixmap(theme.text_dim, 30))

    def set_device(self, device: dict, active: bool = False) -> None:
        self.serial = str(device.get("id") or device.get("serial") or "")
        name = str(device.get("name") or self.serial or "—")
        addr = str(device.get("addr") or "")
        state = str(device.get("state") or "")
        online = state == "device"
        self._active = bool(active)
        self._online = online
        self.name_label.setText(name)
        self.addr_label.setText(addr or str(device.get("serial") or ""))
        if active:
            text, tone = "已连接", "ok"
        elif online:
            text, tone = "可连接", "primary"
        elif state == "unauthorized":
            text, tone = "未授权", "warn"
        elif state == "offline":
            text, tone = "离线", ""
        elif addr:
            text, tone = "可连接", "primary"
        else:
            text, tone = "离线", ""
        self.chip.setText(text)
        self.chip.setProperty("tone", tone)
        self.chip.setStyleSheet("")  # 让 QSS 的 tone 选择器接管
        repolish(self.chip)
        self.setProperty("active", "true" if active else "false")
        repolish(self)
        self.setToolTip(f"{name}\n{addr or self.serial}\n点击连接")

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        if event.button() == Qt.LeftButton and self.rect().contains(event.position().toPoint()):
            if self.serial:
                self.clicked.emit(self.serial)
        super().mouseReleaseEvent(event)


# ---------------------------------------------------------------- 指标卡
class MetricCard(QFrame):
    """一个实时指标：标题 + 数值 + 单位。"""

    def __init__(self, title: str, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("MetricCard")
        self.setMinimumHeight(62)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self._theme: Theme = ui_theme.LIGHT
        self._tone: str | None = None
        self._value_text = "—"
        self._unit_text = ""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 10, 14, 10)
        layout.setSpacing(1)
        self.title = QLabel(title)
        self.title.setProperty("role", "metricTitle")
        layout.addWidget(self.title)
        row = QHBoxLayout()
        row.setSpacing(3)
        self.value = QLabel("—")
        self.value.setProperty("role", "metricValue")
        self.unit = QLabel("")
        self.unit.setProperty("role", "metricUnit")
        self.unit.setAlignment(Qt.AlignLeft | Qt.AlignBottom)
        row.addWidget(self.value)
        row.addWidget(self.unit)
        row.addStretch(1)
        layout.addLayout(row)

    def set_theme(self, theme: Theme) -> None:
        self._theme = theme
        self.set_metric(self._value_text, self._unit_text, self._tone)

    def set_metric(self, value: str, unit: str = "", tone: str | None = None) -> None:
        self._value_text = value
        self._unit_text = unit
        self._tone = tone
        self.value.setText(value)
        self.unit.setText(unit)
        color = getattr(self._theme, tone) if tone else self._theme.text
        self.value.setStyleSheet(f"color: {color};")


# ---------------------------------------------------------------- 日志
class LogPanel(QFrame):
    """可折叠的运行日志：保留最近 200 行，富文本按行着色。"""

    MAX_LINES = 200
    collapsedChanged = Signal(bool)

    def __init__(self, theme: Theme, parent: QWidget | None = None):
        super().__init__(parent)
        self.setProperty("card", True)
        self._theme = theme
        self._entries: list[tuple[str, str, str]] = []  # (时间, 文本, 级别)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 12, 16, 14)
        layout.setSpacing(8)
        header = QHBoxLayout()
        header.setSpacing(8)
        title = QLabel("运行日志")
        title.setProperty("role", "cardTitle")
        header.addWidget(title)
        self.count = QLabel("0 行")
        self.count.setProperty("role", "faint")
        header.addWidget(self.count)
        header.addStretch(1)
        self.clear_button = QPushButton("清空")
        self.clear_button.setObjectName("LinkButton")
        self.clear_button.setCursor(Qt.PointingHandCursor)
        self.clear_button.clicked.connect(self.clear)
        header.addWidget(self.clear_button)
        self.toggle = QToolButton()
        self.toggle.setObjectName("LogToggle")
        self.toggle.setCheckable(True)
        self.toggle.setChecked(True)
        self.toggle.setCursor(Qt.PointingHandCursor)
        self.toggle.setText("收起 ▾")
        self.toggle.toggled.connect(self._on_toggle)
        header.addWidget(self.toggle)
        layout.addLayout(header)
        self.view = QTextBrowser()
        self.view.setObjectName("LogView")
        self.view.setOpenExternalLinks(False)
        self.view.setReadOnly(True)
        self.view.setMinimumHeight(64)
        self.view.setMaximumHeight(118)
        self.view.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.view.setFocusPolicy(Qt.NoFocus)
        layout.addWidget(self.view)
        self._render()

    def set_side_panel(self, enabled: bool = True) -> None:
        """右侧栏模式：日志视图纵向拉伸，占满整列高度。"""
        if enabled:
            self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Expanding)
            self.view.setMinimumHeight(180)
            self.view.setMaximumHeight(16777215)
            self.view.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        else:
            self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Preferred)
            self.view.setMinimumHeight(64)
            self.view.setMaximumHeight(118)
            self.view.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

    def _on_toggle(self, checked: bool) -> None:
        self.view.setVisible(checked)
        self.toggle.setText("收起 ▾" if checked else "展开 ▸")
        self.collapsedChanged.emit(not checked)

    def set_collapsed(self, collapsed: bool, notify: bool = False) -> None:
        previous = self.toggle.blockSignals(True)
        self.toggle.setChecked(not collapsed)
        self.toggle.blockSignals(previous)
        self.view.setVisible(not collapsed)
        self.toggle.setText("收起 ▾" if not collapsed else "展开 ▸")
        if notify:
            self.collapsedChanged.emit(collapsed)

    def is_collapsed(self) -> bool:
        return not self.toggle.isChecked()

    def append(self, message: str) -> None:
        message = (message or "").strip()
        if not message:
            return
        level = classify_log(message)
        self._entries.append((time.strftime("%H:%M:%S"), message, level))
        if len(self._entries) > self.MAX_LINES:
            self._entries = self._entries[-self.MAX_LINES:]
        self._render()

    def clear(self) -> None:
        self._entries.clear()
        self._render()

    def set_theme(self, theme: Theme) -> None:
        self._theme = theme
        self._render()

    def _render(self) -> None:
        color_map = {
            "info": self._theme.text_dim,
            "ok": self._theme.ok,
            "warn": self._theme.warn,
            "error": self._theme.danger,
        }
        lines = []
        for stamp, message, level in self._entries:
            color = color_map.get(level, self._theme.text_dim)
            lines.append(
                f'<span style="color:{self._theme.text_faint}">{stamp}</span> '
                f'<span style="color:{color}">{html.escape(message)}</span>'
            )
        self.view.setHtml("<br>".join(lines))
        bar = self.view.verticalScrollBar()
        bar.setValue(bar.maximum())
        self.count.setText(f"{len(self._entries)} 行")


def classify_log(message: str) -> str:
    """按关键词给日志行分级（引擎没有单独的 level 字段）。"""
    error_keys = ("失败", "错误", "出错", "异常", "[x]", "Error", "error")
    warn_keys = ("警告", "注意", "断开", "重连", "超时")
    ok_keys = ("成功", "已连接", "已启动", "已接管", "已保存", "已创建", "已释放", "已写入")
    if any(key in message for key in error_keys):
        return "error"
    if any(key in message for key in warn_keys):
        return "warn"
    if any(key in message for key in ok_keys):
        return "ok"
    return "info"


# ---------------------------------------------------------------- 导航栏
class NavRail(QFrame):
    """左侧窄导航：logo + 三个页面入口 + 版本号。"""

    navigate = Signal(int)

    def __init__(self, theme: Theme, version: str = "", parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("SideBar")
        self.setFixedWidth(76)
        self._theme = theme
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 16, 8, 12)
        layout.setSpacing(6)
        self.logo = QToolButton()
        self.logo.setObjectName("NavLogo")
        self.logo.setIcon(QIcon(icons.logo_pixmap(30, 2.0)))
        self.logo.setIconSize(QSize(34, 34))
        self.logo.setFixedSize(56, 48)
        self.logo.setCursor(Qt.PointingHandCursor)
        self.logo.setToolTip("输入流转X · 回到设备页")
        self.logo.clicked.connect(lambda: self.navigate.emit(0))
        layout.addWidget(self.logo, 0, Qt.AlignHCenter)
        layout.addSpacing(16)
        self.group = QButtonGroup(self)
        self.group.setExclusive(True)
        self.buttons: list[QToolButton] = []
        self._keys = ("devices", "settings", "about")
        for index, (key, label) in enumerate((("devices", "设备"), ("settings", "设置"), ("about", "关于"))):
            button = QToolButton()
            button.setText(label)
            button.setProperty("nav", "true")
            button.setCheckable(True)
            button.setToolButtonStyle(Qt.ToolButtonTextUnderIcon)
            button.setIconSize(QSize(20, 20))
            button.setFixedHeight(58)
            button.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            button.setCursor(Qt.PointingHandCursor)
            button.setToolButtonStyle(Qt.ToolButtonTextUnderIcon)
            button.clicked.connect(lambda _checked=False, idx=index: self.navigate.emit(idx))
            self.group.addButton(button, index)
            self.buttons.append(button)
            layout.addWidget(button)
        layout.addStretch(1)
        self.version = QLabel(f"v{version}" if version else "")
        self.version.setProperty("role", "faint")
        self.version.setAlignment(Qt.AlignHCenter)
        self.version.setToolTip(f"输入流转X v{version}" if version else "")
        self.version.setStyleSheet("font-size: 7.5pt;")
        self.version.setMinimumHeight(14)
        layout.addWidget(self.version)
        self.group.idToggled.connect(self._on_toggled)
        self.buttons[0].setChecked(True)
        self._refresh_icons()

    def _on_toggled(self, button_id: int, checked: bool) -> None:
        del button_id, checked
        self._refresh_icons()

    def _refresh_icons(self) -> None:
        for index, button in enumerate(self.buttons):
            color = self._theme.primary if button.isChecked() else self._theme.text_dim
            button.setIcon(icons.nav_icon(self._keys[index], 20, color))

    def set_current(self, index: int) -> None:
        if 0 <= index < len(self.buttons):
            self.buttons[index].setChecked(True)
            self._refresh_icons()

    def set_theme(self, theme: Theme) -> None:
        self._theme = theme
        self._refresh_icons()


# ---------------------------------------------------------------- 设置卡片
class Card(QFrame):
    """设置/关于页的分组卡片：标题 + 说明 + 若干「左文右控件」行。"""

    def __init__(self, title: str = "", desc: str = "", parent: QWidget | None = None):
        super().__init__(parent)
        self.setProperty("card", True)
        self.root = QVBoxLayout(self)
        self.root.setContentsMargins(16, 14, 16, 16)
        self.root.setSpacing(10)
        if title:
            label = QLabel(title)
            label.setProperty("role", "cardTitle")
            self.root.addWidget(label)
        if desc:
            label = QLabel(desc)
            label.setProperty("role", "faint")
            label.setWordWrap(True)
            self.root.addWidget(label)
        self.body = QVBoxLayout()
        self.body.setSpacing(12)
        self.root.addLayout(self.body)

    def add_row(self, title: str, desc: str, control: QWidget | None = None) -> QWidget:
        row = QWidget()
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(14)
        column = QVBoxLayout()
        column.setSpacing(2)
        title_label = QLabel(title)
        title_label.setProperty("role", "body")
        column.addWidget(title_label)
        if desc:
            desc_label = QLabel(desc)
            desc_label.setProperty("role", "faint")
            desc_label.setWordWrap(True)
            column.addWidget(desc_label)
        layout.addLayout(column, 1)
        if control is not None:
            layout.addWidget(control, 0, Qt.AlignVCenter)
        self.body.addWidget(row)
        return row

    def add_widget(self, widget: QWidget) -> QWidget:
        self.body.addWidget(widget)
        return widget


class EmptyState(QFrame):
    """设备列表空态。"""

    def __init__(self, theme: Theme, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("EmptyState")
        self._theme = theme
        self.setMinimumHeight(172)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 16, 24, 16)
        layout.setSpacing(7)
        layout.addStretch(1)
        self.icon = QLabel()
        self.icon.setAlignment(Qt.AlignHCenter)
        layout.addWidget(self.icon)
        title = QLabel("没有发现安卓设备")
        title.setProperty("role", "cardTitle")
        title.setAlignment(Qt.AlignHCenter)
        layout.addWidget(title)
        desc = QLabel("确保安卓端已开启无线调试，且与本机在同一 Wi-Fi")
        desc.setProperty("role", "dim")
        desc.setAlignment(Qt.AlignHCenter)
        desc.setWordWrap(True)
        layout.addWidget(desc)
        hint = QLabel(
            "首次使用：安卓端「开发者选项 → 无线调试 → 使用配对码配对设备」，"
            "配对后回到这里点「重新扫描」"
        )
        hint.setProperty("role", "faint")
        hint.setAlignment(Qt.AlignHCenter)
        hint.setWordWrap(True)
        layout.addWidget(hint)
        layout.addStretch(1)
        self.set_theme(theme)

    def set_theme(self, theme: Theme) -> None:
        self._theme = theme
        self.icon.setPixmap(icons.device_pixmap(theme.text_faint, 56))


# ---------------------------------------------------------------- 开关
class Switch(QAbstractButton):
    """自绘的 iOS 风格开关（QCheckBox 的 QSS 做不出滑动旋钮）。"""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setCheckable(True)
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedSize(38, 22)
        self.setFocusPolicy(Qt.StrongFocus)
        self._theme: Theme = ui_theme.LIGHT
        self._pos = 0.0
        self._animation = QVariantAnimation(self)
        self._animation.setDuration(140)
        self._animation.setEasingCurve(QEasingCurve.OutCubic)
        self._animation.valueChanged.connect(self._set_pos)
        self.toggled.connect(self._animate)

    def sizeHint(self) -> QSize:  # noqa: N802 - Qt 命名
        return QSize(38, 22)

    def _set_pos(self, value) -> None:
        self._pos = float(value)
        self.update()

    def _animate(self, checked: bool) -> None:
        self._animation.stop()
        self._animation.setStartValue(self._pos)
        self._animation.setEndValue(1.0 if checked else 0.0)
        self._animation.start()

    def set_theme(self, theme: Theme) -> None:
        self._theme = theme
        self.update()

    def set_checked_silently(self, checked: bool) -> None:
        previous = self.blockSignals(True)
        self.setChecked(checked)
        self.blockSignals(previous)
        self._animation.stop()
        self._pos = 1.0 if checked else 0.0
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        del event
        painter = QPainter(self)
        try:
            painter.setRenderHint(QPainter.Antialiasing, True)
            width = float(self.width())
            height = float(self.height())
            track = QRectF(0.5, 0.5, width - 1.0, height - 1.0)
            radius = height / 2.0
            off = QColor(self._theme.border_strong)
            on = QColor(self._theme.primary)
            painter.setPen(Qt.NoPen)
            painter.setBrush(_mix(off, on, self._pos))
            painter.drawRoundedRect(track, radius, radius)
            knob_diameter = height - 6.0
            knob_radius = knob_diameter / 2.0
            x = 3.0 + knob_radius + self._pos * (width - 6.0 - knob_diameter)
            painter.setBrush(QColor("#FFFFFF"))
            painter.drawEllipse(QPointF(x, height / 2.0), knob_radius, knob_radius)
            if self.hasFocus():
                pen = QPen(QColor(self._theme.primary))
                pen.setWidthF(1.0)
                painter.setPen(pen)
                painter.setBrush(Qt.NoBrush)
                painter.drawRoundedRect(track, radius, radius)
        finally:
            painter.end()
