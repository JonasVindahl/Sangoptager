import math
import os
import shutil
import struct
import wave

import pytest

from sangoptager.audio.mixdown import balance_gains, mixdown

pytestmark = pytest.mark.skipif(
    shutil.which("ffmpeg") is None, reason="ffmpeg ikke installeret"
)


def _write_sine(path, freq, rate, seconds=1.0, channels=1):
    frames = int(rate * seconds)
    with wave.open(path, "wb") as wf:
        wf.setnchannels(channels)
        wf.setsampwidth(2)
        wf.setframerate(rate)
        for i in range(frames):
            val = int(0.4 * 32767 * math.sin(2 * math.pi * freq * i / rate))
            wf.writeframes(struct.pack("<h", val) * channels)


def test_balance_gains_neutral_is_unity():
    mic, loop = balance_gains(0.5)
    assert mic == pytest.approx(1.0)
    assert loop == pytest.approx(1.0)


def test_balance_gains_extremes():
    mic, loop = balance_gains(1.0)
    assert loop == pytest.approx(0.0)
    assert mic > 1.0
    mic, loop = balance_gains(0.0)
    assert mic == pytest.approx(0.0)


def test_mix_two_tracks_different_rates(tmp_path):
    mic = str(tmp_path / "mic.wav")
    loop = str(tmp_path / "melodi.wav")
    out = str(tmp_path / "ud.mp3")
    _write_sine(mic, 440, 44100)
    _write_sine(loop, 330, 48000, channels=2)

    result = mixdown(mic, loop, out, balance=0.5)
    assert os.path.getsize(result) > 1000
    # WAV-filerne må ikke være rørt
    assert os.path.isfile(mic) and os.path.isfile(loop)


def test_mix_single_track(tmp_path):
    mic = str(tmp_path / "mic.wav")
    out = str(tmp_path / "ud.mp3")
    _write_sine(mic, 440, 44100)
    assert os.path.getsize(mixdown(mic, None, out)) > 1000


def test_mix_without_normalize(tmp_path):
    mic = str(tmp_path / "mic.wav")
    out = str(tmp_path / "ud.mp3")
    _write_sine(mic, 440, 44100)
    assert os.path.getsize(mixdown(mic, None, out, normalize=False)) > 1000


def test_normalize_lifts_quiet_recording(tmp_path):
    """En meget stille optagelse skal komme ud væsentligt kraftigere."""
    import subprocess

    mic = str(tmp_path / "mic.wav")
    _write_sine(mic, 440, 44100, seconds=3.0)

    # Dæmp kildesignalet kraftigt (-30 dB) og mix med/uden normalisering
    quiet = str(tmp_path / "quiet.wav")
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", mic,
                    "-af", "volume=-30dB", quiet], check=True)

    def mean_volume(path):
        out = subprocess.run(
            ["ffmpeg", "-i", path, "-af", "volumedetect", "-f", "null", "-"],
            capture_output=True, text=True,
        ).stderr
        for line in out.splitlines():
            if "mean_volume" in line:
                return float(line.split("mean_volume:")[1].split("dB")[0])
        raise AssertionError("volumedetect gav intet resultat")

    raw = str(tmp_path / "raw.mp3")
    norm = str(tmp_path / "norm.mp3")
    mixdown(quiet, None, raw, normalize=False)
    mixdown(quiet, None, norm, normalize=True)
    assert mean_volume(norm) > mean_volume(raw) + 10  # mindst 10 dB løft


def test_mix_no_tracks_raises(tmp_path):
    from sangoptager.audio.mixdown import MixdownError

    with pytest.raises(MixdownError):
        mixdown(None, None, str(tmp_path / "ud.mp3"))


def test_full_pipeline_mix_then_tag(tmp_path):
    """End-to-end: mix → læg i månedsmappe → retag hele mappen."""
    import datetime

    from mutagen.mp3 import MP3

    from sangoptager.library import album_folder, build_filename, retag_folder

    mic = str(tmp_path / "mic.wav")
    _write_sine(mic, 440, 44100)

    when = datetime.datetime(2026, 7, 30, 12, 0, 0)
    dest_dir = album_folder(str(tmp_path / "sync"), when)
    os.makedirs(dest_dir)
    dest = os.path.join(dest_dir, build_filename("Testsangen", when))

    mixdown(mic, None, dest)
    total = retag_folder(dest_dir, "2026-07", artist="Far")

    assert total == 1
    audio = MP3(dest)
    assert str(audio.tags["TIT2"]) == "Testsangen"
    assert str(audio.tags["TALB"]) == "2026-07"
    assert str(audio.tags["TPE1"]) == "Far"
    assert str(audio.tags["TRCK"]) == "1/1"
