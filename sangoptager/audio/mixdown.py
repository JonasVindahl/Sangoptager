"""Mix af mikrofon- og melodispor til én MP3 (320 kbps) med ffmpeg.

Balancen (0..1) styrer forholdet mellem stemme og melodi med equal-power-
kurver, så samlet lydstyrke føles konstant når man skruer:
    0.0 = kun melodi, 0.5 = neutral, 1.0 = kun mikrofon.
En limiter til sidst forhindrer digital klipning af summen.
"""

from __future__ import annotations

import math
import os
import shutil
import subprocess
import sys

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


def mixdown(
    mic_wav: str | None,
    loop_wav: str | None,
    out_mp3: str,
    balance: float = 0.5,
    normalize: bool = True,
) -> str:
    """Mix (eller konvertér et enkelt spor) til MP3. Returnerer stien.

    normalize=True kører EBU R128 loudness-normalisering, så alle sange
    ender med samme oplevede lydstyrke. WAV-filerne røres ikke — kald selv
    cleanup bagefter, når MP3'en er verificeret.
    """
    inputs = [p for p in (mic_wav, loop_wav) if p and os.path.isfile(p)]
    if not inputs:
        raise MixdownError("Ingen lydspor at gemme — optagelsen er tom.")

    ffmpeg = find_ffmpeg()
    mic_gain, loop_gain = balance_gains(balance)
    finisher = _LOUDNORM if normalize else "alimiter=limit=0.97"

    cmd = [ffmpeg, "-y", "-hide_banner", "-loglevel", "error"]
    for path in inputs:
        cmd += ["-i", path]

    if len(inputs) == 2:
        filt = (
            f"[0:a]volume={mic_gain:.4f}[voc];"
            f"[1:a]volume={loop_gain:.4f}[mel];"
            "[voc][mel]amix=inputs=2:duration=longest:normalize=0,"
            + finisher
        )
    else:
        filt = finisher

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
