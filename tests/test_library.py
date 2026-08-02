import datetime
import os
import shutil
import subprocess

import pytest

from sangoptager.library import (
    album_folder,
    build_filename,
    invert_datetime,
    parse_filename,
    sanitize_title,
    uninvert_datetime,
)


def test_invert_roundtrip():
    iso = "2024-03-22_14-55-17"
    assert uninvert_datetime(invert_datetime(iso)) == iso


def test_invert_matches_old_script_example():
    # Eksempel fra det gamle scripts docstring (år 2024 → 7975)
    assert invert_datetime("2024-03-22_14-55-17") == "7975-96-77_85-44-82"


def test_inverted_sorts_newest_first():
    older = invert_datetime("2024-03-22_14-55-17")
    newer = invert_datetime("2026-07-30_09-00-00")
    assert sorted([older, newer])[0] == newer


def test_parse_inverted_format():
    inv = invert_datetime("2024-03-22_14-55-17")
    title, iso = parse_filename(f"{inv}_Den danske sang.mp3")
    assert title == "Den danske sang"
    assert iso == "2024-03-22_14-55-17"


def test_parse_new_format():
    title, iso = parse_filename("22-03-2024_14-55-17_Den danske sang.mp3")
    assert title == "Den danske sang"
    assert iso == "2024-03-22_14-55-17"


def test_parse_old_format():
    title, iso = parse_filename("Den danske sang_22-03-2024_14-55-17.mp3")
    assert title == "Den danske sang"
    assert iso == "2024-03-22_14-55-17"


def test_parse_title_with_underscores():
    title, iso = parse_filename("22-03-2024_14-55-17_En_sang_med_bundstreger.mp3")
    assert title == "En_sang_med_bundstreger"
    assert iso == "2024-03-22_14-55-17"


def test_parse_rejects_garbage():
    assert parse_filename("bare-en-fil.mp3") is None
    assert parse_filename("kort_navn.mp3") is None


def test_build_filename_roundtrip():
    when = datetime.datetime(2026, 7, 30, 12, 34, 56)
    name = build_filename("Min nye sang", when)
    title, iso = parse_filename(name)
    assert title == "Min nye sang"
    assert iso == "2026-07-30_12-34-56"


def test_sanitize_keeps_danish_letters():
    assert sanitize_title("Blæsten går frisk over Limfjordens vande") == (
        "Blæsten går frisk over Limfjordens vande"
    )


def test_sanitize_removes_unsafe_and_separator_chars():
    assert sanitize_title('En/sang: med_"grimme"*tegn?') == "En sang med grimme tegn"


def test_album_folder():
    when = datetime.datetime(2026, 7, 30, 12, 0, 0)
    assert album_folder("/rod", when) == os.path.join("/rod", "2026-07")


def test_collect_titles(tmp_path):
    from sangoptager.library import collect_titles, invert_datetime

    for month, files in {
        "2026-06": [
            f"{invert_datetime('2026-06-01_10-00-00')}_Den danske sang.mp3",
            "22-03-2024_14-55-17_Gamle Ole.mp3",       # nyt format
            "ikke-en-sangfil.mp3",                      # kan ikke parses
            "noter.txt",                                # ikke mp3
        ],
        "2026-07": [
            # Dublet på tværs af måneder — må kun optræde én gang
            f"{invert_datetime('2026-07-15_09-30-00')}_Den danske sang.mp3",
            f"{invert_datetime('2026-07-20_11-00-00')}_askepot.mp3",
        ],
    }.items():
        folder = tmp_path / month
        folder.mkdir()
        for name in files:
            (folder / name).touch()

    titles = collect_titles(str(tmp_path))
    assert titles == ["askepot", "Den danske sang", "Gamle Ole"]


def test_collect_titles_missing_root():
    from sangoptager.library import collect_titles

    assert collect_titles("/findes/ikke/nogen/steder") == []


def test_collect_titles_ignores_sync_conflicts(tmp_path):
    """Syncthings konfliktkopier er ikke sange og må ikke i autocomplete."""
    from sangoptager.library import collect_titles

    folder = tmp_path / "2026-07"
    folder.mkdir()
    inv = invert_datetime("2026-07-15_09-30-00")
    (folder / f"{inv}_Den danske sang.mp3").touch()
    (folder / f"{inv}_Den danske sang.sync-conflict-20260715-093000-ABCDEFG.mp3").touch()

    assert collect_titles(str(tmp_path)) == ["Den danske sang"]


