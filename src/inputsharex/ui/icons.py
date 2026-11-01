"""用 QPainter 现画图标：应用图标、导航图标、设备图标、托盘圆点。

刻意不依赖任何图片文件：打包后只有一个 exe，图标全部运行时绘制，
也方便跟随深浅色主题换颜色。
"""
from __future__ import annotations

import math

from PySide6.QtCore import QPointF, QRectF, QSize, Qt
from PySide6.QtGui import (
    QColor,
    QIcon,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
)

__all__ = [
    "app_icon",
    "tray_icon",
    "logo_pixmap",
    "nav_icon",
    "device_pixmap",
    "dot_pixmap",
]

_LOGO_TOP = "#4F8DF7"
_LOGO_BOTTOM = "#2563EB"


# ---------------------------------------------------------------- 应用图标
def _paint_logo(painter: QPainter, size: float) -> None:
    """蓝底圆角方块 + 白色分享图形（三个点两条连线）。"""
    margin = size * 0.02
    body = QRectF(margin, margin, size - 2 * margin, size - 2 * margin)
    radius = body.width() * 0.235
    path = QPainterPath()
    path.addRoundedRect(body, radius, radius)
    gradient = QLinearGradient(0, 0, 0, size)
    gradient.setColorAt(0.0, QColor(_LOGO_TOP))
    gradient.setColorAt(1.0, QColor(_LOGO_BOTTOM))
    painter.fillPath(path, gradient)

    painter.save()
    painter.setClipPath(path)
    highlight = QLinearGradient(0, 0, 0, size * 0.55)
    highlight.setColorAt(0.0, QColor(255, 255, 255, 44))
    highlight.setColorAt(1.0, QColor(255, 255, 255, 0))
    painter.fillRect(QRectF(0, 0, size, size * 0.55), highlight)
    painter.restore()

    white = QColor(255, 255, 255)
    left = QPointF(size * 0.31, size * 0.50)
    top = QPointF(size * 0.68, size * 0.27)
    bottom = QPointF(size * 0.68, size * 0.73)
    pen = QPen(white, max(1.0, size * 0.055), Qt.SolidLine, Qt.RoundCap)
    painter.setPen(pen)
    painter.setBrush(Qt.NoBrush)
    painter.drawLine(left, top)
    painter.drawLine(left, bottom)
    painter.setPen(Qt.NoPen)
    painter.setBrush(white)
    dot = size * 0.088
    for point in (left, top, bottom):
        painter.drawEllipse(point, dot, dot)


def logo_pixmap(display_size: int = 36, dpr: float = 2.0) -> QPixmap:
    """按高 DPI 渲染的 logo；QLabel 里显示成 display_size 逻辑像素。"""
    dpr = max(1.0, float(dpr))
    device = max(8, int(round(display_size * dpr)))
    pixmap = QPixmap(device, device)
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    try:
        painter.setRenderHint(QPainter.Antialiasing, True)
        _paint_logo(painter, float(device))
    finally:
        painter.end()
    pixmap.setDevicePixelRatio(dpr)
    return pixmap


def app_icon() -> QIcon:
    icon = QIcon()
    for size in (16, 20, 24, 32, 40, 48, 64, 128, 256):
        pixmap = QPixmap(size, size)
        pixmap.fill(Qt.transparent)
        painter = QPainter(pixmap)
        try:
            painter.setRenderHint(QPainter.Antialiasing, True)
            _paint_logo(painter, float(size))
        finally:
            painter.end()
        icon.addPixmap(pixmap)
    return icon


def _circle_pixmap(size: int, color: str, inner: str | None = None,
                   inner_ratio: float = 0.24) -> QPixmap:
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    try:
        painter.setRenderHint(QPainter.Antialiasing, True)
        diameter = size * 0.84
        rect = QRectF((size - diameter) / 2, (size - diameter) / 2, diameter, diameter)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(color))
        painter.drawEllipse(rect)
        if inner:
            painter.setBrush(QColor(inner))
            r = diameter * inner_ratio
            painter.drawEllipse(QPointF(size / 2, size / 2), r, r)
    finally:
        painter.end()
    return pixmap


def tray_icon(active: bool = False) -> QIcon:
    """托盘图标：蓝色圆点；共享中变绿色。"""
    color = "#22C55E" if active else "#3B82F6"
    icon = QIcon()
    for size in (16, 20, 24, 32, 48, 64):
        icon.addPixmap(_circle_pixmap(size, color, "#FFFFFF", 0.26))
    return icon


