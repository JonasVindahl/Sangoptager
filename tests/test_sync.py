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

from sangoptager.audio.capture import compute_offset_ms
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


# ── build_filter må aldrig forsinke et spor ──────────────────────────────────

@pytest.mark.parametrize("normalize", [True, False])
def test_filter_never_delays_a_track(normalize):
    filt = build_filter(1.0, 1.0, normalize)
    assert "adelay" not in filt
    assert "atrim" not in filt
    assert "asetpts" not in filt


def test_filter_applies_balance_gains():
    filt = build_filter(0.8, 1.2, normalize=False)
    assert "volume=0.8000" in filt
    assert "volume=1.2000" in filt


def test_filter_single_input_is_just_finisher():
    assert build_filter(1.0, 1.0, False, two_inputs=False) == "alimiter=limit=0.97"


def test_mixdown_signature_has_no_offset():
    """Værn mod at kompensationen sniger sig ind igen ad bagvejen."""
    import inspect

    assert "offset" not in str(inspect.signature(mixdown))
    assert "offset" not in str(inspect.signature(build_filter))


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
