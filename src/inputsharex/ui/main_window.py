"""输入流转X 主窗口：设备 / 设置 / 关于 + 托盘。

界面层只依赖 AppController 的公开信号与方法；不直接触碰 adb/scrcpy 引擎。
"""
from __future__ import annotations

from typing import Any

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QAction, QCloseEvent, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QDialog,
    QMenu,
    QPushButton,
    QScrollArea,
    QStackedWidget,
    QSystemTrayIcon,
    QVBoxLayout,
    QWidget,
)

from .. import __version__
from ..app import CONFIG_PATH, DEFAULT_CONFIG
from ..ui_theme import Theme, build_stylesheet, current_theme
from . import autostart, icons
from .pair_dialog import PairDialog
from .device_dialog import DeviceDialog
from ..devices import TRANSPORTS
from .widgets import (
    Card,
    DeviceCard,
    EmptyState,
    LogPanel,
    MetricCard,
    NavRail,
    SegmentedControl,
    StatusBadge,
    Switch,
    make_label,
)

_SUBTITLES = {
    "starting": "正在准备共享通道…",
    "idle": "等待连接安卓设备",
    "discovering": "正在搜索局域网内的平板…",
    "connecting": "正在建立 adb 连接…",
    "connected": "连接成功，可以开始共享",
    "sharing": "输入已接管 · 键鼠直接流向平板",
    "error": "连接出错，请检查 adb 与网络",
}

_HOTKEYS = (
    ("ctrl+alt+q", "Ctrl + Alt + Q"),
    ("ctrl+alt+f9", "Ctrl + Alt + F9"),
    ("f12", "F12"),
)
_KEEPALIVE = ((8, "8 ms（默认）"), (12, "12 ms"), (16, "16 ms（最省电）"))
_CADENCE = ((2, "2 ms（最低延迟）"), (4, "4 ms（均衡）"), (8, "8 ms（省资源）"))
_PLACEMENTS = ((0, "右侧"), (1, "左侧"), (2, "下方"), (3, "上方"))


def _subtitle_for(state: str, armed: bool) -> str:
    """共享已开启但还没接管时，提示怎么进平板；接管后说明键鼠已流向平板。"""
    if state == "sharing":
        return "输入已接管 · 键鼠直接流向平板" if armed else "共享已开启 · 推到屏幕边缘或按热键接管"
    return _SUBTITLES.get(state, _SUBTITLES["idle"])


def _hotkey_label(value: str) -> str:
    for key, label in _HOTKEYS:
        if key == value:
            return label
    return value or "Ctrl + Alt + Q"


def _human_bytes(total: Any) -> tuple[str, str]:
    try:
        number = float(total or 0)
    except (TypeError, ValueError):
        number = 0.0
    units = ("B", "KB", "MB", "GB", "TB")
    index = 0
    while number >= 1024 and index < len(units) - 1:
        number /= 1024.0
        index += 1
    if index == 0:
        return f"{int(number)}", units[index]
    if number >= 100:
        return f"{number:.0f}", units[index]
    return f"{number:.1f}", units[index]


def _keycap(text: str) -> QLabel:
    label = QLabel(text)
    label.setProperty("role", "chip")
    label.setProperty("tone", "primary")
    label.setAlignment(Qt.AlignCenter)
    return label


