import datetime
import os

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
