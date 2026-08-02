import pytest

from sangoptager.update import (
    EXE_NAME,
    build_updater_bat,
    file_sha256,
    is_newer,
    parse_sha256,
)


@pytest.mark.parametrize("remote,current,expected", [
    ("v1.2.0", "1.1.0", True),
    ("v1.1.1", "1.1.0", True),
    ("2.0.0", "1.9.9", True),
    ("v1.1.0", "1.1.0", False),
    ("v1.0.9", "1.1.0", False),
    ("v1.10.0", "1.9.0", True),     # numerisk, ikke leksikografisk
    ("ugyldig-tag", "1.1.0", False),
    ("", "1.1.0", False),
])
def test_is_newer(remote, current, expected):
    assert is_newer(remote, current) is expected


def _bat(install_dir=r"C:\Sangoptager"):
    return build_updater_bat(r"C:\Temp\ny", install_dir, 4242, r"C:\Temp\up",
                             r"C:\Users\Far\AppData\Roaming\Sangoptager\fejl.log")


def test_updater_bat_contents():
    bat = _bat()
    assert "PID eq 4242" in bat
    assert EXE_NAME in bat
    assert 'rmdir /s /q "C:\\Temp\\up"' in bat
    # Genstart skal ske FØR temp-mappen ryddes
    assert bat.index("start") < bat.index("rmdir")


def test_updater_waits_on_exe_name_not_pid_substring():
    """find på PID-tallet ville også matche PID 42421 — der skal matches
    på exe-navnet, som tasklist viser i samme linje."""
    bat = _bat()
    assert f'find /I "{EXE_NAME}"' in bat
    assert 'find "4242"' not in bat


def test_updater_mirrors_only_internal_dir():
    """/MIR sletter alt der ikke er i kilden — det må kun ramme appens egen
    _internal-mappe, aldrig hele installationsmappen med brugerens filer."""
    bat = _bat()
    mir_lines = [ln for ln in bat.splitlines() if "/MIR" in ln]
    assert len(mir_lines) == 1
    assert "_internal" in mir_lines[0]
    # Roden kopieres additivt og springer _internal over
    assert 'robocopy "C:\\Temp\\ny" "C:\\Sangoptager" /E /XD _internal' in bat


def test_updater_falls_back_to_old_app_on_failure():
    bat = _bat()
    assert "if errorlevel 8 goto fejl" in bat
    fejl = bat[bat.index(":fejl"):]
    assert "start" in fejl          # den gamle app startes igen
    assert "rmdir" not in fejl      # temp bevares til fejlsøgning


def test_updater_targets_actual_install_dir():
    """Appen kan ligge hvor som helst — bat'en skal pege på den mappe, den
    faktisk kører fra, så genvejen på skrivebordet stadig virker bagefter."""
    bat = _bat(install_dir=r"D:\Programmer\Sangoptager")
    assert f'start "" "D:\\Programmer\\Sangoptager\\{EXE_NAME}"' in bat
    assert "C:\\Sangoptager" not in bat


@pytest.mark.parametrize("text,expected", [
    ("a" * 64 + "  Sangoptager-windows.zip", "a" * 64),
    ("A" * 64, "a" * 64),                       # normaliseres til småt
    ("Sangoptager-windows.zip: " + "b" * 64, "b" * 64),
    ("ikke en hash", None),
    ("c" * 63, None),                           # for kort
])
def test_parse_sha256(text, expected):
    assert parse_sha256(text) == expected


def test_file_sha256_matches_hashlib(tmp_path):
    import hashlib

    path = tmp_path / "fil.bin"
    data = b"sangoptager" * 1000
    path.write_bytes(data)
    assert file_sha256(str(path)) == hashlib.sha256(data).hexdigest()
