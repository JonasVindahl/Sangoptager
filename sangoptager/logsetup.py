"""Logfil til fjerndiagnose: %APPDATA%\\Sangoptager\\app.log (roterende).

Når noget driller på fars PC, kan man bede om denne ene fil i stedet for
at fejlsøge over telefonen.
"""

from __future__ import annotations

import logging
import logging.handlers
import os
import sys
import threading

from .settings import _config_dir

log = logging.getLogger("sangoptager")


def setup_logging() -> None:
    os.makedirs(_config_dir(), exist_ok=True)
    handler = logging.handlers.RotatingFileHandler(
        os.path.join(_config_dir(), "app.log"),
        maxBytes=500_000,
        backupCount=2,
        encoding="utf-8",
    )
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)-7s %(message)s")
    )
    log.addHandler(handler)
    log.setLevel(logging.INFO)

    # Uventede fejl skal i loggen i stedet for at forsvinde — også fra tråde
    # og Qt: med console=False findes stderr ikke på fars PC
    def excepthook(exc_type, exc, tb):
        log.critical("Uventet fejl", exc_info=(exc_type, exc, tb))
        sys.__excepthook__(exc_type, exc, tb)

    sys.excepthook = excepthook

    def thread_excepthook(args):
        name = args.thread.name if args.thread else "?"
        log.critical("Uventet fejl i tråden %s", name,
                     exc_info=(args.exc_type, args.exc_value,
                               args.exc_traceback))

    threading.excepthook = thread_excepthook
    _install_qt_handler()


def _install_qt_handler() -> None:
    try:
        from PySide6.QtCore import QtMsgType, qInstallMessageHandler
    except ImportError:
        return

    levels = {
        QtMsgType.QtWarningMsg: logging.WARNING,
        QtMsgType.QtCriticalMsg: logging.ERROR,
        QtMsgType.QtFatalMsg: logging.CRITICAL,
    }

    def qt_handler(msg_type, _context, message):
        level = levels.get(msg_type)
        if level is not None:
            log.log(level, "Qt: %s", message)

    qInstallMessageHandler(qt_handler)
