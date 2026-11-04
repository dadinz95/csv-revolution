"""Istanza unica: un secondo avvio («Apri con…») passa i file alla finestra già aperta."""
from __future__ import annotations

import json

from PySide6.QtCore import QObject, Signal
from PySide6.QtNetwork import QLocalServer, QLocalSocket


class SingleInstance(QObject):
    pathsReceived = Signal(list)

    def __init__(self, key: str):
        super().__init__()
        self.key = key
        self.server: QLocalServer | None = None

    def send(self, paths: list[str], timeout: int = 700) -> bool:
        """Invia i percorsi all'istanza già in esecuzione. False se non ce ne sono."""
        sock = QLocalSocket()
        sock.connectToServer(self.key)
        if not sock.waitForConnected(timeout):
            return False
        sock.write(json.dumps(paths).encode("utf-8"))
        sock.flush()
        sock.waitForBytesWritten(timeout)
        sock.disconnectFromServer()
        if sock.state() != QLocalSocket.UnconnectedState:
            sock.waitForDisconnected(timeout)
        return True

    def listen(self) -> bool:
        QLocalServer.removeServer(self.key)  # elimina un socket rimasto dopo un arresto anomalo
        self.server = QLocalServer(self)
        self.server.newConnection.connect(self._accept)
        return self.server.listen(self.key)

    def _accept(self):
        while self.server and self.server.hasPendingConnections():
            sock = self.server.nextPendingConnection()
            buffer = bytearray()
            sock.readyRead.connect(lambda s=sock, b=buffer: b.extend(bytes(s.readAll())))
            sock.disconnected.connect(lambda s=sock, b=buffer: self._finish(s, b))

    def _finish(self, sock, buffer):
        buffer.extend(bytes(sock.readAll()))
        sock.deleteLater()
        try:
            paths = json.loads(buffer.decode("utf-8") or "[]")
        except ValueError:
            paths = []
        self.pathsReceived.emit([p for p in paths if isinstance(p, str)])
