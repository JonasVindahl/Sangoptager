"""Selv-opdatering via GitHub Releases.

Flow: tjek /releases/latest anonymt → hvis nyere version findes, download
zippen, verificér dens checksum, pak den ud i temp, udskift installationen
og start den nye version.

Udskiftningen sker i appens EGEN proces. Tidligere blev det gjort af en
bat-fil med robocopy, men den fejlede hver eneste gang hos brugeren, mens
appens egen skrivetest i samme mappe lykkedes — mønstret for Windows'
Kontrolleret mappeadgang, som blokerer fremmede processer i bl.a. Dokumenter.
Filer i brug kan ikke overskrives, men de kan omdøbes, og det er nok.

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


# Filer der er i brug kan ikke overskrives på Windows, men de kan godt
# OMDØBES. Den gamle udgave flyttes derfor til side med dette suffiks og
# ryddes op ved næste opstart.
BACKUP_SUFFIX = ".gammel"


def swap_in_new_version(new_dir: str, target_dir: str) -> int:
    """Udskift filerne i target_dir med dem fra new_dir. Returnerer antal filer.

    Gøres i appens EGEN proces frem for via robocopy i en bat-fil. Windows'
    Kontrolleret mappeadgang beskytter bl.a. Dokumenter mod fremmede processer:
    appen selv har lov at skrive dér, mens robocopy.exe bliver blokeret — og
    det er netop det mønster, loggen viste (skrivetesten lykkedes, kopieringen
    fejlede hver gang).

    Filer i brug — exe'en og DLL'erne i _internal — omdøbes først til
    BACKUP_SUFFIX, hvilket Windows tillader, hvorefter de nye kan skrives på
    plads. Fejler noget undervejs, rulles alt tilbage, så installationen ikke
    efterlades halvfærdig.
    """
    renamed: list[tuple[str, str]] = []   # (original, backup)
    created: list[str] = []
    count = 0
    try:
        for root, _dirs, files in os.walk(new_dir):
            rel = os.path.relpath(root, new_dir)
            dest_root = target_dir if rel == "." else os.path.join(target_dir, rel)
            os.makedirs(dest_root, exist_ok=True)
            for name in files:
                src = os.path.join(root, name)
                dst = os.path.join(dest_root, name)
                if os.path.exists(dst):
                    backup = _free_backup_path(dst)
                    os.rename(dst, backup)
                    renamed.append((dst, backup))
                else:
                    created.append(dst)
                shutil.copy2(src, dst)
                count += 1
        return count
    except OSError:
        _roll_back(renamed, created)
        raise


def _free_backup_path(path: str) -> str:
    """Ledig sti at flytte den gamle fil hen til."""
    candidate = path + BACKUP_SUFFIX
    n = 2
    while os.path.exists(candidate):
        try:
            os.remove(candidate)          # rest fra en tidligere opdatering
            return candidate
        except OSError:
            candidate = f"{path}{BACKUP_SUFFIX}{n}"
            n += 1
    return candidate


def _roll_back(renamed: list[tuple[str, str]], created: list[str]) -> None:
    """Sæt installationen tilbage, som den var, efter en fejlet udskiftning."""
    for path in created:
        try:
            os.remove(path)
        except OSError:
            pass
    for original, backup in renamed:
        try:
            if os.path.exists(original):
                os.remove(original)
            os.rename(backup, original)
        except OSError:
            log.error("Kunne ikke gendanne %s fra %s", original, backup)
    log.warning("Opdatering rullet tilbage — appen kører videre som før")


def cleanup_old_versions(target_dir: str | None = None) -> int:
    """Fjern efterladte .gammel-filer fra sidste opdatering. Kaldes ved opstart,
    hvor de gamle filer ikke længere er i brug og derfor kan slettes."""
    target = target_dir or install_dir()
    removed = 0
    for root, _dirs, files in os.walk(target):
        for name in files:
            if BACKUP_SUFFIX in name:
                try:
                    os.remove(os.path.join(root, name))
                    removed += 1
                except OSError:
                    pass
    if removed:
        log.info("Ryddet %d fil(er) fra forrige version", removed)
    return removed


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


def apply_update(new_dir: str) -> int:
    """Udskift den kørende installation med den nye version og start den.

    Erstatter den tidligere bat-fil med robocopy. Den fejlede gang på gang
    hos brugeren — appen kunne skrive i mappen, men den eksterne proces kunne
    ikke, hvilket peger på Windows' Kontrolleret mappeadgang. Ved at gøre det
    her i appens egen proces undgås både den blokering og batch-filens
    faldgruber (manglende konsol, errorlevel i blokke, ventetid på PID).

    Returnerer antal udskiftede filer. Kaster OSError hvis det mislykkedes —
    installationen er da rullet tilbage, og appen kan køre videre som før.
    """
    target = install_dir()
    log.info("Udskifter installationen i %s", target)
    count = swap_in_new_version(new_dir, target)
    log.info("%d filer udskiftet", count)
    return count


def launch_new_version() -> None:
    """Start den nyudskiftede exe og lad denne proces afslutte bagefter."""
    exe = os.path.join(install_dir(), EXE_NAME)
    DETACHED = 0x00000008
    subprocess.Popen([exe], creationflags=DETACHED, close_fds=True,
                     cwd=install_dir())
    log.info("Ny version startet: %s", exe)

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
    """Downloader og udpakker den nye version. ready → kald apply_update."""

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
