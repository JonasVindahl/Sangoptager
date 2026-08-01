"""Optagelse af mikrofon + systemlyd (WASAPI loopback) til to separate WAV-filer.

To backends:
  - Windows: PyAudioWPatch — mikrofon og WASAPI-loopback (det PC'en afspiller).
  - Andre platforme (udvikling): sounddevice — kun mikrofon; loopback-sporet
    udelades, og mixdown håndterer så et enkelt spor.

Sporene optages separat og mixes først ved "Gem" (se mixdown.py), så der er
ingen realtidsmixing eller clock-drift-håndtering her.
"""

from __future__ import annotations

import math
import os
import struct
import sys
import threading
import wave

IS_WINDOWS = sys.platform == "win32"

MIC_FILENAME = "mic.wav"
LOOP_FILENAME = "melodi.wav"

_FRAMES_PER_BUFFER = 1024
# Glidende udjævning af niveaumetrene, så de ikke flimrer
_METER_DECAY = 0.6


def _rms_level(pcm16: bytes) -> float:
    """RMS-niveau 0..1 af en buffer med 16-bit PCM."""
    n = len(pcm16) // 2
    if n == 0:
        return 0.0
    samples = struct.unpack(f"<{n}h", pcm16[: n * 2])
    acc = 0
    for s in samples:
        acc += s * s
    return math.sqrt(acc / n) / 32768.0


class _WavWriter:
    """Trådsikker WAV-skriver med tilhørende udjævnet niveaumåler."""

    def __init__(self, path: str, channels: int, rate: int):
        self._wf = wave.open(path, "wb")
        self._wf.setnchannels(channels)
        self._wf.setsampwidth(2)  # 16-bit
        self._wf.setframerate(rate)
        self._lock = threading.Lock()
        self._closed = False
        self.level = 0.0
        self.max_level = 0.0  # højeste RMS i hele optagelsen (til stilheds-tjek)

    def write(self, data: bytes):
        with self._lock:
            if not self._closed:
                self._wf.writeframes(data)
        raw = _rms_level(data)
        self.level = max(raw, self.level * _METER_DECAY)
        self.max_level = max(raw, self.max_level)

    def close(self):
        with self._lock:
            if not self._closed:
                self._closed = True
                self._wf.close()


class RecordingResult:
    def __init__(self, mic_path: str | None, loop_path: str | None, duration: float,
                 mic_peak: float | None = None, loop_peak: float | None = None):
        self.mic_path = mic_path
        self.loop_path = loop_path
        self.duration = duration
        # Højeste RMS pr. spor; None = ukendt (fx gendannet efter crash)
        self.mic_peak = mic_peak
        self.loop_peak = loop_peak


class CaptureError(RuntimeError):
    """Fejl der skal vises for brugeren (manglende enhed osv.)."""


class DualRecorder:
    """Optager mikrofon + systemlyd til to WAV-filer i en given mappe.

    mic_name/loop_name er enhedsnavne fra list_mics()/list_loopbacks();
    None betyder systemets standardenhed.

    Brug:
        rec = DualRecorder(mic_name=..., loop_name=...)
        rec.start(tmpdir)
        ... rec.mic_level / rec.loop_level opdateres løbende ...
        result = rec.stop()
    """

    def __init__(self, mic_name: str | None = None, loop_name: str | None = None):
        if IS_WINDOWS:
            self._backend = _WindowsBackend(mic_name, loop_name)
        else:
            self._backend = _DevBackend(mic_name)
        self._start_time: float | None = None
        self._duration: float = 0.0

    @property
    def mic_level(self) -> float:
        return self._backend.mic_level

    @property
    def loop_level(self) -> float:
        return self._backend.loop_level

    @property
    def has_loopback(self) -> bool:
        return self._backend.has_loopback

    def device_summary(self) -> str:
        return self._backend.device_summary()

    def list_mics(self) -> list[str]:
        return self._backend.list_mics()

    def list_loopbacks(self) -> list[str]:
        return self._backend.list_loopbacks()

    def start(self, out_dir: str):
        import time

        os.makedirs(out_dir, exist_ok=True)
        self._backend.start(out_dir)
        self._start_time = time.monotonic()

    def stop(self) -> RecordingResult:
        import time

        if self._start_time is not None:
            self._duration = time.monotonic() - self._start_time
            self._start_time = None
        mic_path, loop_path, mic_peak, loop_peak = self._backend.stop()
        return RecordingResult(mic_path, loop_path, self._duration,
                               mic_peak, loop_peak)

    def close(self):
        self._backend.close()