def dot_pixmap(color: str, size: int = 10) -> QPixmap:
    return _circle_pixmap(size, color)


# ---------------------------------------------------------------- 导航图标
def _nav_pixmap(name: str, size: int, color: str, dpr: float = 2.0) -> QPixmap:
    device = max(8, int(round(size * dpr)))
    pixmap = QPixmap(device, device)
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    try:
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.scale(device / 20.0, device / 20.0)
        pen = QPen(QColor(color), 1.6, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)
        painter.setPen(pen)
        painter.setBrush(Qt.NoBrush)
        if name == "devices":
            painter.drawRoundedRect(QRectF(2.6, 4.2, 14.8, 10.0), 1.8, 1.8)
            painter.drawLine(QPointF(10.0, 14.4), QPointF(10.0, 16.6))
            painter.drawLine(QPointF(7.0, 17.2), QPointF(13.0, 17.2))
        elif name == "settings":
            for y, knob in ((6.0, 7.2), (10.0, 12.8), (14.0, 8.4)):
                painter.drawLine(QPointF(3.0, y), QPointF(17.0, y))
                painter.setBrush(QColor(color))
                painter.setPen(Qt.NoPen)
                painter.drawEllipse(QPointF(knob, y), 1.9, 1.9)
                painter.setBrush(Qt.NoBrush)
                painter.setPen(pen)
        elif name == "about":
            painter.drawEllipse(QPointF(10.0, 10.0), 7.2, 7.2)
            painter.setBrush(QColor(color))
            painter.setPen(Qt.NoPen)
            painter.drawEllipse(QPointF(10.0, 6.7), 1.0, 1.0)
            painter.setPen(pen)
            painter.drawLine(QPointF(10.0, 9.3), QPointF(10.0, 13.7))
        elif name == "refresh":
            painter.drawArc(QRectF(3.4, 3.4, 13.2, 13.2), 50 * 16, 280 * 16)
            painter.drawLine(QPointF(13.2, 2.4), QPointF(16.6, 3.9))
            painter.drawLine(QPointF(16.6, 3.9), QPointF(15.1, 7.1))
        elif name == "chevron_down":
            painter.drawLine(QPointF(5.0, 8.0), QPointF(10.0, 13.0))
            painter.drawLine(QPointF(10.0, 13.0), QPointF(15.0, 8.0))
        elif name == "chevron_right":
            painter.drawLine(QPointF(8.0, 5.0), QPointF(13.0, 10.0))
            painter.drawLine(QPointF(13.0, 10.0), QPointF(8.0, 15.0))
    finally:
        painter.end()
    pixmap.setDevicePixelRatio(dpr)
    return pixmap


def nav_icon(name: str, size: int = 20, color: str = "#9AA4B2") -> QIcon:
    return QIcon(_nav_pixmap(name, size, color))


# ---------------------------------------------------------------- 设备图标
def device_pixmap(color: str = "#9AA4B2", size: int = 28, dpr: float = 2.0) -> QPixmap:
    dpr = max(1.0, float(dpr))
    device = max(8, int(round(size * dpr)))
    pixmap = QPixmap(device, device)
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    try:
        painter.setRenderHint(QPainter.Antialiasing, True)
        scale = device / 28.0
        painter.scale(scale, scale)
        c = QColor(color)
        painter.setPen(QPen(c, 1.6, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        painter.setBrush(Qt.NoBrush)
        painter.drawRoundedRect(QRectF(6.5, 3.4, 15.0, 21.2), 2.6, 2.6)
        painter.setPen(Qt.NoPen)
        painter.setBrush(c)
        painter.drawEllipse(QPointF(14.0, 21.6), 1.0, 1.0)
        painter.setBrush(Qt.NoBrush)
        painter.setPen(QPen(c, 1.1, Qt.SolidLine, Qt.RoundCap))
        painter.drawLine(QPointF(10.2, 6.4), QPointF(17.8, 6.4))
    finally:
        painter.end()
    pixmap.setDevicePixelRatio(dpr)
    return pixmap


def icon_size(value: int | QSize) -> QSize:
    """小工具：把 int 统一成 QSize。"""
    if isinstance(value, QSize):
        return value
    return QSize(int(value), int(value))
