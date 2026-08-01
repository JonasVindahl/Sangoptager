import pytest

from sangoptager.update import EXE_NAME, build_updater_bat, is_newer


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


def test_updater_bat_contents():
    bat = build_updater_bat(r"C:\Temp\ny", r"C:\Sangoptager", 4242, r"C:\Temp\up")
    assert 'robocopy "C:\\Temp\\ny" "C:\\Sangoptager" /MIR' in bat
    assert "PID eq 4242" in bat
    assert EXE_NAME in bat
    assert 'rmdir /s /q "C:\\Temp\\up"' in bat
    # Genstart skal ske FØR temp-mappen ryddes
    assert bat.index("start") < bat.index("rmdir")
