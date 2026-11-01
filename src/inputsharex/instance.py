"""One GUI per user; subsequent launches restore the existing window."""
from __future__ import annotations

import hashlib
import os
from pathlib import Path

from PySide6.QtCore import QLockFile
from PySide6.QtNetwork import QLocalServer, QLocalSocket


class GuiInstance:
    def __init__(self):
        directory = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "inputsharex"
        directory.mkdir(parents=True, exist_ok=True)
        digest = hashlib.sha256(str(directory).lower().encode()).hexdigest()[:20]
        name = "inputsharex-gui-" + digest
        self.lock = QLockFile(str(directory / "gui.lock"))
        self.primary = self.lock.tryLock(0)
        self.server = None
        if not self.primary:
            client = QLocalSocket()
            client.connectToServer(name)
            if not client.waitForConnected(1500):
                raise RuntimeError("客户端正在启动或退出，请稍后再次打开。")
            client.write(b"show\n")
            client.waitForBytesWritten(1000)
            client.disconnectFromServer()
            return
        self.server = QLocalServer()
        self.server.setSocketOptions(QLocalServer.SocketOption.UserAccessOption)
        QLocalServer.removeServer(name)
        if not self.server.listen(name):
            self.lock.unlock()
            raise RuntimeError("无法创建客户端窗口通信通道。")

    def bind_window(self, window):
        def restore():
            while self.server.hasPendingConnections():
                client = self.server.nextPendingConnection()
                client.disconnectFromServer()
                client.deleteLater()
            window.show_window()
        self.server.newConnection.connect(restore)

    def close(self):
        if self.server is not None:
            self.server.close()
            self.lock.unlock()