class _WindowsBackend:
    """PyAudioWPatch: mikrofon + WASAPI-loopback, valgfrit navngivet enhed."""

    def __init__(self, mic_name: str | None = None, loop_name: str | None = None):
        import pyaudiowpatch as pyaudio

        self._pa_module = pyaudio
        self._pa = pyaudio.PyAudio()
        self._streams = []
        self._writers: list[_WavWriter] = []
        self._mic_writer: _WavWriter | None = None
        self._loop_writer: _WavWriter | None = None
        self._mic_path: str | None = None
        self._loop_path: str | None = None
        self._mic_info = self._find_mic(mic_name)
        self._loop_info = self._find_loopback(loop_name)
        self.has_loopback = self._loop_info is not None

    @property
    def mic_level(self) -> float:
        return self._mic_writer.level if self._mic_writer else 0.0

    @property
    def loop_level(self) -> float:
        return self._loop_writer.level if self._loop_writer else 0.0

    def _wasapi_index(self):
        try:
            return self._pa.get_host_api_info_by_type(self._pa_module.paWASAPI)["index"]
        except OSError:
            return None

    def _iter_mics(self):
        wasapi = self._wasapi_index()
        for i in range(self._pa.get_device_count()):
            info = self._pa.get_device_info_by_index(i)
            if (info.get("maxInputChannels", 0) > 0
                    and not info.get("isLoopbackDevice")
                    and (wasapi is None or info.get("hostApi") == wasapi)):
                yield info

    def list_mics(self) -> list[str]:
        return [info["name"] for info in self._iter_mics()]

    def list_loopbacks(self) -> list[str]:
        try:
            return [info["name"].replace(" [Loopback]", "")
                    for info in self._pa.get_loopback_device_info_generator()]
        except OSError:
            return []

    def _find_mic(self, name: str | None):
        if name:
            for info in self._iter_mics():
                if info["name"] == name:
                    return info
        try:
            return self._pa.get_default_input_device_info()
        except OSError:
            return None

    def _find_loopback(self, name: str | None):
        pyaudio = self._pa_module
        try:
            if name:
                for loopback in self._pa.get_loopback_device_info_generator():
                    if loopback["name"].replace(" [Loopback]", "") == name:
                        return loopback
            wasapi_info = self._pa.get_host_api_info_by_type(pyaudio.paWASAPI)
            speakers = self._pa.get_device_info_by_index(
                wasapi_info["defaultOutputDevice"]
            )
            if speakers.get("isLoopbackDevice"):
                return speakers
            for loopback in self._pa.get_loopback_device_info_generator():
                if speakers["name"] in loopback["name"]:
                    return loopback
        except OSError:
            pass
        return None

    def device_summary(self) -> str:
        mic = self._mic_info["name"] if self._mic_info else "INGEN MIKROFON"
        if self._loop_info:
            loop = self._loop_info["name"].replace(" [Loopback]", "")
        else:
            loop = "INGEN SYSTEMLYD"
        return f"Mikrofon: {mic}  ·  Melodi: {loop}"

    def _open_stream(self, dev_info, writer: _WavWriter, channels: int, rate: int):
        pyaudio = self._pa_module

        def callback(in_data, frame_count, time_info, status):
            writer.write(in_data)
            return (None, pyaudio.paContinue)

        stream = self._pa.open(
            format=pyaudio.paInt16,
            channels=channels,
            rate=rate,
            input=True,
            input_device_index=dev_info["index"],
            frames_per_buffer=_FRAMES_PER_BUFFER,
            stream_callback=callback,
        )
        self._streams.append(stream)

    def start(self, out_dir: str):
        if self._mic_info is None:
            raise CaptureError(
                "Ingen mikrofon fundet. Tilslut en mikrofon og prøv igen."
            )

        mic_rate = int(self._mic_info["defaultSampleRate"])
        mic_ch = min(2, max(1, int(self._mic_info["maxInputChannels"])))
        self._mic_path = os.path.join(out_dir, MIC_FILENAME)
        self._mic_writer = _WavWriter(self._mic_path, mic_ch, mic_rate)
        self._writers.append(self._mic_writer)
        self._open_stream(self._mic_info, self._mic_writer, mic_ch, mic_rate)

        if self._loop_info is not None:
            loop_rate = int(self._loop_info["defaultSampleRate"])
            loop_ch = max(1, int(self._loop_info["maxInputChannels"]))
            self._loop_path = os.path.join(out_dir, LOOP_FILENAME)
            self._loop_writer = _WavWriter(self._loop_path, loop_ch, loop_rate)
            self._writers.append(self._loop_writer)
            self._open_stream(self._loop_info, self._loop_writer, loop_ch, loop_rate)

    def stop(self):
        for stream in self._streams:
            try:
                stream.stop_stream()
                stream.close()
            except OSError:
                pass
        self._streams.clear()

        for writer in self._writers:
            writer.close()
        mic_path, loop_path = self._mic_path, self._loop_path
        mic_peak = self._mic_writer.max_level if self._mic_writer else None
        loop_peak = self._loop_writer.max_level if self._loop_writer else None
        self._writers.clear()
        self._mic_writer = None
        self._loop_writer = None
        self._mic_path = None
        self._loop_path = None
        return mic_path, loop_path, mic_peak, loop_peak

    def close(self):
        self.stop()
        self._pa.terminate()


