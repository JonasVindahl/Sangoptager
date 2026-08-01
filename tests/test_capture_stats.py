import math
import struct

from sangoptager.audio.capture import _WavWriter


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
    from sangoptager.ui.save_dialog import SILENCE_PEAK

    writer = _WavWriter(str(tmp_path / "q.wav"), channels=1, rate=44100)
    writer.write(_sine_buffer(0.005))         # -46 dB — reelt en død mikrofon
    assert writer.max_level < SILENCE_PEAK
    writer.close()
