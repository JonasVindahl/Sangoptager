"""Mix af mikrofon- og melodispor til én MP3 (320 kbps) med ffmpeg.

Balancen (0..1) styrer forholdet mellem stemme og melodi med equal-power-
kurver, så samlet lydstyrke føles konstant når man skruer:
    0.0 = kun melodi, 0.5 = neutral, 1.0 = kun mikrofon.

Normalisering kører i to gennemløb (EBU R128): først måles hele mixet,
derefter anvendes ét konstant gain — så sangene ender med samme oplevede
lydstyrke uden at ducke stemme og melodi i forhold til hinanden.
"""

from __future__ import annotations

import json as _json
import math
import os
import re
import shutil
import subprocess
import sys

from ..logsetup import log

MP3_BITRATE = "320k"


class MixdownError(RuntimeError):
    """Fejl der skal vises for brugeren (ffmpeg mangler/fejlede)."""


def find_ffmpeg() -> str:
    """Find ffmpeg: bundlet (PyInstaller), resources/-mappen, eller på PATH."""
    exe = "ffmpeg.exe" if sys.platform == "win32" else "ffmpeg"

    bundle_dir = getattr(sys, "_MEIPASS", None)
    candidates = []
    if bundle_dir:
        candidates.append(os.path.join(bundle_dir, exe))
    here = os.path.dirname(os.path.abspath(sys.argv[0]))
    candidates.append(os.path.join(here, exe))
    candidates.append(
        os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
                     "resources", exe)
    )
    for cand in candidates:
        if os.path.isfile(cand):
            return cand

    found = shutil.which("ffmpeg")
    if found:
        return found
    raise MixdownError(
        "ffmpeg blev ikke fundet. Læg ffmpeg.exe i programmappen eller installér det."
    )


def balance_gains(balance: float) -> tuple[float, float]:
    """(mic_gain, melodi_gain) ud fra balance 0..1 — equal-power."""
    b = min(1.0, max(0.0, balance))
    mic_gain = math.sin(b * math.pi / 2)
    loop_gain = math.cos(b * math.pi / 2)
    # Normalisér så neutral (0.5) giver gain 1.0 på begge spor
    scale = 1.0 / math.sin(math.pi / 4)
    return mic_gain * scale, loop_gain * scale


# EBU R128: -16 LUFS passer godt til musik på almindelige afspillere
_LOUDNORM = "loudnorm=I=-16:TP=-1.5:LRA=11"


def _parse_loudnorm_stats(stderr: str) -> dict | None:
    """Udtræk loudnorm-målingens JSON fra ffmpeg stderr."""
    cleaned = re.sub(r'\[Parsed_loudnorm_\d+ @ 0x[0-9a-f]+\]\s*', '', stderr)
    for match in reversed(list(re.finditer(r'\{[^{}]+\}', cleaned, re.DOTALL))):
        try:
            stats = _json.loads(match.group())
            if 'input_i' in stats:
                return stats
        except ValueError:
            continue
    return None


def _loudnorm_linear(stats: dict) -> str:
    """Loudnorm med målte værdier — lineært (konstant) gain, ingen ducking."""
    return (
        f"{_LOUDNORM}"
        f":measured_I={stats['input_i']}"
        f":measured_TP={stats['input_tp']}"
        f":measured_LRA={stats['input_lra']}"
        f":measured_thresh={stats['input_thresh']}"
        f":offset={stats['target_offset']}"
        f":linear=true"
    )


def _build_mix(mic_gain: float, loop_gain: float, finisher: str,
               two_inputs: bool) -> str:
    """Byg filterkæden med en given finisher (loudnorm eller limiter)."""
    if not two_inputs:
        return finisher
    return (
        f"[0:a]volume={mic_gain:.4f}[voc];"
        f"[1:a]volume={loop_gain:.4f}[mel];"
        "[voc][mel]amix=inputs=2:duration=longest:normalize=0,"
        + finisher
    )


