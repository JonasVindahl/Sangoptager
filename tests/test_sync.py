"""Tests af at mixet ALDRIG tidsforskyder sporene.

Tidligere blev de to streams' første ADC-tidsstempler trukket fra hinanden og
brugt som "startforskydning". Mikrofon og loopback er to uafhængige
PortAudio-streams, hvis tidsstempler ikke har fælles nulpunkt, så differencen
var ikke en ægte forskydning — den kunne skubbe stemmen op til et halvt sekund
væk fra melodien. Disse tests fastholder, at sporene mixes som optaget.
"""

import shutil
import struct
import subprocess
import wave

import numpy as np
import pytest

from sangoptager.audio.capture import compute_offset_ms, compute_start_offset_ms
from sangoptager.audio.mixdown import build_filter, mixdown

needs_ffmpeg = pytest.mark.skipif(
    shutil.which("ffmpeg") is None, reason="ffmpeg ikke installeret"
)


# ── compute_offset_ms (kun diagnostik) ───────────────────────────────────────

def test_offset_positive_when_mic_starts_later():
    assert compute_offset_ms(10.080, 10.000) == pytest.approx(80.0)


def test_offset_negative_when_loop_starts_later():
    assert compute_offset_ms(10.000, 10.050) == pytest.approx(-50.0)


def test_offset_none_on_missing_or_mixed_clocks():
    assert compute_offset_ms(None, 10.0) is None
    assert compute_offset_ms(10.0, None) is None
    assert compute_offset_ms(10.08, 10.0, same_clock=False) is None


# ── startforskydning ud fra sporlængder ──────────────────────────────────────

def test_start_offset_positive_when_mic_recorded_longer():
    """Mic 49.39 s / melodi 48.60 s (rigtige tal fra app.log) = melodien
    startede 791 ms for sent."""
    assert compute_start_offset_ms(49.388843537414964, 48.59733333333333) == \
        pytest.approx(791.5, abs=0.5)


def test_start_offset_negative_when_loop_recorded_longer():
    assert compute_start_offset_ms(48.0, 48.25) == pytest.approx(-250.0)


def test_start_offset_none_without_both_tracks():
    assert compute_start_offset_ms(None, 10.0) is None
    assert compute_start_offset_ms(10.0, None) is None


# ── build_filter: kompensér KUN ud fra sporlængde-målingen ───────────────────

def test_filter_delays_melody_when_mic_started_first():
    filt = build_filter(1.0, 1.0, normalize=False, start_offset_ms=791.5)
    voc = filt.split("[voc]")[0]
    mel = filt.split("[voc];")[1].split("[mel]")[0]
    assert "adelay" not in voc
    assert "adelay=792:all=1" in mel


def test_filter_delays_mic_when_melody_started_first():
    filt = build_filter(1.0, 1.0, normalize=False, start_offset_ms=-120)
    voc = filt.split("[voc]")[0]
    mel = filt.split("[voc];")[1].split("[mel]")[0]
    assert "adelay=120:all=1" in voc
    assert "adelay" not in mel


@pytest.mark.parametrize("offset", [None, 0, 5, -19, 3500, -9000])
def test_filter_skips_compensation_outside_sane_range(offset):
    assert "adelay" not in build_filter(1.0, 1.0, False, start_offset_ms=offset)


def test_filter_single_input_is_just_finisher():
    assert build_filter(1.0, 1.0, False, two_inputs=False) == "alimiter=limit=0.97"


def test_adc_offset_can_never_reach_the_mix():
    """ADC-tidsstemplerne har ikke fælles nulpunkt — de skævvred mixet i
    v1.3.0-v1.6.0 og må aldrig kunne bruges som filter-parameter igen."""
    import inspect

    for fn in (mixdown, build_filter):
        params = inspect.signature(fn).parameters
        assert "offset_ms" not in params
        assert "start_offset_ms" in params


# ── Funktionelt bevis: klik beholder deres indbyrdes afstand ─────────────────

