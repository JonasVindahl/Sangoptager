"""Tests af spor-synkronisering: offset-beregning, filterkæde og et
funktionelt bevis på at kompensationen faktisk retter forskydningen."""

import os
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


# ── compute_offset_ms ────────────────────────────────────────────────────────

def test_offset_positive_when_mic_starts_later():
    assert compute_offset_ms(10.080, 10.000) == pytest.approx(80.0)


def test_offset_negative_when_loop_starts_later():
    assert compute_offset_ms(10.000, 10.050) == pytest.approx(-50.0)


def test_offset_none_on_missing_or_mixed_clocks():
    assert compute_offset_ms(None, 10.0) is None
    assert compute_offset_ms(10.0, None) is None
    assert compute_offset_ms(10.08, 10.0, same_clock=False) is None


# ── build_filter ─────────────────────────────────────────────────────────────

def test_filter_delays_mic_on_positive_offset():
    filt = build_filter(1.0, 1.0, normalize=False, offset_ms=80)
    voc = filt.split("[voc]")[0]
    assert "adelay=80:all=1" in voc
    mel = filt.split("[voc];")[1].split("[mel]")[0]
    assert "adelay" not in mel


def test_filter_delays_melody_on_negative_offset():
    filt = build_filter(1.0, 1.0, normalize=False, offset_ms=-64)
    voc = filt.split("[voc]")[0]
    assert "adelay" not in voc
    mel = filt.split("[voc];")[1].split("[mel]")[0]
    assert "adelay=64:all=1" in mel


@pytest.mark.parametrize("offset", [None, 3, -4, 900, -1200])
def test_filter_skips_compensation_outside_sane_range(offset):
    assert "adelay" not in build_filter(1.0, 1.0, False, offset_ms=offset)


def test_filter_single_input_has_no_delay():
    assert "adelay" not in build_filter(1.0, 1.0, False, offset_ms=80,
                                        two_inputs=False)


# ── Funktionelt bevis: klik-afstand efter kompenseret mix ───────────────────

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
def test_offset_compensation_aligns_clicks(tmp_path):
    """Melodi-klik ved 1,0 s; mic-streamen 'startede 80 ms senere', så dens
    klik (samme realtid som 2,0 s) ligger ved 1,92 s i filen. Med
    offset_ms=80 skal klik-afstanden i mixet være præcis 1,000 s."""
    mel = str(tmp_path / "melodi.wav")
    mic = str(tmp_path / "mic.wav")
    _write_click_wav(mel, click_at_s=1.0)
    _write_click_wav(mic, click_at_s=1.92)

    out = str(tmp_path / "mix.mp3")
    mixdown(mic, mel, out, balance=0.5, normalize=False, offset_ms=80)
    t1, t2 = _click_positions(out)
    assert t2 - t1 == pytest.approx(1.000, abs=0.005)


@needs_ffmpeg
def test_without_compensation_clicks_stay_misaligned(tmp_path):
    """Kontrol: uden kompensation er afstanden 0,92 s — altså skæv."""
    mel = str(tmp_path / "melodi.wav")
    mic = str(tmp_path / "mic.wav")
    _write_click_wav(mel, click_at_s=1.0)
    _write_click_wav(mic, click_at_s=1.92)

    out = str(tmp_path / "mix.mp3")
    mixdown(mic, mel, out, balance=0.5, normalize=False, offset_ms=None)
    t1, t2 = _click_positions(out)
    assert t2 - t1 == pytest.approx(0.920, abs=0.005)