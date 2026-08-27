import pytest

from sangoptager.update import file_sha256, is_newer, parse_sha256


@pytest.mark.parametrize("remote,current,expected", [
    ("v1.2.0", "1.1.0", True),
    ("v1.1.1", "1.1.0", True),
    ("2.0.0", "1.9.9", True),
    ("v1.1.0", "1.1.0", False),
    ("v1.0.9", "1.1.0", False),
    ("v1.10.0", "1.9.0", True),     # numerisk, ikke leksikografisk
    ("ugyldig-tag", "1.1.0", False),
    ("", "1.1.0", False),
    ("v1.16.0", "1.16", True),   # 2-led-tag i koden ved siden af v1.16.0 → tilbydes altid
])
def test_is_newer(remote, current, expected):
    assert is_newer(remote, current) is expected


# ── Udskiftning af installationen (i appens egen proces) ────────────────────

def _fake_install(tmp_path, version="1.0"):
    """Efterligner en PyInstaller-mappe: exe i roden, resten i _internal."""
    root = tmp_path / "install"
    (root / "_internal").mkdir(parents=True)
    (root / "Sangoptager.exe").write_text(f"exe {version}")
    (root / "_internal" / "qt.dll").write_text(f"dll {version}")
    return root


def _fake_download(tmp_path, version="2.0"):
    new = tmp_path / "ny"
    (new / "_internal").mkdir(parents=True)
    (new / "Sangoptager.exe").write_text(f"exe {version}")
    (new / "_internal" / "qt.dll").write_text(f"dll {version}")
    return new


def test_swap_replaces_files_and_keeps_old_aside(tmp_path):
    from sangoptager.update import BACKUP_SUFFIX, swap_in_new_version

    install = _fake_install(tmp_path)
    new = _fake_download(tmp_path)

    assert swap_in_new_version(str(new), str(install)) == 2
    assert (install / "Sangoptager.exe").read_text() == "exe 2.0"
    assert (install / "_internal" / "qt.dll").read_text() == "dll 2.0"
    # De gamle er flyttet til side, ikke overskrevet — det er dét, der gør at
    # filer i brug kan udskiftes på Windows
    assert (install / ("Sangoptager.exe" + BACKUP_SUFFIX)).read_text() == "exe 1.0"


def test_swap_keeps_users_own_files(tmp_path):
    """Den gamle robocopy /MIR slettede alt, der ikke var i kilden."""
    from sangoptager.update import swap_in_new_version

    install = _fake_install(tmp_path)
    (install / "mine noter.txt").write_text("vigtigt")
    swap_in_new_version(str(_fake_download(tmp_path)), str(install))
    assert (install / "mine noter.txt").read_text() == "vigtigt"


def test_swap_rolls_back_when_a_file_cannot_be_written(tmp_path, monkeypatch):
    """Halvfærdig installation er værre end ingen opdatering."""
    import shutil as _shutil

    from sangoptager.update import swap_in_new_version

    install = _fake_install(tmp_path)
    new = _fake_download(tmp_path)

    ægte_copy = _shutil.copy2
    kald = {"n": 0}

    def fejler_på_anden_fil(src, dst, *a, **kw):
        kald["n"] += 1
        if kald["n"] == 2:
            raise OSError("adgang nægtet")
        return ægte_copy(src, dst, *a, **kw)

    monkeypatch.setattr("sangoptager.update.shutil.copy2", fejler_på_anden_fil)
    with pytest.raises(OSError):
        swap_in_new_version(str(new), str(install))

    # Alt skal være som før forsøget
    assert (install / "Sangoptager.exe").read_text() == "exe 1.0"
    assert (install / "_internal" / "qt.dll").read_text() == "dll 1.0"


def test_cleanup_removes_leftovers_from_previous_update(tmp_path):
    from sangoptager.update import (
        BACKUP_SUFFIX,
        cleanup_old_versions,
        swap_in_new_version,
    )

    install = _fake_install(tmp_path)
    swap_in_new_version(str(_fake_download(tmp_path)), str(install))
    assert (install / ("Sangoptager.exe" + BACKUP_SUFFIX)).exists()

    assert cleanup_old_versions(str(install)) == 2
    assert not (install / ("Sangoptager.exe" + BACKUP_SUFFIX)).exists()
    # De nye filer må naturligvis ikke røres
    assert (install / "Sangoptager.exe").read_text() == "exe 2.0"


def test_swap_survives_a_leftover_backup(tmp_path):
    """Kunne den gamle sikkerhedskopi ikke slettes sidst, må opdateringen
    ikke gå i stå på den."""
    from sangoptager.update import BACKUP_SUFFIX, swap_in_new_version

    install = _fake_install(tmp_path)
    (install / ("Sangoptager.exe" + BACKUP_SUFFIX)).write_text("gammel rest")
    swap_in_new_version(str(_fake_download(tmp_path)), str(install))
    assert (install / "Sangoptager.exe").read_text() == "exe 2.0"


@pytest.mark.parametrize("text,expected", [
    ("a" * 64 + "  Sangoptager-windows.zip", "a" * 64),
    ("A" * 64, "a" * 64),                       # normaliseres til småt
    ("Sangoptager-windows.zip: " + "b" * 64, "b" * 64),
    ("ikke en hash", None),
    ("c" * 63, None),                           # for kort
])
def test_parse_sha256(text, expected):
    assert parse_sha256(text) == expected


@pytest.mark.parametrize("tag,current,expected", [
    ("v1.5.0", "1.5.0", True),    # exe blev udskiftet
    ("v1.5.0", "1.6.0", True),    # endnu nyere — også fint
    ("v1.5.0", "1.4.0", False),   # kører stadig den gamle → mislykkedes
    ("noget-vrøvl", "1.5.0", False),
])
def test_update_took_effect(tag, current, expected):
    from sangoptager.update import update_took_effect

    assert update_took_effect({"tag": tag}, current) is expected


def test_pending_marker_roundtrip(tmp_path, monkeypatch):
    """Markøren skal kunne skrives, læses én gang og derefter være væk —
    ellers ville en gammel markør advare om en fejl der ikke skete."""
    import sangoptager.update as upd

    monkeypatch.setattr(upd, "_config_dir", lambda: str(tmp_path))
    assert upd.take_pending_update() is None      # ingen markør endnu

    upd.mark_update_pending("v1.5.0")
    marker = upd.take_pending_update()
    assert marker is not None and marker["tag"] == "v1.5.0"

    assert upd.take_pending_update() is None      # forbrugt


def test_file_sha256_matches_hashlib(tmp_path):
    import hashlib

    path = tmp_path / "fil.bin"
    data = b"sangoptager" * 1000
    path.write_bytes(data)
    assert file_sha256(str(path)) == hashlib.sha256(data).hexdigest()
