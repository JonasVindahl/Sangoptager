"""Arkiv af de rå spor efter gem — appens "sorte boks".

Lyder en gemt optagelse skæv, kan den re-mixes fra de rå spor her (med
justeret offset/balance) i stedet for at skulle synges om. Arkivet beskæres
automatisk: de nyeste 10 optagelser, højst 14 dage gamle.
"""

from __future__ import annotations

import json
import os
import shutil
import time

from .logsetup import log
from .settings import _config_dir

KEEP_COUNT = 10
KEEP_DAYS = 14


def raw_archive_dir() -> str:
    return os.path.join(_config_dir(), "raa_spor")


def archive_recording(mic_path: str | None, loop_path: str | None,
                      name: str, info: dict) -> str | None:
    """Flyt rå spor + info.json til arkivet. Returnerer mappen (eller None)."""
    tracks = [p for p in (mic_path, loop_path) if p and os.path.isfile(p)]
    if not tracks:
        return None
    dest = os.path.join(raw_archive_dir(), name)
    try:
        os.makedirs(dest, exist_ok=True)
        for path in tracks:
            shutil.move(path, os.path.join(dest, os.path.basename(path)))
        with open(os.path.join(dest, "info.json"), "w", encoding="utf-8") as fh:
            json.dump(info, fh, indent=2, ensure_ascii=False)
        return dest
    except OSError as exc:
        log.warning("Kunne ikke arkivere rå spor: %s", exc)
        return None


def prune_archive() -> None:
    """Behold de nyeste KEEP_COUNT mapper, og intet ældre end KEEP_DAYS."""
    root = raw_archive_dir()
    if not os.path.isdir(root):
        return
    entries = []
    for entry in os.listdir(root):
        path = os.path.join(root, entry)
        if os.path.isdir(path):
            entries.append((os.path.getmtime(path), path))
    entries.sort(reverse=True)

    cutoff = time.time() - KEEP_DAYS * 86400
    for index, (mtime, path) in enumerate(entries):
        if index >= KEEP_COUNT or mtime < cutoff:
            shutil.rmtree(path, ignore_errors=True)
            log.info("Rå-spor-arkiv beskåret: %s", os.path.basename(path))