class MainWindow(QMainWindow):
    def __init__(
        self,
        controller,
        version: str | None = None,
        theme: Theme | None = None,
    ):
        super().__init__()
        self.controller = controller
        self._version = version or __version__
        self._theme = theme or current_theme()
        self._devices: list[dict] = []
        self._device_cards: list[DeviceCard] = []
        self._stats: dict = {}
        self._state = str(getattr(controller, "state", "idle") or "idle")
        self._armed = bool(getattr(controller, "armed", False))
        try:
            self._connected_serial = str(getattr(controller, "serial", "") or "")
        except Exception:  # 控制器尚未初始化完成时也不该让窗口炸掉
            self._connected_serial = ""
        self._config: dict = dict(DEFAULT_CONFIG)
        self._tray_hint_shown = False
        self._force_quit = False
        self.tray: QSystemTrayIcon | None = None
        self.tray_share_action: QAction | None = None
        self.tray_arm_action: QAction | None = None
        self._pair_dialog: PairDialog | None = None
        self._pending_pair_serial = ""
        self._pending_pair_timer = QTimer(self)
        self._pending_pair_timer.setSingleShot(True)
        self._pending_pair_timer.timeout.connect(self._on_pending_pair_timeout)

        self.setWindowTitle("输入流转X")
        self.setWindowIcon(icons.app_icon())
        self.resize(880, 600)
        self.setMinimumSize(840, 560)

        self._build_ui()
        self._build_tray()
        self._load_settings()
        self._wire_controller()
        self._wire_settings()
        self._apply_theme(self._theme)
        self._sync_initial()

        QTimer.singleShot(180, self._auto_discover)

    # ================================================================ UI
    def _build_ui(self) -> None:
        root = QWidget()
        root.setObjectName("Root")
        self.setCentralWidget(root)
        root_layout = QHBoxLayout(root)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)

        self.nav = NavRail(self._theme, self._version)
        self.nav.navigate.connect(self._switch_page)
        root_layout.addWidget(self.nav)

        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(0)
        right_layout.addWidget(self._build_header())

        self.stack = QStackedWidget()
        self.home_page = self._scroll_page(self._build_home())
        self.settings_page = self._scroll_page(self._build_settings())
        self.about_page = self._scroll_page(self._build_about())
        self.stack.addWidget(self.home_page)
        self.stack.addWidget(self.settings_page)
        self.stack.addWidget(self.about_page)
        right_layout.addWidget(self.stack, 1)
        root_layout.addWidget(right, 1)

        self.refresh_shortcut = QShortcut(QKeySequence("F5"), self)
        self.refresh_shortcut.activated.connect(self._refresh_devices)

    def _scroll_page(self, page: QWidget) -> QScrollArea:
        area = QScrollArea()
        area.setWidgetResizable(True)
        area.setFrameShape(QFrame.NoFrame)
        area.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        area.setWidget(page)
        return area

    def _build_header(self) -> QWidget:
        header = QWidget()
        layout = QHBoxLayout(header)
        layout.setContentsMargins(24, 18, 24, 12)
        layout.setSpacing(16)

        left = QVBoxLayout()
        left.setSpacing(2)
        title = QLabel("输入流转X")
        title.setProperty("role", "product")
        self.state_subtitle = make_label(_SUBTITLES["idle"], "faint")
        left.addWidget(title)
        left.addWidget(self.state_subtitle)
        layout.addLayout(left)
        layout.addStretch(1)

        self.status_badge = StatusBadge(self._theme)
        layout.addWidget(self.status_badge, 0, Qt.AlignVCenter)

        device = QVBoxLayout()
        device.setSpacing(1)
        self.header_name = QLabel("未选择设备")
        self.header_name.setProperty("role", "deviceName")
        self.header_name.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.header_name.setMaximumWidth(300)
        self.header_addr = QLabel("连接平板后在这里显示")
        self.header_addr.setProperty("role", "deviceAddr")
        self.header_addr.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.header_addr.setMaximumWidth(300)
        device.addWidget(self.header_name)
        device.addWidget(self.header_addr)
        layout.addLayout(device)
        return header

    def _build_home(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(24, 8, 24, 16)
        layout.setSpacing(12)

        # 实时指标放在页面顶部：视线从状态徽标往下就能看到
        metrics = QHBoxLayout()
        metrics.setSpacing(10)
        self.rtt_card = MetricCard("延迟")
        self.pps_card = MetricCard("发送频率")
        self.bytes_card = MetricCard("累计发送")
        self.arm_card = MetricCard("输入状态")
        for card in (self.rtt_card, self.pps_card, self.bytes_card, self.arm_card):
            card.set_theme(self._theme)
            metrics.addWidget(card, 1)
        layout.addLayout(metrics)

        columns = QHBoxLayout()
        columns.setSpacing(12)

        main = QVBoxLayout()
        main.setSpacing(12)

        bar = QHBoxLayout()
        bar.setSpacing(8)
        bar.addWidget(make_label("设备", "section"))
        self.device_count = make_label("0 台设备", "faint")
        bar.addWidget(self.device_count)
        bar.addStretch(1)
        self.add_device_button = QPushButton("添加设备")
        self.add_device_button.setObjectName("GhostButton")
        self.add_device_button.clicked.connect(lambda: self._edit_device(None))
        bar.addWidget(self.add_device_button)
        self.pair_button = QPushButton("配对…")
        self.pair_button.setObjectName("GhostButton")
        self.pair_button.setCursor(Qt.PointingHandCursor)
        self.pair_button.setToolTip("首次连接时，用平板无线调试里的配对码完成配对")
        self.pair_button.clicked.connect(self._open_pair_dialog)
        bar.addWidget(self.pair_button)
        self.refresh_button = QPushButton("重新扫描")
        self.refresh_button.setObjectName("GhostButton")
        self.refresh_button.setCursor(Qt.PointingHandCursor)
        self.refresh_button.clicked.connect(self._refresh_devices)
        bar.addWidget(self.refresh_button)
        main.addLayout(bar)

        self.transport_tabs = SegmentedControl()
        for label in TRANSPORTS.values():
            self.transport_tabs.addTab(label)
        self.transport_tabs.currentChanged.connect(lambda _: self._refresh_device_cards())
        main.addWidget(self.transport_tabs)

        self.device_stack = QStackedWidget()
        self.device_stack.setFixedHeight(220)
        self.empty_state = EmptyState(self._theme)
        self.device_scroll = QScrollArea()
        self.device_scroll.setWidgetResizable(True)
        self.device_scroll.setFrameShape(QFrame.NoFrame)
        self.device_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.device_container = QWidget()
        self.device_layout = QVBoxLayout(self.device_container)
        self.device_layout.setContentsMargins(0, 0, 4, 0)
        self.device_layout.setSpacing(8)
        self.device_scroll.setWidget(self.device_container)
        self.device_stack.addWidget(self.empty_state)
        self.device_stack.addWidget(self.device_scroll)
        main.addWidget(self.device_stack)

        actions = QHBoxLayout()
        actions.setSpacing(10)
        self.primary_button = QPushButton("开始共享")
        self.primary_button.setObjectName("PrimaryButton")
        self.primary_button.setCursor(Qt.PointingHandCursor)
        self.primary_button.setMinimumSize(156, 46)
        self.primary_button.clicked.connect(self._on_primary)
        actions.addWidget(self.primary_button)
        self.arm_button = QPushButton("接管切换")
        self.arm_button.setObjectName("GhostButton")
        self.arm_button.setCursor(Qt.PointingHandCursor)
        self.arm_button.setMinimumHeight(46)
        self.arm_button.clicked.connect(self._on_arm)
        actions.addWidget(self.arm_button)
        actions.addStretch(1)
        self.mode_hint = make_label("当前：仅连接，不接管鼠标", "faint")
        actions.addWidget(self.mode_hint, 0, Qt.AlignVCenter)
        main.addLayout(actions)
        main.addStretch(1)

        columns.addLayout(main, 1)

        # 运行日志固定在右侧栏，整列高度可见，不再被页面底部裁掉
        self.log_panel = LogPanel(self._theme)
        self.log_panel.set_side_panel(True)
        self.log_panel.setMinimumWidth(256)
        self.log_panel.setMaximumWidth(256)
        columns.addWidget(self.log_panel, 0)

        layout.addLayout(columns, 1)
        return page

    def _new_switch(self, checked: bool = False) -> Switch:
        switch = Switch()
        switch.set_theme(self._theme)
        switch.set_checked_silently(checked)
        return switch

    def _build_settings(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(24, 4, 24, 20)
        layout.setSpacing(14)

        head = QHBoxLayout()
        head.addWidget(make_label("设置", "section"))
        head.addStretch(1)
        head.addWidget(make_label("更改会立即保存", "faint"))
        layout.addLayout(head)

        general = Card("常规", "启动方式与全局热键")
        self.autostart_switch = self._new_switch()
        general.add_row(
            "开机自启",
            "登录 Windows 后在托盘静默启动（写入当前用户的注册表 Run 项）",
            self.autostart_switch,
        )
        self.start_armed_switch = self._new_switch()
        general.add_row(
            "开始共享时直接接管",
            "关闭时先建立连接，再由「接管切换」决定是否占用鼠标",
            self.start_armed_switch,
        )
        self.hotkey_combo = QComboBox()
        for value, label in _HOTKEYS:
            self.hotkey_combo.addItem(label, value)
        general.add_row("全局热键", "在任意窗口切换「接管 / 仅连接」", self.hotkey_combo)
        layout.addWidget(general)

        link_card = Card("无线链路", "adb 保活与键鼠事件合并节奏")
        self.keepalive_combo = QComboBox()
        for value, label in _KEEPALIVE:
            self.keepalive_combo.addItem(label, value)
        link_card.add_row("保活间隔", "adb 连接的心跳间隔，越短越不易掉线", self.keepalive_combo)
        self.cadence_combo = QComboBox()
        for value, label in _CADENCE:
            self.cadence_combo.addItem(label, value)
        link_card.add_row("合并周期", "事件合并后发送的周期；越小延迟越低、越费电", self.cadence_combo)
        self.auto_connect_switch = self._new_switch()
        link_card.add_row("自动连接", "发现已配对设备后自动建立连接", self.auto_connect_switch)
        self.keep_awake_switch = self._new_switch()
        link_card.add_row("保持平板常亮", "共享期间阻止平板自动息屏", self.keep_awake_switch)
        layout.addWidget(link_card)

        mouse = Card("鼠标控制", "指针接管与触控映射")
        self.edge_switch = self._new_switch()
        mouse.add_row(
            "边缘切换",
            "指针碰到屏幕边缘时把焦点交给平板（需已连接）",
            self.edge_switch,
        )
        self.double_combo = QComboBox()
        self.double_combo.addItem("关闭", False)
        self.double_combo.addItem("300 ms（合并为双击）", True)
        mouse.add_row(
            "双击",
            "300 ms 内的两次点击合并为原生双击；关闭时逐次发送",
            self.double_combo,
        )
        self.placement_combo = QComboBox()
        for value, label in _PLACEMENTS:
            self.placement_combo.addItem(label, value)
        mouse.add_row("平板放置方向", "平板位于电脑的哪一侧，决定边缘切换方向", self.placement_combo)
        self.lock_refresh_switch = self._new_switch()
        mouse.add_row(
            "锁定高刷新率（实验）",
            "安卓端用透明覆盖层向系统申请 120Hz，让抖音等视频 App 前台也保持高刷；需要新版安卓 App，略增耗电",
            self.lock_refresh_switch,
        )
        layout.addWidget(mouse)

        layout.addStretch(1)
        config_note = QLabel(f"配置文件：{CONFIG_PATH}")
        config_note.setProperty("role", "faint")
        config_note.setWordWrap(True)
        layout.addWidget(config_note)
        return page

    def _build_about(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(24, 4, 24, 20)
        layout.setSpacing(14)

        hero = QHBoxLayout()
        hero.setSpacing(14)
        logo = QLabel()
        logo.setPixmap(icons.logo_pixmap(52, 2.0))
        logo.setFixedSize(58, 58)
        hero.addWidget(logo, 0, Qt.AlignTop)
        column = QVBoxLayout()
        column.setSpacing(2)
        column.addWidget(make_label("输入流转X", "section"))
        column.addWidget(make_label(f"版本 v{self._version} · Windows 10 / 11", "dim"))
        desc = make_label(
            "把 Windows 的键盘鼠标通过 adb + scrcpy UHID 共享给安卓平板，"
            "数据只在本机与平板之间传输。",
            "faint",
        )
        desc.setWordWrap(True)
        column.addWidget(desc)
        hero.addLayout(column, 1)
        layout.addLayout(hero)

        links = Card("版本与链接")
        links.add_row("当前版本", "在命令行运行 inputsharex --version 也可查看", _keycap(f"v{self._version}"))
        links.add_row(
            "GitHub",
            "仓库地址占位，发布后替换为正式链接",
            make_label("github.com/…（占位）", "dim"),
        )
        links.add_row("界面技术", "PySide6 / Qt 6，跟随系统深浅色", make_label("Qt 6", "dim"))
        layout.addWidget(links)

        licenses = Card("开源声明")
        text = make_label(
            "输入流转X 是独立重写的桌面端，与 InputShare2 官方无关联，也不包含其任何界面资源。\n"
            "第三方组件：scrcpy（Apache-2.0）、Android platform-tools（Google 授权）、"
            "PySide6 / Qt（LGPL-3.0）、Python（PSF）。各组件版权归原作者所有。",
            "faint",
        )
        text.setWordWrap(True)
        licenses.add_widget(text)
        layout.addWidget(licenses)

        keys = Card("快捷键", "全局热键可在设置页更换")
        self._about_hotkey_cap = _keycap(
            _hotkey_label(str(self._config.get("hotkey") or "ctrl+alt+q"))
        )
        keys.add_row(
            "切换接管 / 仅连接",
            "在任意窗口生效，默认组合可在设置中修改",
            self._about_hotkey_cap,
        )
        keys.add_row("重新扫描设备", "在设备页或任意页面按 F5", _keycap("F5"))
        keys.add_row("打开 / 隐藏窗口", "关闭窗口默认最小化到托盘，双击托盘图标恢复", _keycap("托盘"))
        layout.addWidget(keys)

        help_card = Card("故障排查", "按顺序检查，仍未解决请看运行日志")
        help_card.add_row(
            "1. 打开无线调试",
            "设置 → 关于平板电脑 → 连续点击「版本号」7 次，"
            "再进入「开发者选项 → 无线调试」并打开。",
        )
        help_card.add_row(
            "2. 同一 Wi-Fi 与 mDNS",
            "电脑和平板需要处于同一网段；路由器开启 AP 隔离或使用访客网络时，"
            "设备发现会失败。",
        )
        help_card.add_row(
            "3. 配对",
            "首次连接需在平板「无线调试 → 使用配对码配对设备」查看 IP、端口和 6 位配对码，"
            "回到设备页点「配对…」输入。",
        )
        help_card.add_row(
            "4. adb 端口冲突",
            "Android Studio 等工具可能占用 5037 端口；先退出它们，"
            "或在命令行执行 adb kill-server 后重试。",
        )
        help_card.add_row(
            "5. 检查运行日志",
            "设备页右侧的运行日志会显示发现、连接与共享失败的原因；"
            "也请确认未被安全软件隔离 vendor 下的 adb 与 scrcpy 文件。",
        )
        layout.addWidget(help_card)
        layout.addStretch(1)
        return page

    # ================================================================ 数据同步
    def _switch_page(self, index: int) -> None:
        if 0 <= index < self.stack.count():
            self.stack.setCurrentIndex(index)
            self.nav.set_current(index)

    def _active_device(self) -> dict | None:
        serial = self._connected_serial
        if not serial:
            return None
        for device in self._devices:
            if device.get("id") == getattr(self.controller, "_active_profile", ""):
                return device
        return next((d for d in self._devices if d.get("serial") == serial), None)
        return None

    def _sync_initial(self) -> None:
        self.status_badge.set_state(self._state)
        self.state_subtitle.setText(_subtitle_for(self._state, self._armed))
        self._update_device_header()
        self._update_actions()
        self._update_metrics()
        self._refresh_tray()

    def _update_device_header(self) -> None:
        device = self._active_device()
        stats_name = str(self._stats.get("device_name") or "")
        if device is not None:
            name = str(device.get("name") or self._connected_serial)
            addr = str(device.get("addr") or self._connected_serial)
        elif self._connected_serial:
            name = self._connected_serial
            addr = "已保存的设备"
        elif stats_name:
            name = stats_name
            addr = "已连接"
        else:
            name = "未选择设备"
            addr = "连接平板后在这里显示"
        self.header_name.setText(name)
        transport = self._stats.get("transport_label", "")
        self.header_addr.setText(f"{transport} · {addr}" if transport else addr)
        self.header_name.setToolTip(name)
        self.header_addr.setToolTip(addr)

    def _refresh_device_cards(self) -> None:
        while self.device_layout.count():
            item = self.device_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        self._device_cards = []
        serial = self._connected_serial
        for kind, _label in TRANSPORTS.items():
            if kind != list(TRANSPORTS)[self.transport_tabs.currentIndex()]:
                continue
            group = [d for d in self._devices if d.get("transport") == kind]
            for device in group:
                card = DeviceCard(self._theme)
                card.set_device(device, active=device.get("id") == getattr(self.controller, "_active_profile", ""))
                card.clicked.connect(self._on_device_clicked)
                card.editRequested.connect(self._edit_device)
                card.deleteRequested.connect(self._delete_device)
                self.device_layout.addWidget(card)
                self._device_cards.append(card)
            if not group:
                self.device_layout.addWidget(make_label("暂无设备，可手动添加或重新扫描", "faint"))
        self.device_layout.addStretch(1)
        self.device_count.setText(f"{len(self._devices)} 个连接配置")
        self.device_stack.setCurrentIndex(1)

    def _update_actions(self) -> None:
        sharing = self._state in ("starting", "sharing")
        self.primary_button.setText("停止共享" if sharing else "开始共享")
        self.primary_button.setToolTip(
            "停止共享，保留 adb 连接" if sharing else "开始共享并直接接管鼠标"
        )
        self.mode_hint.setText(
            ("鼠标已接管" if self._armed else "鼠标未接管") +
            (" · 下次自动共享：" + ("开" if self._stats.get("auto_share") else "关")
             if self._stats.get("auto_share") is not None else "")
        )
        self.arm_button.setToolTip(
            f"当前为「{'已接管' if self._armed else '仅连接'}」，点击切换"
        )
        if self.tray_share_action is not None:
            self.tray_share_action.setText("停止共享" if sharing else "开始共享")
        if self.tray_arm_action is not None:
            self.tray_arm_action.setText("接管切换")

    def _update_metrics(self) -> None:
        stats = self._stats
        rtt = stats.get("rtt_ms")
        if isinstance(rtt, (int, float)):
            rtt_text = f"{rtt:.0f}" if rtt >= 10 else f"{rtt:.1f}"
            tone = "ok" if rtt <= 60 else ("warn" if rtt <= 120 else "danger")
            self.rtt_card.set_metric(rtt_text, "ms", tone)
        else:
            self.rtt_card.set_metric("—", "ms", None)

        packets = stats.get("packets_per_sec") or 0
        try:
            packets_value = float(packets)
        except (TypeError, ValueError):
            packets_value = 0.0
        pps_text = f"{packets_value:.0f}" if packets_value >= 10 else f"{packets_value:.1f}"
        self.pps_card.set_metric(pps_text, "pkt/s", "primary" if packets_value > 0 else None)

        value, unit = _human_bytes(stats.get("sent_bytes"))
        self.bytes_card.set_metric(value, unit, None)

        self.arm_card.set_metric(
            "已接管" if self._armed else "未接管",
            "",
            "ok" if self._armed else "text_dim",
        )

    def _refresh_tray(self) -> None:
        if self.tray is None:
            return
        active = self._state == "sharing" or self._armed
        self.tray.setIcon(icons.tray_icon(active))
        device = self._active_device()
        name = (
            str(device.get("name") or "") if device is not None else ""
        ) or self._connected_serial or str(self._stats.get("device_name") or "") or "未连接"
        if self._state == "sharing":
            status_text = "共享中"
        elif self._state in ("connected", "connecting", "discovering"):
            status_text = _SUBTITLES.get(self._state, "待机")
        else:
            status_text = "待机"
        self.tray.setToolTip(f"输入流转X · {status_text}\n{name}")

    # ================================================================ 控制器信号
    def _wire_controller(self) -> None:
        connections = (
            ("stateChanged", self._on_state),
            ("devicesChanged", self._on_devices),
            ("statsChanged", self._on_stats),
            ("logMessage", self._on_log),
            ("errorRaised", self._on_error),
            ("qrDeviceConnected", self._on_qr_paired_device),
            ("configChanged", self._on_config_changed),
        )
        for name, slot in connections:
            signal = getattr(self.controller, name, None)
            if signal is None:
                continue
            try:
                signal.connect(slot)
            except (AttributeError, RuntimeError, TypeError):
                pass

    def _on_state(self, state: str, detail: str) -> None:
        self._state = state or "idle"
        try:
            serial = str(getattr(self.controller, "serial", "") or "")
        except Exception:
            serial = ""
        if serial:
            self._connected_serial = serial
        elif self._state == "idle":
            self._connected_serial = ""
        self.status_badge.set_state(self._state)
        subtitle = _subtitle_for(self._state, self._armed)
        if self._state == "error" and detail:
            subtitle = detail
        self.state_subtitle.setText(subtitle)
        self._update_device_header()
        self._refresh_device_cards()
        self._update_actions()
        self._update_metrics()
        self._refresh_tray()

    def _on_devices(self, devices: list) -> None:
        self._devices = [dict(item) for item in (devices or []) if isinstance(item, dict)]
        self._refresh_device_cards()
        self._update_device_header()
        self._refresh_tray()
        self._try_connect_pending_pair()

    def _try_connect_pending_pair(self) -> bool:
        """二维码配对成功后，设备一旦出现在列表里就自动发起连接。"""
        serial = self._pending_pair_serial
        if not serial:
            return False
        target = next(
            (d for d in self._devices
             if d.get("serial") == serial and d.get("transport") == "tls"),
            None,
        )
        if target is None:
            return False
        self._pending_pair_serial = ""
        self._pending_pair_timer.stop()
        if self._state in ("starting", "sharing"):
            self.log_panel.append("[提示] 已配对设备已出现，但当前正在共享，未自动切换设备。")
            return True
        self.log_panel.append(f"[信息] 已发现配对设备 {serial}，正在连接…")
        try:
            self.controller.connect_profile(target["id"])
        except Exception as exc:
            self.log_panel.append(f"[错误] 自动连接配对设备失败：{exc}")
        return True

    def _on_qr_paired_device(self, serial: str) -> None:
        serial = (serial or "").strip()
        if not serial:
            return
        self._pending_pair_serial = serial
        self.log_panel.append(f"[信息] 配对成功：{serial}，等待设备出现后自动连接…")
        self._pending_pair_timer.start(20000)
        self._refresh_devices()
        self._try_connect_pending_pair()

    def _on_pending_pair_timeout(self) -> None:
        if not self._pending_pair_serial:
            return
        self.log_panel.append(
            f"[提示] 配对成功但设备尚未出现：{self._pending_pair_serial}；可在设备页手动连接。"
        )
        self._pending_pair_serial = ""

    def _on_stats(self, stats: dict) -> None:
        self._stats = dict(stats or {})
        armed = self._stats.get("armed")
        if armed is not None:
            self._armed = bool(armed)
            if self._state == "sharing":
                self.state_subtitle.setText(_subtitle_for(self._state, self._armed))
        self._update_metrics()
        self._update_actions()
        self._update_device_header()
        self._refresh_tray()

    def _on_log(self, message: str) -> None:
        self.log_panel.append(message)

    def _on_error(self, message: str) -> None:
        self.log_panel.append(f"[错误] {message}")

    def _on_config_changed(self, changes: dict) -> None:
        """安卓端改了设置：静默更新电脑端界面控件，避免触发回写。"""
        if not isinstance(changes, dict):
            return
        self._config.update(changes)
        if "placement" in changes:
            index = self.placement_combo.findData(int(changes["placement"]))
            if index >= 0:
                blocked = self.placement_combo.blockSignals(True)
                self.placement_combo.setCurrentIndex(index)
                self.placement_combo.blockSignals(blocked)
        if "android_keep_awake" in changes:
            self.keep_awake_switch.set_checked_silently(bool(changes["android_keep_awake"]))
        if "lock_refresh" in changes:
            self.lock_refresh_switch.set_checked_silently(bool(changes["lock_refresh"]))

    # ================================================================ 用户操作
    def _auto_discover(self) -> None:
        if self._state in ("discovering", "connecting", "sharing"):
            return
        self.log_panel.append("[信息] 正在发现局域网内的安卓设备…")
        try:
            self.controller.start_discovery()
        except Exception as exc:
            self.log_panel.append(f"[错误] 无法启动设备发现：{exc}")

    def _refresh_devices(self) -> None:
        self.log_panel.append("[信息] 已请求重新扫描设备")
        try:
            self.controller.start_discovery()
        except Exception as exc:
            self.log_panel.append(f"[错误] 无法启动设备发现：{exc}")

    def _on_device_clicked(self, serial: str) -> None:
        device = next((d for d in self._devices if d.get("id") == serial), {})
        self._connected_serial = device.get("serial", "")
        self._refresh_device_cards()
        self._update_device_header()
        self.log_panel.append(f"[信息] 正在连接 {serial}…")
        try:
            self.controller.connect_profile(serial)
        except Exception as exc:
            self.log_panel.append(f"[错误] 连接失败：{exc}")

    def _edit_device(self, identifier):
        device = next((d for d in self._devices if d.get("id") == identifier), None)
        dialog = DeviceDialog(device, self)
        dialog.setStyleSheet(build_stylesheet(self._theme))
        while dialog.exec() == QDialog.Accepted:
            try:
                self.controller.save_device(*dialog.values(), previous_id=identifier)
                return
            except ValueError as exc:
                QMessageBox.warning(self, "无法保存", str(exc))

    def _delete_device(self, identifier):
        device = next((d for d in self._devices if d.get("id") == identifier), {})
        result = QMessageBox.question(self, "删除连接配置", f"删除“{device.get('name', '')}”的此连接配置？\n正在使用该配置时会停止共享。设备配对记录会保留。")
        if result == QMessageBox.Yes:
            self.controller.delete_device(identifier)

    def _on_primary(self) -> None:
        try:
            if self._state in ("starting", "sharing"):
                self.controller.stop_sharing()
            else:
                self.controller.start_sharing()
        except Exception as exc:
            self.log_panel.append(f"[错误] 共享操作失败：{exc}")

    def _on_arm(self) -> None:
        try:
            self.controller.toggle_armed()
        except Exception as exc:
            self.log_panel.append(f"[错误] 切换接管失败：{exc}")

    def _open_pair_dialog(self) -> None:
        dialog = PairDialog(self.controller, self._theme, self)
        try:
            dialog.pairedDevice.connect(self._on_qr_paired_device)
        except (AttributeError, RuntimeError):
            pass
        self._pair_dialog = dialog
        accepted = dialog.exec()
        self._pair_dialog = None
        if accepted:
            self.log_panel.append("[信息] 配对完成，正在重新扫描设备…")
            self._refresh_devices()

    # ================================================================ 设置
    def _load_settings(self) -> None:
        config = dict(DEFAULT_CONFIG)
        try:
            config.update(dict(getattr(self.controller, "config", {}) or {}))
        except Exception:
            pass
        self._config = config

        def select(combo: QComboBox, value: Any) -> None:
            index = combo.findData(value)
            if index >= 0:
                combo.setCurrentIndex(index)

        select(self.hotkey_combo, config.get("hotkey", "ctrl+alt+q"))
        select(self.keepalive_combo, config.get("keepalive_ms", 8))
        select(self.cadence_combo, config.get("cadence_ms", 4))
        select(self.placement_combo, config.get("placement", 0))
        self.start_armed_switch.set_checked_silently(bool(config.get("start_armed", False)))
        self.auto_connect_switch.set_checked_silently(bool(config.get("auto_connect", True)))
        self.keep_awake_switch.set_checked_silently(bool(config.get("android_keep_awake", True)))
        self.edge_switch.set_checked_silently(bool(config.get("edge_switch", True)))
        self.lock_refresh_switch.set_checked_silently(bool(config.get("lock_refresh", False)))
        self.double_combo.setCurrentIndex(1 if bool(config.get("double_trigger", False)) else 0)
        try:
            autostart_enabled = autostart.is_enabled()
        except Exception:
            autostart_enabled = False
        self.autostart_switch.set_checked_silently(autostart_enabled)
        cap = getattr(self, "_about_hotkey_cap", None)
        if cap is not None:
            cap.setText(_hotkey_label(str(config.get("hotkey") or "ctrl+alt+q")))

    def _wire_settings(self) -> None:
        self.autostart_switch.toggled.connect(self._on_autostart_toggled)
        self.start_armed_switch.toggled.connect(
            lambda checked: self._save(start_armed=checked)
        )
        self.auto_connect_switch.toggled.connect(
            lambda checked: self._save(auto_connect=checked)
        )
        self.keep_awake_switch.toggled.connect(
            lambda checked: self._save(android_keep_awake=checked)
        )
        self.edge_switch.toggled.connect(lambda checked: self._save(edge_switch=checked))
        self.lock_refresh_switch.toggled.connect(
            lambda checked: self._save(lock_refresh=checked)
        )
        self.double_combo.currentIndexChanged.connect(
            lambda _index: self._save(double_trigger=bool(self.double_combo.currentData()))
        )
        self.hotkey_combo.currentIndexChanged.connect(
            lambda _index: self._save(hotkey=self.hotkey_combo.currentData())
        )
        self.keepalive_combo.currentIndexChanged.connect(
            lambda _index: self._save(keepalive_ms=int(self.keepalive_combo.currentData()))
        )
        self.cadence_combo.currentIndexChanged.connect(
            lambda _index: self._save(cadence_ms=int(self.cadence_combo.currentData()))
        )
        self.placement_combo.currentIndexChanged.connect(
            lambda _index: self._save(placement=int(self.placement_combo.currentData()))
        )

    def _save(self, **changes: Any) -> None:
        try:
            self.controller.update_config(**changes)
        except Exception as exc:
            self.log_panel.append(f"[错误] 保存设置失败：{exc}")
            return
        self._config.update(changes)
        if "hotkey" in changes:
            cap = getattr(self, "_about_hotkey_cap", None)
            if cap is not None:
                cap.setText(_hotkey_label(str(changes["hotkey"])))

    def _on_autostart_toggled(self, checked: bool) -> None:
        try:
            autostart.set_enabled(checked)
        except Exception as exc:
            self.autostart_switch.set_checked_silently(not checked)
            self.log_panel.append(f"[错误] 设置开机自启失败：{exc}")
            return
        self.log_panel.append("[信息] 已开启开机自启" if checked else "[信息] 已关闭开机自启")

    # ================================================================ 主题/托盘/窗口
    def _apply_theme(self, theme: Theme) -> None:
        self._theme = theme
        app = QApplication.instance()
        if isinstance(app, QApplication):
            app.setStyleSheet(build_stylesheet(theme))
        self.status_badge.set_theme(theme)
        self.log_panel.set_theme(theme)
        self.transport_tabs.set_theme(theme)
        self.nav.set_theme(theme)
        self.empty_state.set_theme(theme)
        for card in self._device_cards:
            card.set_theme(theme)
        for metric in (self.rtt_card, self.pps_card, self.bytes_card, self.arm_card):
            metric.set_theme(theme)
        for switch in (
            self.autostart_switch,
            self.start_armed_switch,
            self.auto_connect_switch,
            self.keep_awake_switch,
            self.edge_switch,
        ):
            switch.set_theme(theme)
        self._refresh_tray()
        self.update()

    def _build_tray(self) -> None:
        try:
            available = QSystemTrayIcon.isSystemTrayAvailable()
        except Exception:
            available = False
        if not available:
            self.tray = None
            return
        self.tray = QSystemTrayIcon(icons.tray_icon(False), self)
        self.tray.setToolTip("输入流转X")
        menu = QMenu(self)
        show_action = QAction("显示窗口", self)
        show_action.triggered.connect(lambda _checked=False: self.show_window())
        menu.addAction(show_action)
        self.tray_share_action = QAction("开始共享", self)
        self.tray_share_action.triggered.connect(lambda _checked=False: self._on_primary())
        menu.addAction(self.tray_share_action)
        self.tray_arm_action = QAction("接管切换", self)
        self.tray_arm_action.triggered.connect(lambda _checked=False: self._on_arm())
        menu.addAction(self.tray_arm_action)
        menu.addSeparator()
        quit_action = QAction("退出", self)
        quit_action.triggered.connect(lambda _checked=False: self._quit())
        menu.addAction(quit_action)
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(self._on_tray_activated)
        self.tray.show()

    @property
    def tray_available(self) -> bool:
        return self.tray is not None

    def _on_tray_activated(self, reason) -> None:
        if reason in (QSystemTrayIcon.Trigger, QSystemTrayIcon.DoubleClick):
            self.show_window()

    def show_window(self) -> None:
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def notify_minimized(self) -> None:
        if self.tray is not None and self.tray.supportsMessages():
            self.tray.showMessage(
                "输入流转X",
                "已在后台运行，双击托盘图标可打开窗口。",
                QSystemTrayIcon.Information,
                4000,
            )

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802 - Qt 命名
        if self._force_quit or self.tray is None:
            self._force_quit = True
            event.accept()
            return
        event.ignore()
        self.hide()
        if not self._tray_hint_shown and self.tray.supportsMessages():
            self.tray.showMessage(
                "输入流转X 仍在运行",
                "关闭窗口只是最小化到托盘；共享不会中断。右键托盘图标可退出。",
                QSystemTrayIcon.Information,
                4500,
            )
            self._tray_hint_shown = True

    def _quit(self) -> None:
        self._force_quit = True
        if self.tray is not None:
            self.tray.hide()
        QApplication.quit()
