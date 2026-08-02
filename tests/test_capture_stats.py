import math
import struct

import pytest

from sangoptager.audio.capture import SILENCE_PEAK, _WavWriter


def _sine_buffer(amplitude: float, n: int = 1024, freq: float = 440.0,
                 rate: int = 44100) -> bytes:
    return b"".join(
        struct.pack("<h", int(amplitude * 32767 * math.sin(2 * math.pi * freq * i / rate)))
        for i in range(n)
    )


def test_wavwriter_tracks_max_level(tmp_path):
    writer = _WavWriter(str(tmp_path / "t.wav"), channels=1, rate=44100)
    writer.write(bytes(2048))                 # stilhed
    assert writer.max_level < 0.001

    writer.write(_sine_buffer(0.5))           # kraftigt signal
    peak_after_loud = writer.max_level
    assert peak_after_loud > 0.3

    writer.write(bytes(2048))                 # stilhed igen — peak må ikke falde
    assert writer.max_level == peak_after_loud
    writer.close()


def test_silence_threshold_matches_quiet_track(tmp_path):
    writer = _WavWriter(str(tmp_path / "q.wav"), channels=1, rate=44100)
    writer.write(_sine_buffer(0.005))         # -46 dB — reelt en død mikrofon
    assert writer.max_level < SILENCE_PEAK
    writer.close()


def test_tail_silence_tracks_music_stopping(tmp_path):
    """Melodien forsvinder midt i sangen (lyd skiftet til anden enhed):
    hale-stilheden skal vokse, og nulstilles når signalet kommer igen."""
    rate = 44100
    writer = _WavWriter(str(tmp_path / "m.wav"), channels=1, rate=rate)
    for _ in range(20):
        writer.write(_sine_buffer(0.4, n=rate))    # 20 sek. musik
    assert writer.tail_silence_seconds == 0.0

    for _ in range(15):
        writer.write(bytes(2 * rate))              # 15 sek. stilhed
    assert writer.tail_silence_seconds == pytest.approx(15.0, abs=0.1)

    writer.write(_sine_buffer(0.4, n=rate))        # musikken er tilbage
    assert writer.tail_silence_seconds == 0.0
    writer.close()


def test_write_failure_stops_queueing(tmp_path):
    """Dør writer-tråden på en diskfejl, må køen ikke vokse i det uendelige —
    og optagelsen skal markeres som fejlet, så brugeren advares."""
    writer = _WavWriter(str(tmp_path / "d.wav"), channels=1, rate=44100)
    writer.write_failed = True
    writer.write(_sine_buffer(0.4))
    assert len(writer._queue) == 0
    assert writer.max_level > 0.0     # niveaumetret virker stadig
    writer.close()
