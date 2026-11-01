"""界面主题：颜色 token、系统深浅色检测、QSS 生成。

UI 层唯一的美术来源：所有颜色都从这里取，改主题只改这一个文件。
主色 #3B82F6（与原应用一致），圆角卡片、克制字号、无多余边框。
"""
from __future__ import annotations

import sys
from dataclasses import dataclass
from string import Template

PRIMARY = "#3B82F6"

UI_FONT = '"Microsoft YaHei UI", "Microsoft YaHei", "Segoe UI", "Noto Sans SC", sans-serif'
MONO_FONT = '"Cascadia Mono", "Consolas", "JetBrains Mono", monospace'


@dataclass(frozen=True)
class Theme:
    """一套界面颜色 token。"""

    dark: bool
    bg: str
    bg_alt: str
    card: str
    card_hover: str
    border: str
    border_strong: str
    text: str
    text_dim: str
    text_faint: str
    primary: str
    primary_hover: str
    primary_pressed: str
    primary_soft: str
    ok: str
    warn: str
    danger: str
    log_bg: str
    chip_bg: str
    scrollbar: str

    @property
    def name(self) -> str:
        return "dark" if self.dark else "light"


DARK = Theme(
    dark=True,
    bg="#0F1115",
    bg_alt="#12151B",
    card="#181C24",
    card_hover="#1E2330",
    border="#262C38",
    border_strong="#38404F",
    text="#E8ECF4",
    text_dim="#9AA4B2",
    text_faint="#697386",
    primary=PRIMARY,
    primary_hover="#4B8FF7",
    primary_pressed="#2563EB",
    primary_soft="rgba(59, 130, 246, 0.16)",
    ok="#22C55E",
    warn="#F59E0B",
    danger="#EF4444",
    log_bg="#10131A",
    chip_bg="#212837",
    scrollbar="#333B4A",
)

LIGHT = Theme(
    dark=False,
    bg="#F5F7FB",
    bg_alt="#EEF2F8",
    card="#FFFFFF",
    card_hover="#F2F6FC",
    border="#E2E8F1",
    border_strong="#C9D3E0",
    text="#111827",
    text_dim="#5B6472",
    text_faint="#8A94A6",
    primary=PRIMARY,
    primary_hover="#2F76EA",
    primary_pressed="#2563EB",
    primary_soft="rgba(59, 130, 246, 0.12)",
    ok="#16A34A",
    warn="#D97706",
    danger="#DC2626",
    log_bg="#F8FAFC",
    chip_bg="#EEF2F8",
    scrollbar="#C9D3E0",
)


def system_prefers_dark() -> bool:
    """读取 Windows 个性化设置里的「应用模式」；读不到就当浅色。"""
    if sys.platform != "win32":
        return False
    try:
        import winreg

        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize",
        ) as key:
            light, _ = winreg.QueryValueEx(key, "AppsUseLightTheme")
        return not bool(light)
    except (OSError, ValueError):
        return False


def current_theme() -> Theme:
    return DARK if system_prefers_dark() else LIGHT


def build_stylesheet(theme: Theme) -> str:
    """把 Theme 展开成全局 QSS。"""
    values = dict(theme.__dict__)
    values["ui_font"] = UI_FONT
    values["mono_font"] = MONO_FONT
    return Template(_QSS_TEMPLATE).substitute(values)


