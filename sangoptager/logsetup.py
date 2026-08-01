"""Logfil til fjerndiagnose: %APPDATA%\\Sangoptager\\app.log (roterende).

Når noget driller på fars PC, kan man bede om denne ene fil i stedet for
at fejlsøge over telefonen.
"""

from __future__ import annotations

import logging
import logging.handlers
import os
import sys

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

    # Uventede fejl skal i loggen i stedet for at forsvinde
    def excepthook(exc_type, exc, tb):
        log.critical("Uventet fejl", exc_info=(exc_type, exc, tb))
        sys.__excepthook__(exc_type, exc, tb)

    sys.excepthook = excepthook
