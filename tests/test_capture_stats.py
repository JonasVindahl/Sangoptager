import math
import struct
import time

import pytest

from sangoptager.audio.capture import (
    SILENCE_PEAK,
    RecordingResult,
    TrackStats,
    _measure,
    _WavWriter,
)


def _sine_buffer(amplitude: float, n: int = 1024, freq: float = 440.0,
                 rate: int = 44100) -> bytes:
    return b"".join(
        struct.pack("<h", int(amplitude * 32767 * math.sin(2 * math.pi * freq * i / rate)))
        for i in range(n)
    )


def _clipped_sine(n: int = 1024, freq: float = 440.0, rate: int = 44100,
                  amplitude: float = 1.5) -> bytes:
    """Sinus skruet forbi loftet og skåret af — præcis hvad en overstyret
    mikrofon leverer."""
    return b"".join(
        struct.pack("<h", max(-32768, min(32767, int(
            amplitude * 32767 * math.sin(2 * math.pi * freq * i / rate)))))
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


def test_measure_separates_rms_from_peak():
    """En sinus ligger 3 dB under sin egen top. Bruger metret ét tal til
    begge dele, kan det ikke både vise lydstyrke og afstand til loftet."""
    rms, peak = _measure(_sine_buffer(0.8))
    assert peak == pytest.approx(0.8, abs=0.01)
    assert rms == pytest.approx(0.8 / math.sqrt(2), abs=0.01)
    assert _measure(b"") == (0.0, 0.0)


def test_loud_but_clean_signal_is_not_clipping(tmp_path):
    """Kernen i det hele: kraftig sang er ikke det samme som forvrænget sang.
    Et signal 1 dB under loftet er højt, men helt rent."""
    writer = _WavWriter(str(tmp_path / "loud.wav"), channels=1, rate=44100)
    writer.write(_sine_buffer(0.89))
    assert writer.max_peak > 0.85
    assert writer.clipped_seconds == 0.0
    assert writer.levels.clipping is False
    writer.close()


def test_clipping_is_detected_and_held(tmp_path):
    rate = 44100
    writer = _WavWriter(str(tmp_path / "clip.wav"), channels=1, rate=rate)
    writer.write(_clipped_sine(n=rate // 2))       # et halvt sekund i loftet
    assert writer.max_peak == pytest.approx(1.0)
    assert writer.clipped_seconds == pytest.approx(0.5, abs=0.01)
    # Holdet er hele pointen: en buffer varer 23 ms, UI'et kigger hver 80 ms
    assert writer.levels.clipping is True
    writer.close()


def test_clip_hold_expires_but_the_measurement_stays(tmp_path):
    writer = _WavWriter(str(tmp_path / "c.wav"), channels=1, rate=44100)
    writer.write(_clipped_sine())
    assert writer.levels.clipping is True
    writer._clip_until = time.monotonic() - 0.001  # holdet er løbet ud
    assert writer.levels.clipping is False
    assert writer.clipped_seconds > 0.0            # men det skete
    writer.close()


def test_rms_and_peak_are_tracked_side_by_side(tmp_path):
    """Et klippet spor har både højt RMS og peak i loftet — mens stille
    passager bagefter hverken må sænke det ene eller det andet."""
    writer = _WavWriter(str(tmp_path / "b.wav"), channels=1, rate=44100)
    writer.write(_clipped_sine())
    peaks = (writer.max_level, writer.max_peak)
    writer.write(bytes(4096))
    assert (writer.max_level, writer.max_peak) == peaks
    assert writer.levels.rms < writer.max_level    # udjævningen falder af
    writer.close()


def test_track_stats_collects_a_finished_track(tmp_path):
    writer = _WavWriter(str(tmp_path / "t.wav"), channels=1, rate=44100)
    writer.overflows = 2
    writer.write(_clipped_sine())
    writer.close()

    stats = TrackStats.of(writer)
    assert stats.true_peak == pytest.approx(1.0)
    assert stats.clipped_seconds > 0.0
    assert stats.rms_peak > 0.5
    assert stats.overflows == 2
    assert stats.write_failed is False
    assert stats.seconds > 0.0


def test_result_aggregates_across_tracks():
    mic = TrackStats(rms_peak=0.4, true_peak=1.0, clipped_seconds=2.0,
                     overflows=1)
    loop = TrackStats(rms_peak=0.2, true_peak=0.5, overflows=2,
                      write_failed=True)
    result = RecordingResult("mic.wav", "melodi.wav", 30.0, mic=mic, loop=loop)
    assert result.overflows == 3
    assert result.disk_failed is True
    assert result.tracks == (mic, loop)


def test_recovered_recording_has_no_measurements():
    """Gendannet fra disken efter et crash: der findes ingen måletal, og
    advarslerne skal springes over frem for at gætte på dem."""
    result = RecordingResult("mic.wav", None, 30.0)
    assert result.tracks == ()
    assert result.overflows == 0
    assert result.disk_failed is False
