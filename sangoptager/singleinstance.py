"""Én-instans-vagt: dobbeltklik nr. 2 fronter det eksisterende vindue.

To samtidige instanser ville dele temp-mappen til rå spor og ødelægge
optagelser — derfor Qt-standardmønstret med en lokal socket.
"""

from __future__ import annotations

from PySide6.QtNetwork import QLocalServer, QLocalSocket

from .logsetup import log

_SERVER_NAME = "sangoptager-instans"

# Rundhåndet timeout: en travl PC må ikke få instans nr. 2 til at tro, at
# den første er død — det er netop dér, brugeren dobbeltklikker igen
_CONNECT_TIMEOUT_MS = 2000


def notify_existing_instance() -> bool:
    """True hvis en kørende instans blev fundet og bedt om at vise sig."""
    socket = QLocalSocket()
    socket.connectToServer(_SERVER_NAME)
    if socket.waitForConnected(_CONNECT_TIMEOUT_MS):
        socket.write(b"show")
        socket.waitForBytesWritten(300)
        socket.disconnectFromServer()
        log.info("Anden instans kører allerede — fronter den og lukker")
        return True
    return False


class SingleInstanceServer:
    """Lytter efter senere instanser og kalder on_show, når en dukker op.

    other_instance bliver True, hvis en levende instans dukkede op i racet
    mellem callerens connect-tjek og listen() — så skal calleren lukke."""

    def __init__(self, on_show):
        self._on_show = on_show
        self.other_instance = False
        self._server = QLocalServer()
        self._server.newConnection.connect(self._handle)
        if self._server.listen(_SERVER_NAME):
            return
        # Navnet er optaget: enten en efterladt socket fra et crash, eller en
        # levende instans der var for langsom til callerens connect-tjek.
        # Spørg igen før der ryddes op — at fjerne en LEVENDE servers socket
        # ville give to instanser om samme temp-mappe.
        if notify_existing_instance():
            self.other_instance = True
            return
        QLocalServer.removeServer(_SERVER_NAME)
        if not self._server.listen(_SERVER_NAME):
            log.warning("Kunne ikke starte instans-vagt: %s",
                        self._server.errorString())

    def _handle(self):
        socket = self._server.nextPendingConnection()
        if socket is not None:
            socket.readAll()
            socket.disconnectFromServer()
        self._on_show()
