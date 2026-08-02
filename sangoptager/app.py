"""Sangoptager — optag sang + melodi direkte til MP3 i sync-mappen."""

from __future__ import annotations

import sys

from PySide6.QtWidgets import QApplication

from . import __version__
from .logsetup import log, setup_logging
from .rawarchive import prune_archive
from .singleinstance import SingleInstanceServer, notify_existing_instance
from .ui.main_window import MainWindow
from .ui.theme import apply_theme


def main() -> int:
    setup_logging()
    log.info("Sangoptager v%s starter (%s)", __version__, sys.platform)
    app = QApplication(sys.argv)
    app.setApplicationName("Sangoptager")

    if notify_existing_instance():
        return 0  # instans nr. 2: den første er frontet — luk stille

    prune_archive()
    apply_theme(app)
    window = MainWindow()

    def bring_to_front():
        window.showNormal()
        window.activateWindow()
        window.raise_()

    instance_server = SingleInstanceServer(bring_to_front)  # noqa: F841

    window.show()
    window.activateWindow()
    window.raise_()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
