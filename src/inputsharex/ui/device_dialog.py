from PySide6.QtWidgets import QComboBox, QDialog, QDialogButtonBox, QFormLayout, QLineEdit

from ..devices import TRANSPORTS


class DeviceDialog(QDialog):
    def __init__(self, device=None, parent=None):
        super().__init__(parent)
        device = device or {}
        self.setWindowTitle("编辑设备连接" if device else "添加设备连接")
        self.setMinimumWidth(400)
        layout = QFormLayout(self)
        self.name = QLineEdit(str(device.get("name", "")))
        self.transport = QComboBox()
        for key, label in TRANSPORTS.items():
            self.transport.addItem(label, key)
        self.transport.setCurrentIndex(max(0, self.transport.findData(device.get("transport", "tls"))))
        self.address = QLineEdit(str(device.get("serial", "")))
        self.address.setPlaceholderText("无线：IP:端口；有线：设备序列号")
        layout.addRow("设备名称", self.name)
        layout.addRow("连接方式", self.transport)
        layout.addRow("连接地址 / 序列号", self.address)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText("保存")
        buttons.button(QDialogButtonBox.Cancel).setText("取消")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addRow(buttons)

    def values(self):
        return self.name.text(), self.address.text(), self.transport.currentData()