class _DevBackend:
    """sounddevice: kun mikrofon — til udvikling/test på macOS/Linux."""

    def __init__(self, mic_name: str | None = None):
        import sounddevice as sd

        self._sd = sd
        self._mic_name = mic_name
        self._stream = None
        self._mic_writer: _WavWriter | None = None
        self._mic_path: str | None = None
        self.has_loopback = False

    @property
    def mic_level(self) -> float:
        return self._mic_writer.level if self._mic_writer else 0.0

    @property
    def loop_level(self) -> float:
        return 0.0

    def list_mics(self) -> list[str]:
        try:
            return [d["name"] for d in self._sd.query_devices()
                    if d["max_input_channels"] > 0]
        except self._sd.PortAudioError:
            return []

    def list_loopbacks(self) -> list[str]:
        return []

    def _find_mic(self):
        """(device_index_eller_None, info) — index None = systemets standard."""
        sd = self._sd
        if self._mic_name:
            for i, dev in enumerate(sd.query_devices()):
                if dev["max_input_channels"] > 0 and dev["name"] == self._mic_name:
                    return i, dev
        return None, sd.query_devices(kind="input")

    def device_summary(self) -> str:
        try:
            _, dev = self._find_mic()
        except (self._sd.PortAudioError, ValueError):
            return "Mikrofon: INGEN  ·  Melodi: (kun Windows)"
        return f"Mikrofon: {dev['name']}  ·  Melodi: (kun Windows)"

    def start(self, out_dir: str):
        sd = self._sd
        try:
            index, dev = self._find_mic()
        except (sd.PortAudioError, ValueError):
            raise CaptureError(
                "Ingen mikrofon fundet. Tilslut en mikrofon og prøv igen."
            ) from None

        rate = int(dev["default_samplerate"])
        channels = min(2, max(1, int(dev["max_input_channels"])))
        self._mic_path = os.path.join(out_dir, MIC_FILENAME)
        self._mic_writer = _WavWriter(self._mic_path, channels, rate)

        def callback(indata, frames, time_info, status):
            self._mic_writer.write(bytes(indata))

        self._stream = sd.RawInputStream(
            device=index,
            samplerate=rate,
            channels=channels,
            dtype="int16",
            blocksize=_FRAMES_PER_BUFFER,
            callback=callback,
        )
        self._stream.start()

    def stop(self):
        if self._stream is not None:
            self._stream.stop()
            self._stream.close()
            self._stream = None
        mic_peak = None
        if self._mic_writer is not None:
            mic_peak = self._mic_writer.max_level
            self._mic_writer.close()
            self._mic_writer = None
        path = self._mic_path
        self._mic_path = None
        return path, None, mic_peak, None

    def close(self):
        self.stop()
