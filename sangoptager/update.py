"""Selv-opdatering via GitHub Releases.

Flow: tjek /releases/latest anonymt → hvis nyere version findes, download
zippen, pak den ud i temp, og kør en updater-bat der venter på at appen
lukker, spejler den nye mappe oven i installationen og genstarter appen.

Kun aktiv i den frosne Windows-udgave (PyInstaller) — under udvikling gør
modulet ingenting.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import urllib.request
import zipfile
from dataclasses import dataclass

from PySide6.QtCore import QThread, Signal

from . import __version__
from .logsetup import log

REPO = "JonasVindahl/Sangoptager"
ASSET_NAME = "Sangoptager-windows.zip"
API_LATEST = f"https://api.github.com/repos/{REPO}/releases/latest"
EXE_NAME = "Sangoptager.exe"

_HEADERS = {
    "Accept": "application/vnd.github+json",
    "User-Agent": f"Sangoptager/{__version__}",
}


@dataclass
class UpdateInfo:
    tag: str
    url: str
    size: int


def can_self_update() -> bool:
    return sys.platform == "win32" and getattr(sys, "frozen", False)


def _parse_version(tag: str) -> tuple[int, ...]:
    parts = tag.strip().lstrip("vV").split(".")
    return tuple(int(p) for p in parts)


def is_newer(remote_tag: str, current: str = __version__) -> bool:
    try:
        return _parse_version(remote_tag) > _parse_version(current)
    except ValueError:
        return False


def check_for_update(current: str = __version__,
                     timeout: float = 8.0) -> UpdateInfo | None:
    """Returnerer UpdateInfo hvis en nyere version med Windows-zip findes."""
    req = urllib.request.Request(API_LATEST, headers=_HEADERS)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = json.load(resp)
    tag = data.get("tag_name", "")
    if not is_newer(tag, current):
        return None
    for asset in data.get("assets", []):
        if asset.get("name") == ASSET_NAME:
            return UpdateInfo(tag=tag, url=asset["browser_download_url"],
                              size=int(asset.get("size", 0)))
    return None


def build_updater_bat(new_dir: str, install_dir: str, pid: int,
                      tmp_root: str) -> str:
    """Indholdet af updater-scriptet: vent på appen, spejl filer, genstart."""
    return f"""@echo off
:wait
tasklist /FI "PID eq {pid}" 2>nul | find "{pid}" >nul
if not errorlevel 1 (
    timeout /t 1 /nobreak >nul
    goto wait
)
robocopy "{new_dir}" "{install_dir}" /MIR /R:10 /W:1 >nul
start "" "{os.path.join(install_dir, EXE_NAME)}"
timeout /t 2 /nobreak >nul
rmdir /s /q "{tmp_root}"
"""


def launch_updater(new_dir: str, tmp_root: str) -> None:
    """Starter updater-bat'en løsrevet fra appen — kald quit() lige efter."""
    install_dir = os.path.dirname(sys.executable)
    bat_path = os.path.join(tmp_root, "opdater.bat")
    with open(bat_path, "w", encoding="ascii", errors="replace") as fh:
        fh.write(build_updater_bat(new_dir, install_dir, os.getpid(), tmp_root))

    DETACHED_PROCESS = 0x00000008
    CREATE_NO_WINDOW = 0x08000000
    subprocess.Popen(
        ["cmd", "/c", bat_path],
        creationflags=DETACHED_PROCESS | CREATE_NO_WINDOW,
        close_fds=True,
    )
    log.info("Updater startet: %s → %s", new_dir, install_dir)


class UpdateCheckWorker(QThread):
    """Baggrundstjek ved opstart. found udsendes kun ved nyere version."""

    found = Signal(object)  # UpdateInfo

    def run(self):
        try:
            info = check_for_update()
        except Exception as exc:  # offline/ratelimit — stilhed, prøv næste start
            log.info("Opdaterings-tjek sprang over: %s", exc)
            return
        if info is not None:
            log.info("Ny version fundet: %s (%d MB)",
                     info.tag, info.size // 1_048_576)
            self.found.emit(info)


class UpdateDownloadWorker(QThread):
    """Downloader og udpakker den nye version. ready → kald launch_updater."""

    progress = Signal(int)          # procent 0..100
    ready = Signal(str, str)        # (new_dir, tmp_root)
    failed = Signal(str)

    def __init__(self, info: UpdateInfo, parent=None):
        super().__init__(parent)
        self._info = info

    def run(self):
        try:
            tmp_root = os.path.join(tempfile.gettempdir(), "sangoptager_update")
            shutil.rmtree(tmp_root, ignore_errors=True)
            os.makedirs(tmp_root)

            zip_path = os.path.join(tmp_root, "update.zip")
            req = urllib.request.Request(self._info.url, headers=_HEADERS)
            with urllib.request.urlopen(req, timeout=60) as resp, \
                    open(zip_path, "wb") as out:
                total = self._info.size or int(
                    resp.headers.get("Content-Length") or 0)
                done = 0
                while True:
                    chunk = resp.read(131072)
                    if not chunk:
                        break
                    out.write(chunk)
                    done += len(chunk)
                    if total:
                        self.progress.emit(int(done * 100 / total))

            extract_dir = os.path.join(tmp_root, "ny")
            with zipfile.ZipFile(zip_path) as zf:
                zf.extractall(extract_dir)

            new_dir = self._find_app_dir(extract_dir)
            if new_dir is None:
                raise RuntimeError(f"{EXE_NAME} mangler i den hentede zip")
            self.ready.emit(new_dir, tmp_root)
        except Exception as exc:
            log.error("Opdatering fejlede under download/udpakning: %s", exc)
            self.failed.emit(str(exc))

    @staticmethod
    def _find_app_dir(root: str) -> str | None:
        for dirpath, _dirnames, filenames in os.walk(root):
            if EXE_NAME in filenames:
                return dirpath
        return None