def build_filter(mic_gain: float, loop_gain: float, normalize: bool,
                 two_inputs: bool = True) -> str:
    """ffmpeg-filterkæden: balance, sum og afsluttende limiter/normalisering.

    Sporene lægges oven på hinanden præcis som optaget — der forskydes ALDRIG
    i tid. Det er ikke nødvendigt: begge WAV-filer starter i samme øjeblik,
    der blev trykket Optag, fordi optagelsen selv fylder stilhed i, mens
    WASAPI-loopbacken endnu ikke leverer data (se _WavWriter.begin).

    Tidligere forsøg på at kompensere i mixet gjorde det kun værre — først
    med ADC-tidsstempler uden fælles nulpunkt (v1.3.0-v1.6.0), siden ud fra
    sporlængder (v1.9.0) og målt startforskydning (v1.10.0). Problemet hørte
    hjemme i optagelsen, ikke i mixet.
    """
    finisher = _LOUDNORM if normalize else "alimiter=limit=0.97"
    return _build_mix(mic_gain, loop_gain, finisher, two_inputs)


def _measure_loudness(ffmpeg: str, inputs: list[str], mic_gain: float,
                      loop_gain: float, two_inputs: bool) -> dict | None:
    """Første gennemløb: mål lydstyrke uden at skrive output."""
    filt = _build_mix(mic_gain, loop_gain,
                      f"{_LOUDNORM}:print_format=json", two_inputs)
    null_out = "NUL" if sys.platform == "win32" else "/dev/null"
    cmd = [ffmpeg, "-y", "-hide_banner", "-loglevel", "info"]
    for path in inputs:
        cmd += ["-i", path]
    cmd += ["-filter_complex", filt, "-f", "null", null_out]

    creationflags = 0x08000000 if sys.platform == "win32" else 0
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True,
                              creationflags=creationflags)
    except OSError:
        return None
    if proc.returncode != 0:
        return None
    return _parse_loudnorm_stats(proc.stderr)


def mixdown(
    mic_wav: str | None,
    loop_wav: str | None,
    out_mp3: str,
    balance: float = 0.5,
    normalize: bool = True,
) -> str:
    """Mix (eller konvertér et enkelt spor) til MP3. Returnerer stien.

    normalize=True kører EBU R128 loudness-normalisering i to gennemløb:
    først måles hele mixet, derefter anvendes ét konstant gain — så alle sange
    ender med samme oplevede lydstyrke uden at ducke stemme mod melodi.
    Sporene tidsforskydes ikke — de er allerede synkrone fra optagelsen.
    WAV-filerne røres ikke — kald selv cleanup bagefter, når MP3'en er
    verificeret.
    """
    inputs = [p for p in (mic_wav, loop_wav) if p and os.path.isfile(p)]
    if not inputs:
        raise MixdownError("Ingen lydspor at gemme — optagelsen er tom.")

    ffmpeg = find_ffmpeg()
    mic_gain, loop_gain = balance_gains(balance)
    two_inputs = len(inputs) == 2

    if normalize:
        stats = _measure_loudness(ffmpeg, inputs, mic_gain, loop_gain,
                                  two_inputs)
        if stats is not None:
            finisher = _loudnorm_linear(stats)
            log.info("Loudnorm two-pass: input_i=%s, target_offset=%s",
                     stats.get('input_i'), stats.get('target_offset'))
        else:
            finisher = _LOUDNORM
            log.warning("Loudnorm-måling fejlede — falder tilbage til "
                        "single-pass (dynamisk)")
    else:
        finisher = "alimiter=limit=0.97"

    filt = _build_mix(mic_gain, loop_gain, finisher, two_inputs)

    cmd = [ffmpeg, "-y", "-hide_banner", "-loglevel", "error"]
    for path in inputs:
        cmd += ["-i", path]

    cmd += [
        "-filter_complex", filt,
        "-ar", "44100",
        "-codec:a", "libmp3lame",
        "-b:a", MP3_BITRATE,
        out_mp3,
    ]

    creationflags = 0x08000000 if sys.platform == "win32" else 0  # CREATE_NO_WINDOW
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, creationflags=creationflags
        )
    except OSError as exc:
        raise MixdownError(f"Kunne ikke starte ffmpeg: {exc}") from exc

    if proc.returncode != 0:
        raise MixdownError(f"ffmpeg fejlede:\n{proc.stderr.strip()[-500:]}")
    if not os.path.isfile(out_mp3) or os.path.getsize(out_mp3) == 0:
        raise MixdownError("MP3-filen blev ikke skrevet korrekt.")
    return out_mp3
