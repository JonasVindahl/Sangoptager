"""Tests af spor-synkronisering: den sker i OPTAGELSEN, ikke i mixet.

WASAPI-loopback leverer ikke data, før der faktisk afspilles lyd. Uden
modtræk ville melodisporets første sample være det øjeblik, musikken startede
— ikke det øjeblik der blev trykket Optag — og sporet ville ligge for tidligt.
Optagelsen fylder derfor selv stilhed i hullet, så begge WAV-filer dækker
samme tidsrum og kan lægges råt oven på hinanden.

Tre tidligere forsøg på at rette det i mixet gjorde det værre: ADC-tidsstempler
uden fælles nulpunkt (v1.3.0-v1.6.0), sporlængde-differencen (v1.9.0, som også
indeholdt pausen efter musikken stoppede) og en målt startforskydning med et
for lavt loft (v1.10.0). Testene her fastholder, at mixet aldrig forskyder.
"""

import shutil
import struct
import subprocess
import time
import wave

import numpy as np
import pytest

from sangoptager.audio.capture import _WavWriter
from sangoptager.audio.mixdown import build_filter, mixdown

needs_ffmpeg = pytest.mark.skipif(
    shutil.which("ffmpeg") is None, reason="ffmpeg ikke installeret"
)


# ── Sporene er synkrone fra optagelsen, ikke fra mixet ──────────────────────

def test_writer_pads_the_gap_before_first_audio(tmp_path):
    """WASAPI-loopback tier, indtil der spiller lyd. Sporet skal selv fylde
    hullet fra optagestart til første buffer."""
    rate = 8000
    writer = _WavWriter(str(tmp_path / "melodi.wav"), channels=1, rate=rate)
    writer.begin(time.monotonic() - 2.0)      # der gik 2 sek. før musikken kom
    writer.write(bytes(2 * rate))             # 1 sek. rigtig lyd
    writer.close()

    with wave.open(str(tmp_path / "melodi.wav")) as wf:
        seconds = wf.getnframes() / wf.getframerate()
    # Bufferen dækker selv det sidste sekund, så der mangler 1 sek. stilhed
    # foran — filen dækker i alt de 2 sek. siden der blev trykket Optag
    assert seconds == pytest.approx(2.0, abs=0.1)
    assert writer.filled_gaps == 1


def test_writer_fills_a_gap_in_the_middle(tmp_path):
    """Kernen i den sporadiske fejl: melodien tier MIDT i sangen — videoen
    sættes på pause, hakker, eller der kommer en reklame. Loopbacken leverer
    intet imens, og uden udfyldning ville de sekunder mangle, så resten af
    sangen lå for tidligt."""
    rate = 8000
    t0 = time.monotonic()
    writer = _WavWriter(str(tmp_path / "melodi.wav"), channels=1, rate=rate)
    writer.begin(t0)

    writer.write(bytes(2 * rate))          # 1 sek. musik
    writer._start_ts = t0 - 4.0            # …så gik der 3 sek. uden data
    writer.write(bytes(2 * rate))          # 1 sek. musik igen
    writer.close()

    with wave.open(str(tmp_path / "melodi.wav")) as wf:
        seconds = wf.getnframes() / wf.getframerate()
    assert seconds == pytest.approx(4.0, abs=0.15)   # 1 + 2 hul + 1
    assert writer.filled_gaps == 1
    assert writer.filled_seconds == pytest.approx(2.0, abs=0.15)


def test_writer_ignores_jitter_below_threshold(tmp_path):
    """Små udsving er planlægnings-jitter, ikke huller. Fyldte vi dem ud,
    ville vi indsætte stilhed der aldrig var der."""
    rate = 8000
    t0 = time.monotonic()
    writer = _WavWriter(str(tmp_path / "mic.wav"), channels=1, rate=rate)
    writer.begin(t0)
    writer.write(bytes(2 * rate))
    writer._start_ts = t0 - 1.2            # 200 ms bagud — under tærsklen
    writer.write(bytes(2 * rate))
    writer.close()

    assert writer.filled_gaps == 0


def test_writer_pads_nothing_when_audio_arrives_at_once(tmp_path):
    rate = 8000
    writer = _WavWriter(str(tmp_path / "mic.wav"), channels=1, rate=rate)
    writer.begin(time.monotonic())
    writer.write(bytes(2 * rate))
    writer.close()

    assert writer.filled_gaps == 0
    with wave.open(str(tmp_path / "mic.wav")) as wf:
        assert wf.getnframes() == rate


def test_both_tracks_share_the_same_zero_point(tmp_path):
    """Et realistisk forløb over 6 sekunder: mikrofonen leverer uafbrudt,
    mens melodien tier de første 2 sek. og igen 1 sek. midtvejs. Begge filer
    skal dække samme tidsrum, så de kan lægges råt oven på hinanden.

    _start_ts flyttes mellem kaldene for at simulere, at uret er gået —
    hver buffer må kun indeholde lyd, der er plads til i den forløbne tid.
    """
    rate = 8000
    now = time.monotonic()

    mic = _WavWriter(str(tmp_path / "mic.wav"), channels=1, rate=rate)
    mel = _WavWriter(str(tmp_path / "melodi.wav"), channels=1, rate=rate)

    # Mikrofonen: 6 sek. lyd på 6 sek. — intet at fylde ud
    mic.begin(now - 6.0)
    mic.write(bytes(2 * rate * 6))

    mel.begin(now - 4.0)                 # 4 sek. gået: 2 stille + 2 med musik
    mel.write(bytes(2 * rate * 2))
    mel._start_ts = now - 6.0            # 6 sek. gået: 1 sek. hul, så 1 med musik
    mel.write(bytes(2 * rate * 1))

    mic.close()
    mel.close()

    lengths = []
    for name in ("mic.wav", "melodi.wav"):
        with wave.open(str(tmp_path / name)) as wf:
            lengths.append(wf.getnframes() / wf.getframerate())
    assert lengths[0] == pytest.approx(6.0, abs=0.15)
    assert lengths[1] == pytest.approx(lengths[0], abs=0.15)
    assert mic.filled_gaps == 0
    assert mel.filled_gaps == 2           # hullet foran og hullet midtvejs
    assert mel.filled_seconds == pytest.approx(3.0, abs=0.15)


# ── build_filter forskyder aldrig ───────────────────────────────────────────

@pytest.mark.parametrize("normalize", [True, False])
def test_filter_never_delays_a_track(normalize):
    filt = build_filter(1.0, 1.0, normalize)
    for shifter in ("adelay", "atrim", "asetpts", "itsoffset"):
        assert shifter not in filt


def test_filter_applies_balance_gains():
    filt = build_filter(0.8, 1.2, normalize=False)
    assert "volume=0.8000" in filt
    assert "volume=1.2000" in filt


def test_filter_single_input_is_just_finisher():
    assert build_filter(1.0, 1.0, False, two_inputs=False) == "alimiter=limit=0.97"


def test_mix_takes_no_offset_parameter():
    """Synkroniseringen hører hjemme i optagelsen. Tre forsøg på at rette den
    i mixet gjorde det kun værre — ingen af dem må komme igen."""
    import inspect

    for fn in (mixdown, build_filter):
        params = inspect.signature(fn).parameters
        assert not any("offset" in name for name in params)


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
