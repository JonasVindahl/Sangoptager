"""Selv-opdatering via GitHub Releases.

Flow: tjek /releases/latest anonymt → hvis nyere version findes, download
zippen, pak den ud i temp, og kør en updater-bat der venter på at appen
lukker, spejler den nye mappe oven i installationen og genstarter appen.

Kun aktiv i den frosne Windows-udgave (PyInstaller) — under udvikling gør
modulet ingenting.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
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
from .settings import _config_dir

REPO = "JonasVindahl/Sangoptager"
ASSET_NAME = "Sangoptager-windows.zip"
CHECKSUM_NAME = ASSET_NAME + ".sha256"
API_LATEST = f"https://api.github.com/repos/{REPO}/releases/latest"
RELEASES_PAGE = f"https://github.com/{REPO}/releases/latest"
EXE_NAME = "Sangoptager.exe"

_HEADERS = {
    "Accept": "application/vnd.github+json",
    "User-Agent": f"Sangoptager/{__version__}",
}

_SHA256_PAT = re.compile(r"\b([0-9a-fA-F]{64})\b")


@dataclass
class UpdateInfo:
    tag: str
    url: str
    size: int
    sha256_url: str | None = None  # checksum-asset; None på ældre releases


def install_dir() -> str:
    """Mappen appen faktisk kører fra — dét er den, der opdateres, uanset
    hvor brugeren har lagt den. Genvejen på skrivebordet peger på den samme
    exe bagefter, så den bliver ved med at virke."""
    return os.path.dirname(sys.executable)


def can_self_update() -> bool:
    return sys.platform == "win32" and getattr(sys, "frozen", False)


def install_dir_writable() -> bool:
    """Kan vi overhovedet udskifte appen dér, hvor den ligger?

    Ligger den i Program Files, kan updateren ikke skrive — så er det bedre
    at sige det med det samme end at hente hele zippen forgæves.
    """
    target = install_dir()
    try:
        probe = os.path.join(target, ".skrivetest")
        with open(probe, "w"):
            pass
        os.remove(probe)
        return True
    except OSError:
        log.warning("Installationsmappen kan ikke skrives: %s", target)
        return False


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
    assets = {a.get("name"): a for a in data.get("assets", [])}
    asset = assets.get(ASSET_NAME)
    if asset is None:
        return None
    checksum = assets.get(CHECKSUM_NAME)
    return UpdateInfo(
        tag=tag, url=asset["browser_download_url"],
        size=int(asset.get("size", 0)),
        sha256_url=checksum["browser_download_url"] if checksum else None,
    )


def parse_sha256(text: str) -> str | None:
    """Første SHA256-hash i en checksum-fil ('abc123…  filnavn')."""
    match = _SHA256_PAT.search(text)
    return match.group(1).lower() if match else None


def _pending_marker_path() -> str:
    return os.path.join(_config_dir(), "opdatering_ventende.json")


def mark_update_pending(tag: str) -> None:
    """Notér hvilken version updateren er ved at installere.

    Ved næste opstart afsløres det, om udskiftningen faktisk skete — ellers
    ville en updater, der fejler i tavshed, bare tilbyde den samme opdatering
    igen og igen.
    """
    try:
        with open(_pending_marker_path(), "w", encoding="utf-8") as fh:
            json.dump({"tag": tag, "fra": __version__}, fh)
    except OSError as exc:
        log.warning("Kunne ikke skrive opdaterings-markør: %s", exc)


def take_pending_update() -> dict | None:
    """Læs og fjern markøren fra sidste opdateringsforsøg."""
    path = _pending_marker_path()
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return None
    try:
        os.remove(path)
    except OSError:
        pass
    return data if isinstance(data, dict) and data.get("tag") else None


def update_took_effect(marker: dict, current: str = __version__) -> bool:
    """Blev den ventende opdatering rent faktisk installeret?

    True når den kørende version er nået op på (eller forbi) den ventede —
    altså når exe'en faktisk blev udskiftet.
    """
    try:
        return _parse_version(current) >= _parse_version(marker["tag"])
    except (ValueError, KeyError, TypeError):
        return False


def file_sha256(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        while chunk := fh.read(1 << 20):
            digest.update(chunk)
    return digest.hexdigest()


def build_updater_bat(new_dir: str, install_dir: str, pid: int,
                      tmp_root: str, error_log: str) -> str:
    """Indholdet af updater-scriptet: vent på appen, kopiér filer, genstart.

    Ventetjekket matcher på exe-navnet (ikke PID-tallet, som ville substring-
    matche fremmede PID'er). Kun appens egen _internal-mappe spejles med /MIR;
    roden kopieres oveni, så filer brugeren selv har lagt i mappen aldrig
    slettes. Fejler robocopy (exitkode >= 8), logges det, temp-mappen bevares
    til fejlsøgning, og den gamle app genstartes i stedet."""
    # Bat'en kører altid på Windows — join med backslash uanset hvilken
    # platform den blev skrevet på (testene kører også på macOS/Linux)
    exe_path = install_dir.rstrip("\\/") + "\\" + EXE_NAME
    return f"""@echo off
:wait
tasklist /FI "PID eq {pid}" 2>nul | find /I "{EXE_NAME}" >nul
if not errorlevel 1 (
    timeout /t 1 /nobreak >nul
    goto wait
)
robocopy "{new_dir}" "{install_dir}" /E /XD _internal /R:10 /W:1 >nul
if errorlevel 8 goto fejl
if exist "{new_dir}\\_internal" (
    robocopy "{new_dir}\\_internal" "{install_dir}\\_internal" /MIR /R:10 /W:1 >nul
    if errorlevel 8 goto fejl
)
if not exist "{exe_path}" goto fejl
start "" "{exe_path}"
timeout /t 2 /nobreak >nul
rmdir /s /q "{tmp_root}"
exit /b

:fejl
echo %date% %time% Opdatering fejlede - se {tmp_root} >> "{error_log}"
if exist "{exe_path}" start "" "{exe_path}"
exit /b
"""


def launch_updater(new_dir: str, tmp_root: str) -> None:
    """Starter updater-bat'en løsrevet fra appen — kald quit() lige efter."""
    target = install_dir()
    error_log = os.path.join(_config_dir(), "opdatering_fejl.log")
    bat_path = os.path.join(tmp_root, "opdater.bat")
    with open(bat_path, "w", encoding="ascii", errors="replace") as fh:
        fh.write(build_updater_bat(new_dir, target, os.getpid(),
                                   tmp_root, error_log))

    DETACHED_PROCESS = 0x00000008
    CREATE_NO_WINDOW = 0x08000000
    subprocess.Popen(
        ["cmd", "/c", bat_path],
        creationflags=DETACHED_PROCESS | CREATE_NO_WINDOW,
        close_fds=True,
    )
    log.info("Updater startet: %s → %s", new_dir, target)


class UpdateCheckWorker(QThread):
    """Tjek for ny version.

    found udsendes kun ved nyere version — så det automatiske tjek ved
    opstart kan nøjes med den og forblive tavst. up_to_date og failed
    bruges af det manuelle tjek, der skal svare uanset udfaldet.
    """

    found = Signal(object)      # UpdateInfo
    up_to_date = Signal()
    failed = Signal(str)

    def run(self):
        try:
            info = check_for_update()
        except Exception as exc:  # offline/ratelimit — stilhed, prøv næste start
            log.info("Opdaterings-tjek sprang over: %s", exc)
            self.failed.emit(str(exc))
            return
        if info is not None:
            # Begge sider af sammenligningen i loggen: så kan man altid se,
            # om appen faktisk kører den version, den tror
            log.info("Ny version fundet: %s (kører selv v%s, %d MB)",
                     info.tag, __version__, info.size // 1_048_576)
            self.found.emit(info)
        else:
            log.info("Opdaterings-tjek: ingen nyere version (kører v%s)",
                     __version__)
            self.up_to_date.emit()


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

            if total and done != total:
                raise RuntimeError(
                    f"Ufuldstændig download ({done} af {total} bytes)")
            self._verify_checksum(zip_path)

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

    def _verify_checksum(self, zip_path: str) -> None:
        """Sammenlign zip'en med releasens .sha256-asset (hvis den findes)."""
        if self._info.sha256_url is None:
            log.info("Release har ingen checksum-fil — springer verifikation over")
            return
        req = urllib.request.Request(self._info.sha256_url, headers=_HEADERS)
        with urllib.request.urlopen(req, timeout=30) as resp:
            expected = parse_sha256(resp.read(4096).decode("ascii", "replace"))
        if expected is None:
            raise RuntimeError("Checksum-filen på releasen kunne ikke læses")
        actual = file_sha256(zip_path)
        if actual != expected:
            raise RuntimeError(
                "Opdateringen bestod ikke integritetstjekket (SHA256-mismatch) "
                "— installerer IKKE. Prøv igen senere."
            )
        log.info("Checksum verificeret: %s", expected)

    @staticmethod
    def _find_app_dir(root: str) -> str | None:
        for dirpath, _dirnames, filenames in os.walk(root):
            if EXE_NAME in filenames:
                return dirpath
        return None