_QSS_TEMPLATE = """
* { outline: none; }
QWidget { color: $text; }
QWidget#Root { background: $bg; }
QFrame#SideBar { background: $bg_alt; border: none; border-right: 1px solid $border; }
QLabel { background: transparent; }

/* ---- 文字层级 ---- */
QLabel[role="product"] { font-size: 20pt; font-weight: bold; color: $text; }
QLabel[role="subtitle"] { font-size: 8pt; color: $text_faint; }
QLabel[role="section"] { font-size: 11pt; font-weight: bold; color: $text; }
QLabel[role="cardTitle"] { font-size: 10pt; font-weight: bold; color: $text; }
QLabel[role="body"] { font-size: 9pt; color: $text; }
QLabel[role="dim"] { font-size: 8.5pt; color: $text_dim; }
QLabel[role="faint"] { font-size: 8pt; color: $text_faint; }
QLabel[role="metricValue"] { font-family: $mono_font; font-size: 16pt; font-weight: bold; color: $text; }
QLabel[role="metricTitle"] { font-size: 8pt; color: $text_faint; }
QLabel[role="metricUnit"] { font-size: 8.5pt; color: $text_faint; }
QLabel[role="deviceName"] { font-size: 10pt; font-weight: bold; color: $text; }
QLabel[role="deviceAddr"] { font-size: 8.5pt; color: $text_dim; font-family: $mono_font; }
QLabel[role="chip"] { padding: 2px 9px; border-radius: 9px; font-size: 8pt; background: $chip_bg; color: $text_dim; }
QLabel[role="chip"][tone="ok"] { color: $ok; background: rgba(34, 197, 94, 0.13); }
QLabel[role="chip"][tone="primary"] { color: $primary; background: $primary_soft; }
QLabel[role="chip"][tone="danger"] { color: $danger; background: rgba(239, 68, 68, 0.13); }

/* ---- 卡片 ---- */
QFrame[card="true"] { background: $card; border: 1px solid $border; border-radius: 14px; }
QFrame#MetricCard { background: $card; border: 1px solid $border; border-radius: 12px; }
QLabel#QrImage { background: #FFFFFF; border: 1px solid $border; border-radius: 14px; padding: 10px; }
QFrame#StatusBadge { background: $chip_bg; border: none; border-radius: 13px; }
QFrame#DeviceCard { background: $card; border: 1px solid $border; border-radius: 12px; }
QFrame#DeviceCard:hover { background: $card_hover; border-color: $border_strong; }
QFrame#DeviceCard[active="true"] { border-color: $primary; background: $primary_soft; }
QFrame#EmptyState { background: transparent; border: 1px dashed $border_strong; border-radius: 14px; }
QFrame#Separator { background: $border; border: none; max-height: 1px; min-height: 1px; }

/* ---- 按钮 ---- */
QPushButton { font-size: 9pt; border-radius: 9px; padding: 7px 14px; }
QPushButton#PrimaryButton {
    background: $primary; color: #FFFFFF; border: none; font-weight: bold;
    padding: 10px 24px; font-size: 10pt; border-radius: 10px;
}
QPushButton#PrimaryButton:hover { background: $primary_hover; }
QPushButton#PrimaryButton:pressed { background: $primary_pressed; }
QPushButton#PrimaryButton:disabled { background: $border; color: $text_faint; }
QPushButton#GhostButton { background: $chip_bg; color: $text; border: 1px solid $border; padding: 8px 14px; }
QPushButton#GhostButton:hover { background: $card_hover; border-color: $border_strong; }
QPushButton#GhostButton:disabled { color: $text_faint; border-color: $border; background: transparent; }
QPushButton#LinkButton { background: transparent; border: none; color: $primary; padding: 4px 6px; font-size: 8.5pt; }
QPushButton#LinkButton:hover { color: $primary_hover; text-decoration: underline; }
QToolButton { background: transparent; border: none; color: $text_dim; }
QToolButton#LogToggle { font-size: 8.5pt; padding: 2px 6px; border-radius: 6px; }
QToolButton#LogToggle:hover { color: $text; background: $card_hover; }
QToolButton#NavLogo { background: transparent; border: none; padding: 0; }
QToolButton[nav="true"] { border: none; border-radius: 10px; color: $text_dim; padding: 7px 0px; font-size: 8pt; }
QToolButton[nav="true"]:hover { background: $card_hover; color: $text; }
QToolButton[nav="true"]:checked { background: $primary_soft; color: $primary; }

/* ---- 分段切换（替换原生 QTabBar） ---- */
QFrame#SegmentedControl { background: $bg_alt; border: 1px solid $border; border-radius: 12px; }
QPushButton#SegmentButton {
    background: transparent; color: $text_dim; border: none;
    border-radius: 9px; padding: 7px 10px; font-size: 9pt;
}
QPushButton#SegmentButton:hover { background: $card_hover; color: $text; }
QPushButton#SegmentButton:checked { background: $primary_soft; color: $primary; font-weight: bold; }
QPushButton#SegmentButton:checked:hover { background: $primary_soft; }

/* ---- 输入控件 ---- */
QDialog { background: $bg; }
QLineEdit {
    background: $card; color: $text; border: 1px solid $border; border-radius: 8px;
    padding: 6px 10px; font-size: 9pt; selection-background-color: $primary_soft;
    selection-color: $text;
}
QLineEdit:hover { border-color: $border_strong; }
QLineEdit:focus { border-color: $primary; }
QLineEdit:disabled { color: $text_faint; background: $bg_alt; }
QComboBox {
    background: $card; color: $text; border: 1px solid $border; border-radius: 8px;
    padding: 5px 10px; min-width: 116px; font-size: 9pt;
}
QComboBox:hover { border-color: $border_strong; }
QComboBox:focus { border-color: $primary; }
QComboBox::drop-down { border: none; width: 22px; }
QComboBox QAbstractItemView {
    background: $card; color: $text; border: 1px solid $border; border-radius: 8px;
    padding: 4px; selection-background-color: $primary_soft; selection-color: $text; outline: 0;
}
QSpinBox {
    background: $card; color: $text; border: 1px solid $border; border-radius: 8px;
    padding: 5px 8px; min-width: 86px; font-size: 9pt; font-family: $mono_font;
}
QSpinBox:hover { border-color: $border_strong; }
QSpinBox:focus { border-color: $primary; }
QSpinBox::up-button, QSpinBox::down-button { width: 16px; border: none; background: transparent; }
QSpinBox::up-arrow, QSpinBox::down-arrow { width: 7px; height: 7px; }

/* ---- 滚动 ---- */
QScrollArea { border: none; background: transparent; }
QScrollArea > QWidget > QWidget { background: transparent; }
QScrollBar:vertical { background: transparent; width: 8px; margin: 2px 1px; }
QScrollBar::handle:vertical { background: $scrollbar; border-radius: 4px; min-height: 26px; }
QScrollBar::handle:vertical:hover { background: $border_strong; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: transparent; }
QScrollBar:horizontal { background: transparent; height: 8px; }
QScrollBar::handle:horizontal { background: $scrollbar; border-radius: 4px; min-width: 26px; }
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal { width: 0; }

/* ---- 日志 ---- */
QTextBrowser#LogView {
    background: $log_bg; border: 1px solid $border; border-radius: 10px;
    padding: 8px 10px; color: $text_dim; font-family: $mono_font; font-size: 8.5pt;
    selection-background-color: $primary_soft;
}

/* ---- 菜单/提示 ---- */
QMenu { background: $card; color: $text; border: 1px solid $border; border-radius: 10px; padding: 6px; }
QMenu::item { padding: 6px 28px 6px 14px; border-radius: 6px; }
QMenu::item:selected { background: $primary_soft; color: $text; }
QMenu::item:disabled { color: $text_faint; }
QMenu::separator { height: 1px; background: $border; margin: 5px 8px; }
QToolTip { background: $card; color: $text; border: 1px solid $border; padding: 4px 8px; }
QMessageBox { background: $bg; }
"""