needs_ffmpeg = pytest.mark.skipif(
    shutil.which("ffmpeg") is None, reason="ffmpeg ikke installeret"
)


def _make_mp3(path, seconds=1):
    subprocess.run(
        ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
         "-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}",
         "-codec:a", "libmp3lame", "-b:a", "64k", str(path)],
        check=True,
    )


@needs_ffmpeg
def test_retag_folder_newest_is_track_one(tmp_path):
    """Kernekravet i biblioteket: nyeste optagelse skal altid være track 1,
    så afspilleren viser de nye sange øverst."""
    from mutagen.mp3 import MP3

    from sangoptager.library import retag_folder

    times = ["2026-07-01_10-00-00", "2026-07-20_18-30-00", "2026-07-10_12-00-00"]
    for iso in times:
        _make_mp3(tmp_path / f"{invert_datetime(iso)}_Sang {iso[8:10]}.mp3")

    total = retag_folder(str(tmp_path), "2026-07", artist="Far")
    assert total == 3

    tracks = {}
    for name in os.listdir(tmp_path):
        audio = MP3(str(tmp_path / name))
        title = str(audio.tags["TIT2"].text[0])
        tracks[title] = str(audio.tags["TRCK"].text[0])
        assert str(audio.tags["TALB"].text[0]) == "2026-07"
        assert str(audio.tags["TPE1"].text[0]) == "Far"

    assert tracks["Sang 20"] == "1/3"   # nyeste = 1
    assert tracks["Sang 10"] == "2/3"
    assert tracks["Sang 01"] == "3/3"   # ældste sidst


@needs_ffmpeg
def test_retag_folder_skips_unchanged_files(tmp_path):
    """Andet gennemløb må ikke røre filer, hvis tags allerede er rigtige —
    ellers re-synkroniserer Syncthing hele måneden ved hvert gem."""
    from sangoptager.library import retag_folder

    for iso in ["2026-07-01_10-00-00", "2026-07-20_18-30-00"]:
        _make_mp3(tmp_path / f"{invert_datetime(iso)}_Sang {iso[8:10]}.mp3")

    retag_folder(str(tmp_path), "2026-07", artist="Far")
    before = {n: os.stat(tmp_path / n).st_mtime_ns for n in os.listdir(tmp_path)}

    retag_folder(str(tmp_path), "2026-07", artist="Far")
    after = {n: os.stat(tmp_path / n).st_mtime_ns for n in os.listdir(tmp_path)}
    assert before == after

    # …men et ændret kunstnernavn skal stadig slå igennem
    retag_folder(str(tmp_path), "2026-07", artist="Bedstefar")
    changed = {n: os.stat(tmp_path / n).st_mtime_ns for n in os.listdir(tmp_path)}
    assert changed != after


@needs_ffmpeg
def test_retag_folder_ignores_sync_conflicts(tmp_path):
    """En konfliktkopi må hverken tælle med i totalen eller få tracknummer."""
    from mutagen.mp3 import MP3

    from sangoptager.library import retag_folder

    inv = invert_datetime("2026-07-20_18-30-00")
    _make_mp3(tmp_path / f"{inv}_Rigtig sang.mp3")
    conflict = tmp_path / f"{inv}_Rigtig sang.sync-conflict-20260720-183000-XYZ.mp3"
    _make_mp3(conflict)

    assert retag_folder(str(tmp_path), "2026-07", artist="Far") == 1
    # Konfliktkopien beholder ffmpegs egne tags, men får ingen af vores
    conflict_tags = MP3(str(conflict)).tags
    assert conflict_tags is None or "TRCK" not in conflict_tags
    assert conflict_tags is None or "TIT2" not in conflict_tags


def test_unique_path_avoids_overwrite(tmp_path):
    from sangoptager.library import unique_path

    target = tmp_path / "sang.mp3"
    assert unique_path(str(target)) == str(target)

    target.touch()
    assert unique_path(str(target)) == str(tmp_path / "sang (2).mp3")

    (tmp_path / "sang (2).mp3").touch()
    assert unique_path(str(target)) == str(tmp_path / "sang (3).mp3")