def _write_click_wav(path, click_at_s, rate=44100, seconds=3.5):
    """Stilhed med ét skarpt klik (5 ms firkant) på angivet tidspunkt."""
    frames = int(rate * seconds)
    click_start = int(click_at_s * rate)
    click_len = int(0.005 * rate)
    with wave.open(path, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(rate)
        for i in range(frames):
            val = 28000 if click_start <= i < click_start + click_len else 0
            wf.writeframes(struct.pack("<h", val))


def _click_positions(mp3_path, rate=44100):
    """Dekodér til PCM og find de to klik-positioner i sekunder."""
    raw = subprocess.run(
        ["ffmpeg", "-loglevel", "error", "-i", mp3_path,
         "-f", "s16le", "-ac", "1", "-ar", str(rate), "-"],
        capture_output=True, check=True,
    ).stdout
    samples = np.abs(np.frombuffer(raw, dtype=np.int16).astype(np.int32))

    first = int(np.argmax(samples))
    guard = int(0.100 * rate)  # udelad ±100 ms omkring første klik
    masked = samples.copy()
    masked[max(0, first - guard):first + guard] = 0
    second = int(np.argmax(masked))
    return sorted((first / rate, second / rate))


@needs_ffmpeg
@pytest.mark.parametrize("mic_click,forventet", [
    (1.92, 0.920),   # stemmen efter melodien
    (0.40, 0.600),   # stemmen før melodien
    (1.00, None),    # præcis samtidig — ét klik, ingen afstand at måle
])
def test_mixdown_preserves_relative_timing(tmp_path, mic_click, forventet):
    """Klikkenes indbyrdes afstand i mixet skal være nøjagtig som i kilderne."""
    mel = str(tmp_path / "melodi.wav")
    mic = str(tmp_path / "mic.wav")
    _write_click_wav(mel, click_at_s=1.0)
    _write_click_wav(mic, click_at_s=mic_click)

    out = str(tmp_path / "mix.mp3")
    mixdown(mic, mel, out, balance=0.5, normalize=False)

    if forventet is None:
        return  # samtidige klik smelter sammen; intet at måle
    t1, t2 = _click_positions(out)
    assert t2 - t1 == pytest.approx(forventet, abs=0.005)


@needs_ffmpeg
def test_mixdown_keeps_click_at_absolute_position(tmp_path):
    """Melodiens klik må heller ikke flytte sig i absolut tid."""
    mel = str(tmp_path / "melodi.wav")
    mic = str(tmp_path / "mic.wav")
    _write_click_wav(mel, click_at_s=1.0)
    _write_click_wav(mic, click_at_s=1.92)

    out = str(tmp_path / "mix.mp3")
    mixdown(mic, mel, out, balance=0.5, normalize=False)
    first, _ = _click_positions(out)
    assert first == pytest.approx(1.000, abs=0.020)


@needs_ffmpeg
def test_compensation_realigns_late_starting_melody(tmp_path):
    """Gengiv den fejl loggen viste: melodi-streamen startede 792 ms for sent,
    så melodisporet mangler sin begyndelse.

    Melodiens klik falder i virkeligheden ved 1,0 s, men ligger 792 ms tidligere
    i filen (0,208 s), fordi optagelsen først begyndte da. Mikrofonens klik
    falder ved 2,0 s. Efter kompensation skal afstanden i mixet være 1,000 s —
    ikke 1,792 s.
    """
    mel = str(tmp_path / "melodi.wav")
    mic = str(tmp_path / "mic.wav")
    _write_click_wav(mel, click_at_s=0.208)
    _write_click_wav(mic, click_at_s=2.0, seconds=4.0)

    out = str(tmp_path / "mix.mp3")
    mixdown(mic, mel, out, balance=0.5, normalize=False, start_offset_ms=791.5)
    t1, t2 = _click_positions(out)
    assert t2 - t1 == pytest.approx(1.000, abs=0.010)


@needs_ffmpeg
def test_without_compensation_late_melody_stays_misaligned(tmp_path):
    """Kontrol: uden kompensation er afstanden 1,792 s — altså skæv."""
    mel = str(tmp_path / "melodi.wav")
    mic = str(tmp_path / "mic.wav")
    _write_click_wav(mel, click_at_s=0.208)
    _write_click_wav(mic, click_at_s=2.0, seconds=4.0)

    out = str(tmp_path / "mix.mp3")
    mixdown(mic, mel, out, balance=0.5, normalize=False)
    t1, t2 = _click_positions(out)
    assert t2 - t1 == pytest.approx(1.792, abs=0.010)
