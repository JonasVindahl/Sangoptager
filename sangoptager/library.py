"""Navngivning, filnavns-parsing og ID3-tagging af sangbiblioteket.

Logikken er porteret uændret fra det gamle obs_recorder.pyw, så nye filer er
100% kompatible med det eksisterende bibliotek (Nextcloud/TrueNAS):

  - Filnavn: INVDATO_INVTID_Titel.mp3 hvor dato/tid er "inverteret"
    (9999-år, 99-måned, ...) så nyeste optagelse sorterer først alfabetisk.
  - Mappestruktur: <rod>/ÅÅÅÅ-MM/  (én mappe pr. måned = ét album)
  - Tags: TIT2=titel, TALB=ÅÅÅÅ-MM, TPE1=kunstner, TDRC=dato,
    TRCK=tracknr hvor nyeste = 1/total.
"""

from __future__ import annotations

import datetime
import os
import re

from mutagen.id3 import ID3, TALB, TDRC, TIT2, TPE1, TRCK
from mutagen.mp3 import MP3

from .logsetup import log

DATE_PAT = re.compile(r"^(\d{2})-(\d{2})-(\d{4})$")   # DD-MM-YYYY
TIME_PAT = re.compile(r"^(\d{2})-(\d{2})-(\d{2})$")   # HH-MM-SS
INV_PAT  = re.compile(r"^(\d{4})-(\d{2})-(\d{2})$")   # inverteret (år > 2100)

# Tegn der er ulovlige i Windows-filnavne, plus "_" som er feltseparator
# i filnavnsformatet og derfor ikke må optræde i selve titlen.
_UNSAFE_CHARS = re.compile(r'[\\/:*?"<>|_]+')


def _is_sync_conflict(filename: str) -> bool:
    """Syncthings konfliktkopier må ikke behandles som sange."""
    return "sync-conflict" in filename.lower()


def sanitize_title(title: str) -> str:
    """Rens en titel så den kan indgå i et filnavn. Æøå bevares."""
    cleaned = _UNSAFE_CHARS.sub(" ", title)
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" .")
    return cleaned


def invert_datetime(iso_dt: str) -> str:
    """2024-03-22_14-55-17 → 7975-96-77_85-44-82 (nyeste sorterer først)."""
    date_part, time_part = iso_dt.split("_")
    yyyy, mm, dd = date_part.split("-")
    hh, mi, ss   = time_part.split("-")
    return (
        f"{str(9999-int(yyyy)).zfill(4)}-{str(99-int(mm)).zfill(2)}-{str(99-int(dd)).zfill(2)}"
        f"_{str(99-int(hh)).zfill(2)}-{str(99-int(mi)).zfill(2)}-{str(99-int(ss)).zfill(2)}"
    )


def uninvert_datetime(inv_dt: str) -> str:
    """7975-96-77_85-44-82 → 2024-03-22_14-55-17."""
    return invert_datetime(inv_dt)  # inverteringen er sin egen inverse


def parse_filename(filename: str):
    """Returnerer (title, iso_datetime) eller None.

    Understøtter:
      - Inverteret:  IYYY-IMM-IDD_IHH-IMI-ISS_Titel
      - Nyt:         DD-MM-YYYY_HH-MM-SS_Titel
      - Gammelt:     Titel_DD-MM-YYYY_HH-MM-SS
    """
    basename = os.path.splitext(filename)[0]
    parts    = basename.split("_")

    if len(parts) < 3:
        return None

    # Inverteret format (år > 2100)
    m = INV_PAT.match(parts[0])
    if m and TIME_PAT.match(parts[1]) and int(m.group(1)) > 2100:
        iso_dt = uninvert_datetime(f"{parts[0]}_{parts[1]}")
        title  = "_".join(parts[2:]).strip()
        return (title, iso_dt) if title else None

    # Nyt format: DD-MM-YYYY_HH-MM-SS_Titel
    m = DATE_PAT.match(parts[0])
    if m and TIME_PAT.match(parts[1]):
        day, month, year = m.group(1), m.group(2), m.group(3)
        hh, mi, ss = parts[1].split("-")
        title = "_".join(parts[2:]).strip()
        return (title, f"{year}-{month}-{day}_{hh}-{mi}-{ss}") if title else None

    # Gammelt format: Titel_DD-MM-YYYY_HH-MM-SS
    m = DATE_PAT.match(parts[-2]) if len(parts) >= 3 else None
    if m and TIME_PAT.match(parts[-1]):
        day, month, year = m.group(1), m.group(2), m.group(3)
        hh, mi, ss = parts[-1].split("-")
        title = "_".join(parts[:-2]).strip()
        return (title, f"{year}-{month}-{day}_{hh}-{mi}-{ss}") if title else None

    return None


