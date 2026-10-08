"""One desktop per user, with acknowledged local activation requests."""

import hashlib
import os
from pathlib import Path
import tempfile
import time

from PySide6.QtCore import QObject, QLockFile, QTimer, Signal
from PySide6.QtNetwork import QLocalServer, QLocalSocket

from aiflame.core.paths import user_data_dir


class DesktopInstance(QObject):
    activation_requested = Signal()

    def __init__(self, parent=None, *, directory=None):
        super().__init__(parent)
        self.directory = Path(directory) if directory is not None else user_data_dir()
        identity = hashlib.sha256(
            str(self.directory.expanduser().resolve()).encode("utf-8")
        ).hexdigest()[:24]
        # macOS AF_UNIX paths are short; its per-user temporary directory can
        # already consume most of that limit. Keep the socket under /tmp instead.
        base = Path("/tmp") if os.name != "nt" else Path(tempfile.gettempdir())
        self.socket_directory = base / ("aiflame-" + identity)
        self.endpoint = (
            "aiflame-" + identity + "-desktop" if os.name == "nt"
            else str(self.socket_directory / "desktop")
        )
        self.lock = QLockFile(str(self.directory / "desktop.lock"))
        self.lock.setStaleLockTime(0)
        self.server = QLocalServer(self)
        self.server.setSocketOptions(QLocalServer.SocketOption.UserAccessOption)
        self.server.newConnection.connect(self._accept)
        self._clients = {}
        self._primary = False
        self._closed = False

    def start(self, timeout_ms=5000):
        if self._closed:
            raise RuntimeError("桌面实例协调器已关闭。")
        if self._primary:
            return True
        try:
            self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
            self.socket_directory.mkdir(mode=0o700, exist_ok=True)
            if os.name != "nt":
                stat = self.socket_directory.lstat()
                if self.socket_directory.is_symlink() or stat.st_uid != os.getuid():
                    raise OSError("本地通信目录不属于当前用户")
                self.socket_directory.chmod(0o700)
        except OSError as error:
            raise RuntimeError(f"无法创建 SeeEnd 本地通信目录：{error}") from error
        deadline = time.monotonic() + max(0, timeout_ms) / 1000
        while True:
            if self.lock.tryLock(0):
                # Only the lock owner may remove a crashed predecessor's socket.
                QLocalServer.removeServer(self.endpoint)
                if not self.server.listen(self.endpoint):
                    self.lock.unlock()
                    raise RuntimeError(f"无法启动 SeeEnd 本地通信：{self.server.errorString()}")
                self._primary = True
                return True
            if self.lock.error() != QLockFile.LockError.LockFailedError:
                raise RuntimeError("无法锁定 SeeEnd 桌面实例，请检查用户数据目录权限。")
            remaining = int((deadline - time.monotonic()) * 1000)
            if remaining <= 0:
                raise RuntimeError("SeeEnd 已运行但暂未响应，请稍后重试。")
            socket = QLocalSocket()
            try:
                socket.connectToServer(self.endpoint)
                if socket.waitForConnected(min(100, remaining)):
                    socket.write(b"activate\n")
                    remaining = max(1, int((deadline - time.monotonic()) * 1000))
                    socket.waitForBytesWritten(remaining)
                    response = bytearray()
                    while time.monotonic() < deadline:
                        response.extend(bytes(socket.readAll()))
                        if response == b"ok\n":
                            return False
                        if b"\n" in response or len(response) > 32:
                            raise RuntimeError("SeeEnd 本地通信返回了无效响应。")
                        remaining = max(1, int((deadline - time.monotonic()) * 1000))
                        if not socket.waitForReadyRead(remaining):
                            response.extend(bytes(socket.readAll()))
                            if response == b"ok\n":
                                return False
                            break
            finally:
                socket.abort()
            # Connection refused is immediate while the owner is still starting.
            time.sleep(min(0.025, max(0, deadline - time.monotonic())))

    def _accept(self):
        while self.server.hasPendingConnections():
            socket = self.server.nextPendingConnection()
            timer = QTimer(socket)
            timer.setSingleShot(True)
            self._clients[socket] = bytearray()
            timer.timeout.connect(socket.abort)
            socket.disconnected.connect(lambda s=socket: self._release(s))
            socket.readyRead.connect(lambda s=socket: self._read(s))
            timer.start(2000)
            self._read(socket)

    def _read(self, socket):
        buffer = self._clients.get(socket)
        if buffer is None:
            return
        buffer.extend(bytes(socket.readAll()))
        if len(buffer) > 32:
            socket.abort()
        elif b"\n" in buffer:
            if buffer == b"activate\n":
                # Deliver on the GUI event loop, after the shell connects its slot.
                QTimer.singleShot(0, self, self._activate)
                socket.write(b"ok\n")
                socket.disconnectFromServer()
            else:
                socket.abort()

    def _activate(self):
        if not self._closed:
            self.activation_requested.emit()

    def _release(self, socket):
        if socket not in self._clients:
            return
        self._clients.pop(socket)
        socket.deleteLater()

    def close(self):
        if self._closed:
            return
        self._closed = True
        for socket in list(self._clients):
            socket.abort()
            self._release(socket)
        self.server.close()
        if self._primary:
            self.lock.unlock()
            self._primary = False
