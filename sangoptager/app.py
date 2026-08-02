"""Sangoptager — optag sang + melodi direkte til MP3 i sync-mappen."""

from __future__ import annotations

import sys

from PySide6.QtWidgets import QApplication

from . import __version__
from .logsetup import log, setup_logging
from .rawarchive import prune_archive
from .ui.main_window import MainWindow
from .ui.theme import apply_theme


def main() -> int:
    setup_logging()
    log.info("Sangoptager v%s starter (%s)", __version__, sys.platform)
    prune_archive()
    app = QApplication(sys.argv)
    app.setApplicationName("Sangoptager")
    apply_theme(app)
    window = MainWindow()
    window.show()
    window.activateWindow()
    window.raise_()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