def build_filename(title: str, when: datetime.datetime | None = None) -> str:
    """Byg det endelige (inverterede) filnavn for en ny optagelse."""
    when = when or datetime.datetime.now()
    iso_dt = when.strftime("%Y-%m-%d_%H-%M-%S")
    return f"{invert_datetime(iso_dt)}_{sanitize_title(title)}.mp3"


def album_folder(root: str, when: datetime.datetime | None = None) -> str:
    """Månedsmappen (albummet) en ny optagelse skal ligge i, f.eks. <root>/2026-07."""
    when = when or datetime.datetime.now()
    return os.path.join(root, when.strftime("%Y-%m"))


def unique_path(path: str) -> str:
    """Ledig variant af path: 'navn.mp3' → 'navn (2).mp3' hvis optaget."""
    if not os.path.exists(path):
        return path
    root, ext = os.path.splitext(path)
    n = 2
    while os.path.exists(f"{root} ({n}){ext}"):
        n += 1
    return f"{root} ({n}){ext}"


def collect_titles(root: str) -> list[str]:
    """Alle unikke sangtitler i biblioteket, sorteret — til autocomplete."""
    titles: set[str] = set()
    try:
        month_dirs = os.listdir(root)
    except OSError:
        return []
    for month in month_dirs:
        month_path = os.path.join(root, month)
        if not os.path.isdir(month_path):
            continue
        try:
            filenames = os.listdir(month_path)
        except OSError:
            continue
        for filename in filenames:
            if not filename.endswith(".mp3") or _is_sync_conflict(filename):
                continue
            result = parse_filename(filename)
            if result is not None:
                titles.add(result[0])
    return sorted(titles, key=str.casefold)


def _tags_current(audio: MP3, title: str, album: str, artist: str,
                  date_only: str, track: str) -> bool:
    """True hvis filens tags allerede er som ønsket — så skrivning (og dermed
    Syncthing/Navidrome-churn) kan springes over."""
    if audio.tags is None:
        return False

    def text(key: str) -> str | None:
        frame = audio.tags.get(key)
        return str(frame.text[0]) if frame is not None and frame.text else None

    return (text("TIT2") == title and text("TALB") == album
            and text("TPE1") == artist and text("TDRC") == date_only
            and text("TRCK") == track)


def retag_folder(folder_path: str, album_name: str, artist: str = "Far") -> int:
    """Omdøb + sæt alle tags + genberegn TRCK for hele mappen. Nyeste = track 1."""
    songs = []
    for filename in os.listdir(folder_path):
        if not filename.endswith(".mp3") or _is_sync_conflict(filename):
            continue
        result = parse_filename(filename)
        if result is None:
            continue
        title, iso_dt = result
        songs.append((filename, title, iso_dt))

    songs.sort(key=lambda x: x[2], reverse=True)
    total = len(songs)

    for track_num, (filename, title, iso_dt) in enumerate(songs, start=1):
        filepath = os.path.join(folder_path, filename)

        # Omdøb til inverteret format hvis nødvendigt
        inv          = invert_datetime(iso_dt)
        new_filename = f"{inv}_{title}.mp3"
        new_filepath = os.path.join(folder_path, new_filename)
        if filename != new_filename:
            try:
                os.rename(filepath, new_filepath)
                filepath = new_filepath
            except OSError as exc:
                log.warning("Kunne ikke omdøbe %s: %s", filename, exc)
                continue

        date_only = iso_dt[:10]
        track = f"{track_num}/{total}"
        try:
            audio = MP3(filepath, ID3=ID3)
            # Uændrede filer springes over, så Syncthing/Navidrome ikke skal
            # re-synkronisere hele måneden ved hvert gem
            if _tags_current(audio, title, album_name, artist, date_only, track):
                continue
            if audio.tags is None:
                audio.add_tags()
            audio.tags["TIT2"] = TIT2(encoding=3, text=title)
            audio.tags["TALB"] = TALB(encoding=3, text=album_name)
            audio.tags["TPE1"] = TPE1(encoding=3, text=artist)
            audio.tags["TDRC"] = TDRC(encoding=3, text=date_only)
            audio.tags["TRCK"] = TRCK(encoding=3, text=track)
            audio.save()
        except Exception as exc:
            log.warning("Kunne ikke tagge %s: %s", filepath, exc)

    return total
