"""Optagelse af mikrofon + systemlyd (WASAPI loopback) til to separate WAV-filer.

To backends:
  - Windows: PyAudioWPatch — mikrofon og WASAPI-loopback (det PC'en afspiller).
  - Andre platforme (udvikling): sounddevice — kun mikrofon; loopback-sporet
    udelades, og mixdown håndterer så et enkelt spor.

Sporene optages separat og mixes først ved "Gem" (se mixdown.py), så der er
ingen realtidsmixing eller clock-drift-håndtering her.
"""

from __future__ import annotations

import collections
import math
import os
import sys
import threading
import time
import wave
from typing import NamedTuple

from ..logsetup import log

IS_WINDOWS = sys.platform == "win32"

MIC_FILENAME = "mic.wav"
LOOP_FILENAME = "melodi.wav"

_FRAMES_PER_BUFFER = 1024
# Glidende udjævning af niveaumetrene, så de ikke flimrer
_METER_DECAY = 0.6
# Under denne peak-RMS regnes et helt spor for (nær-)stille — ca. -34 dB.
# En glemt mikrofon eller en melodi der aldrig blev afspillet lander her.
SILENCE_PEAK = 0.02
# 16-bit topper ved ±32767 (og -32768). Rammer en sample loftet, er signalet
# klippet — dét, og ikke et højt RMS, er hvad der lyder forvrænget.
_FULL_SCALE = 32768.0
CLIP_LEVEL = 32767 / _FULL_SCALE
# Hvor længe "klipper lige nu" bliver hængende. En buffer er 23 ms, men UI'et
# kigger kun hver 80 ms — uden et hold ville de fleste klip aldrig blive vist.
CLIP_HOLD_S = 1.5
# Under denne RMS regnes en enkelt buffer for stille i hale-stilheds-målingen
# (ca. -40 dB — lavere end SILENCE_PEAK, så musikkens svage passager ikke
# tæller med som "melodien er væk")
TAIL_SILENCE_RMS = 0.01
# Mindste hul der fyldes med stilhed. Konservativt med vilje: leveres buffere
# i et lille bundt efter en planlægnings-forsinkelse, ser den første ud som om
# der mangler tid. Ægte huller (pause i videoen, buffering, reklame) er typisk
# over et sekund. At misse en forskydning på under et halvt sekund er langt at
# foretrække frem for at indsætte stilhed, der ikke var der.
GAP_FILL_MIN_S = 0.5


class Levels(NamedTuple):
    """Øjebliksbillede af et spor til niveaumetret."""

    rms: float = 0.0        # udjævnet RMS 0..1 — bjælkens fyld
    peak: float = 0.0       # udjævnet sample-peak 0..1 — peak-hold-stregen
    clipping: bool = False  # sampleværdier har ramt loftet inden for CLIP_HOLD_S


def _measure(pcm16: bytes) -> tuple[float, float]:
    """(RMS, sample-peak) 0..1 af en buffer med 16-bit PCM.

    Begge tal skal bruges hver gang: RMS er den oplevede lydstyrke (bjælken),
    mens sample-peaken er den eneste der afslører forvrængning. En stemme har
    12-15 dB mellem de to, så et pænt RMS udelukker ikke klippede toppe.
    """
    n = len(pcm16) // 2
    if n == 0:
        return 0.0, 0.0
    samples = memoryview(pcm16)[: n * 2].cast("h")
    if hasattr(math, "sumprod"):  # Python 3.12+: C-hastighed
        acc = math.sumprod(samples, samples)
    else:
        acc = 0
        for s in samples:
            acc += s * s
    # -min fanger -32768, som abs() ikke kan repræsentere i 16-bit
    peak = max(max(samples), -min(samples))
    return math.sqrt(acc / n) / _FULL_SCALE, peak / _FULL_SCALE


class _WavWriter:
    """WAV-skriver med writer-tråd: audio-callbacken lægger kun bytes i en kø,
    så et disk-hik aldrig kan blokere lyd-tråden (og dermed tabe samples).
    Måler desuden niveau, første-buffer-tidsstempel og overflow."""

    def __init__(self, path: str, channels: int, rate: int):
        self._path = path
        self._wf = wave.open(path, "wb")
        self._wf.setnchannels(channels)
        self._wf.setsampwidth(2)  # 16-bit
        self._wf.setframerate(rate)
        self._channels = channels
        self._rate = rate
        self._bytes_per_second = 2 * channels * rate
        self._queue: collections.deque[bytes] = collections.deque()
        self._wakeup = threading.Event()
        self._closing = False
        self._closed = threading.Event()
        self.level = 0.0        # udjævnet RMS — bjælkens fyld
        self.peak_level = 0.0   # udjævnet sample-peak — peak-hold-stregen
        self.max_level = 0.0    # højeste RMS i hele optagelsen (stilheds-tjek)
        self.max_peak = 0.0     # højeste sampleværdi i hele optagelsen
        self._clipped_bytes = 0             # bytes i buffere der ramte loftet
        self._clip_until = 0.0              # monotonic-tid hvor klip-holdet slut
        self.bytes_written = 0
        self.overflows = 0      # antal buffere hvor driveren meldte overflow
        self.write_failed = False           # disk-skrivning er brudt sammen
        self._tail_silence_bytes = 0        # bytes siden sidste hørbare buffer
        self.first_ts: float | None = None  # tid for første buffer
        self._start_ts: float | None = None  # nulpunkt: da der blev trykket Optag
        self._accepted = 0                  # bytes lagt i kø (lyd + stilhed)
        self.filled_gaps = 0                # antal huller vi har fyldt ud
        self.filled_seconds = 0.0           # samlet udfyldt tid, til log
        threading.Thread(target=self._drain, daemon=True,
                         name="wav-writer").start()

    def begin(self, start_ts: float):
        """Sæt optagelsens nulpunkt — tidspunktet hvor der blev trykket Optag.

        Sporet fyldes med stilhed fra dette tidspunkt og frem til den første
        rigtige lyddata. Det er nødvendigt, fordi WASAPI-loopback ikke
        leverer noget, før der faktisk afspilles lyd: uden udfyldning ville
        melodisporets første sample være det øjeblik, musikken startede, og
        sporet ville ligge for tidligt i forhold til mikrofonen.
        """
        self._start_ts = start_ts

    def _missing_bytes(self, incoming: int) -> int:
        """Hvor mange bytes stilhed mangler, for at sporet passer med uret?

        Kaldes ved HVER buffer, ikke kun den første: WASAPI-loopback holder op
        med at levere data, hver gang der ikke afspilles lyd — også midt i en
        sang, hvis videoen sættes på pause eller hakker. Uden det her ville de
        sekunder mangle i filen, og alt derefter ligge for tidligt.

        Ingen allokering her — vi er i lyd-callbacken; nullerne skrives af
        writer-tråden.
        """
        if self._start_ts is None:
            return 0
        per_sec = self._bytes_per_second
        # Bufferen dækker selv tiden umiddelbart før callbacken
        expected = int((time.monotonic() - self._start_ts) * per_sec) - incoming
        missing = expected - self._accepted
        if missing < GAP_FILL_MIN_S * per_sec:
            return 0                          # jitter, ikke et ægte hul
        return missing - missing % (2 * self._channels)   # hele frames

    def mark_first_buffer(self):
        """Kaldes fra callbacken ved første buffer. Bruger altid
        time.monotonic(), som er procesbred og derfor fælles for begge spor."""
        if self.first_ts is None:
            self.first_ts = time.monotonic()

    def write(self, data: bytes):
        """Fra audio-callbacken: kø + niveauer, ingen disk-I/O."""
        if not self.write_failed:
            missing = self._missing_bytes(len(data))
            if missing:
                # int i køen = stilhed; rækkefølgen i forhold til lyden
                # bevares automatisk, fordi de deler kø
                self._queue.append(missing)
                self._accepted += missing
                self.filled_gaps += 1
                self.filled_seconds += missing / self._bytes_per_second
            self._queue.append(data)
            self._accepted += len(data)
            self._wakeup.set()
        rms, peak = _measure(data)
        self.level = max(rms, self.level * _METER_DECAY)
        self.peak_level = max(peak, self.peak_level * _METER_DECAY)
        self.max_level = max(rms, self.max_level)
        self.max_peak = max(peak, self.max_peak)
        if peak >= CLIP_LEVEL:
            self._clipped_bytes += len(data)
            self._clip_until = time.monotonic() + CLIP_HOLD_S
        if rms >= TAIL_SILENCE_RMS:
            self._tail_silence_bytes = 0
        else:
            self._tail_silence_bytes += len(data)

    def _drain(self):
        try:
            while True:
                self._wakeup.wait(0.2)
                self._wakeup.clear()
                while self._queue:
                    item = self._queue.popleft()
                    if isinstance(item, int):
                        self._write_silence(item)
                    else:
                        self._wf.writeframes(item)
                        self.bytes_written += len(item)
                if self._closing and not self._queue:
                    self._wf.close()
                    self._closed.set()
                    return
        except Exception as exc:
            # Disk fuld/forsvundet: smid resten væk i stedet for at æde RAM,
            # og lad UI'et melde diskfejlen via write_failed
            self.write_failed = True
            self._queue.clear()
            log.error("Skrivefejl på %s: %s", self._path, exc)
            try:
                self._wf.close()
            except Exception:
                pass
            self._closed.set()

    def _write_silence(self, remaining: int):
        """Fyld et hul med stilhed. Skrives i portioner, så en lang pause ikke
        kræver én kæmpe buffer i hukommelsen."""
        block = bytes(65536)
        while remaining > 0:
            size = min(len(block), remaining)
            self._wf.writeframes(block[:size])
            self.bytes_written += size
            remaining -= size

    @property
    def levels(self) -> Levels:
        """Til niveaumetret — klip-flaget holder CLIP_HOLD_S, så et kort klip
        ikke når at forsvinde mellem to opdateringer af UI'et."""
        return Levels(self.level, self.peak_level,
                      time.monotonic() < self._clip_until)

    @property
    def seconds_written(self) -> float:
        return self.bytes_written / self._bytes_per_second

    @property
    def clipped_seconds(self) -> float:
        """Hvor meget af sporet lå i loftet. Tælles pr. buffer, så tallet er
        rundhåndet — ét klippet sample koster hele bufferens 23 ms."""
        return self._clipped_bytes / self._bytes_per_second

    @property
    def tail_silence_seconds(self) -> float:
        """Hvor længe har sporet været (nær-)stille op til nu?"""
        return self._tail_silence_bytes / self._bytes_per_second

    def close(self):
        if self._closing:
            return
        self._closing = True
        self._wakeup.set()
        self._closed.wait(timeout=15)


class GapReport:
    """Hvor mange huller blev fyldt ud i et spor, og hvor meget i alt.

    Tallene er ren diagnostik: sporet er synkront takket være udfyldningen.
    Men bliver de ved med at være store, virker stilheds-afspilningen (som
    skal holde lydenheden vågen) ikke som forventet.
    """

    def __init__(self, count: int = 0, seconds: float = 0.0):
        self.count = count
        self.seconds = seconds

    def __bool__(self) -> bool:
        return self.count > 0

    def __repr__(self) -> str:
        return f"{self.count} hul(ler), {self.seconds:.1f} sek"


class TrackStats:
    """Hvad der blev målt på ét spor gennem en optagelse."""

    def __init__(self, rms_peak: float = 0.0, true_peak: float = 0.0,
                 clipped_seconds: float = 0.0, seconds: float = 0.0,
                 overflows: int = 0, tail_silence: float = 0.0,
                 gaps: GapReport | None = None, write_failed: bool = False):
        # Højeste RMS — afslører et spor der stod (nær-)stille hele vejen
        self.rms_peak = rms_peak
        # Højeste sampleværdi — afslører forvrængning, som RMS ikke kan
        self.true_peak = true_peak
        self.clipped_seconds = clipped_seconds
        self.seconds = seconds              # sporets længde på disken
        self.overflows = overflows          # buffere driveren meldte tabt
        self.tail_silence = tail_silence    # stilhed op til stop
        # Udfyldte huller — kun til log; sporet er synkront takket være dem
        self.gaps = gaps or GapReport()
        self.write_failed = write_failed

    @classmethod
    def of(cls, writer: _WavWriter) -> TrackStats:
        return cls(
            rms_peak=writer.max_level,
            true_peak=writer.max_peak,
            clipped_seconds=writer.clipped_seconds,
            seconds=writer.seconds_written,
            overflows=writer.overflows,
            tail_silence=writer.tail_silence_seconds,
            gaps=GapReport(writer.filled_gaps, writer.filled_seconds),
            write_failed=writer.write_failed,
        )


class RecordingResult:
    """Stier, længde og måletal for én optagelse.

    mic/loop er None når sporet ikke findes — eller når måletallene er ukendte,
    fx en optagelse der gendannes fra disken efter et crash. Advarslerne i
    gem-dialogen springes så over frem for at gætte.
    """

    def __init__(self, mic_path: str | None, loop_path: str | None,
                 duration: float, mic: TrackStats | None = None,
                 loop: TrackStats | None = None):
        self.mic_path = mic_path
        self.loop_path = loop_path
        self.duration = duration
        self.mic = mic
        self.loop = loop

    @property
    def tracks(self) -> tuple[TrackStats, ...]:
        return tuple(t for t in (self.mic, self.loop) if t is not None)

    @property
    def overflows(self) -> int:
        return sum(t.overflows for t in self.tracks)

    @property
    def disk_failed(self) -> bool:
        """True hvis disk-skrivningen brød sammen undervejs (spor ufuldstændige)."""
        return any(t.write_failed for t in self.tracks)


class CaptureError(RuntimeError):
    """Fejl der skal vises for brugeren (manglende enhed osv.)."""


class _Backend:
    """Fælles tilstand for de to backends: writerne og aflæsningen af dem."""

    def __init__(self):
        self.has_loopback = False
        self.mic_writer: _WavWriter | None = None
        self.loop_writer: _WavWriter | None = None
        self.mic_path: str | None = None
        self.loop_path: str | None = None

    @property
    def writers(self) -> list[_WavWriter]:
        return [w for w in (self.mic_writer, self.loop_writer) if w is not None]

    @property
    def mic_levels(self) -> Levels:
        return self.mic_writer.levels if self.mic_writer else Levels()

    @property
    def loop_levels(self) -> Levels:
        return self.loop_writer.levels if self.loop_writer else Levels()

    @property
    def mic_bytes(self) -> int:
        return self.mic_writer.bytes_written if self.mic_writer else 0

    @property
    def disk_failed(self) -> bool:
        return any(w.write_failed for w in self.writers)

    def _take_stats(self) -> dict:
        """Saml måletallene fra stop() og slip writerne."""
        stats = dict(
            mic_path=self.mic_path, loop_path=self.loop_path,
            mic=TrackStats.of(self.mic_writer) if self.mic_writer else None,
            loop=TrackStats.of(self.loop_writer) if self.loop_writer else None,
        )
        self.mic_writer = self.loop_writer = None
        self.mic_path = self.loop_path = None
        return stats


class DualRecorder:
    """Optager mikrofon + systemlyd til to WAV-filer i en given mappe.

    mic_name/loop_name er enhedsnavne fra list_mics()/list_loopbacks();
    None betyder systemets standardenhed.

    Brug:
        rec = DualRecorder(mic_name=..., loop_name=...)
        rec.start(tmpdir)
        ... rec.mic_levels / rec.loop_levels opdateres løbende ...
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
    def mic_levels(self) -> Levels:
        return self._backend.mic_levels

    @property
    def loop_levels(self) -> Levels:
        return self._backend.loop_levels

    @property
    def has_loopback(self) -> bool:
        return self._backend.has_loopback

    @property
    def mic_bytes(self) -> int:
        """Bytes modtaget fra mikrofonen indtil nu — til vagthund i UI'et."""
        return self._backend.mic_bytes

    @property
    def disk_failed(self) -> bool:
        """True hvis disk-skrivningen er brudt sammen — til vagthund i UI'et."""
        return self._backend.disk_failed

    def device_summary(self) -> str:
        return self._backend.device_summary()

    def list_mics(self) -> list[str]:
        return self._backend.list_mics()

    def list_loopbacks(self) -> list[str]:
        return self._backend.list_loopbacks()

    def start(self, out_dir: str):
        os.makedirs(out_dir, exist_ok=True)
        self._backend.start(out_dir)
        self._start_time = time.monotonic()

    def stop(self) -> RecordingResult:
        if self._start_time is not None:
            self._duration = time.monotonic() - self._start_time
            self._start_time = None
        stats = self._backend.stop()
        return RecordingResult(duration=self._duration, **stats)

    def close(self):
        self._backend.close()


class _WindowsBackend(_Backend):
    """PyAudioWPatch: mikrofon + WASAPI-loopback, valgfrit navngivet enhed."""

    def __init__(self, mic_name: str | None = None, loop_name: str | None = None):
        import pyaudiowpatch as pyaudio

        super().__init__()
        self._pa_module = pyaudio
        self._pa = pyaudio.PyAudio()
        self._streams = []
        self._mic_info = self._find_mic(mic_name)
        self._loop_info = self._find_loopback(loop_name)
        self.has_loopback = self._loop_info is not None
        self._keepalive = None   # stilheds-afspilning, se _start_keepalive

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
            writer.mark_first_buffer()
            if status & pyaudio.paInputOverflow:  # driveren tabte data
                writer.overflows += 1
            writer.write(in_data)
            return (None, pyaudio.paContinue)

        # start=False: den dyre enhedsinitiering sker her, men optagelsen
        # begynder først ved start_stream() — så begge spor kan sættes i gang
        # på samme tid i stedet for med en hel WASAPI-opstart imellem sig
        stream = self._pa.open(
            format=pyaudio.paInt16,
            channels=channels,
            rate=rate,
            input=True,
            input_device_index=dev_info["index"],
            frames_per_buffer=_FRAMES_PER_BUFFER,
            stream_callback=callback,
            start=False,
        )
        self._streams.append(stream)

    def _start_keepalive(self):
        """Afspil konstant, uhørlig stilhed på melodikildens enhed.

        WASAPI-loopback leverer kun data, når der faktisk renderes lyd. Uden
        det her forsvinder de sekunder, hvor musikken tier — pause i videoen,
        buffering, reklame — helt ud af melodisporet, og alt derefter ligger
        for tidligt. Ved at holde endpointet vågent kan hullerne slet ikke
        opstå.

        Fejler det, må optagelsen ikke vælte: hul-udfyldningen i _WavWriter
        er sikkerhedsnettet, og loggen afslører at vi er havnet der.
        """
        pyaudio = self._pa_module
        try:
            device = self._loop_info["index"]
            # Loopback-enheden peger på det render-endpoint vi vil holde vågent
            rate = int(self._loop_info["defaultSampleRate"])
            channels = max(1, int(self._loop_info.get("maxOutputChannels") or 2))
            silence = bytes(2 * channels * 1024)

            def callback(in_data, frame_count, time_info, status):
                return (silence[: 2 * channels * frame_count], pyaudio.paContinue)

            self._keepalive = self._pa.open(
                format=pyaudio.paInt16,
                channels=channels,
                rate=rate,
                output=True,
                output_device_index=device,
                frames_per_buffer=1024,
                stream_callback=callback,
                start=False,
            )
            log.info("Stilheds-afspilning klar på melodikilden (%d kanaler, "
                     "%d Hz) — holder lydenheden vågen", channels, rate)
        except Exception as exc:
            self._keepalive = None
            log.warning("Kunne ikke holde lydenheden vågen (%s) — huller i "
                        "melodisporet fyldes i stedet ud bagefter", exc)

    def start(self, out_dir: str):
        if self._mic_info is None:
            raise CaptureError(
                "Ingen mikrofon fundet. Tilslut en mikrofon og prøv igen."
            )

        mic_rate = int(self._mic_info["defaultSampleRate"])
        mic_ch = min(2, max(1, int(self._mic_info["maxInputChannels"])))
        self.mic_path = os.path.join(out_dir, MIC_FILENAME)
        self.mic_writer = _WavWriter(self.mic_path, mic_ch, mic_rate)
        self._open_stream(self._mic_info, self.mic_writer, mic_ch, mic_rate)

        if self._loop_info is not None:
            loop_rate = int(self._loop_info["defaultSampleRate"])
            loop_ch = max(1, int(self._loop_info["maxInputChannels"]))
            self.loop_path = os.path.join(out_dir, LOOP_FILENAME)
            self.loop_writer = _WavWriter(self.loop_path, loop_ch, loop_rate)
            self._open_stream(self._loop_info, self.loop_writer, loop_ch, loop_rate)
            self._start_keepalive()

        # Alt er klar — ét fælles nulpunkt, og så i gang. Stilheds-afspilningen
        # startes FØRST, så endpointet allerede er vågent, når loopbacken
        # begynder at lytte; ellers ville det første stykke stadig mangle.
        if self._keepalive is not None:
            try:
                self._keepalive.start_stream()
            except OSError as exc:
                log.warning("Stilheds-afspilningen kunne ikke starte: %s", exc)
                self._keepalive = None

        start_ts = time.monotonic()
        for writer in self.writers:
            writer.begin(start_ts)
        for stream in self._streams:
            stream.start_stream()

    def stop(self):
        # Stop alle streams FØR nogen lukkes: close() kan tage tid, og imens
        # ville den anden stream optage videre og forurene sporlængderne
        for stream in self._streams:
            try:
                stream.stop_stream()
            except OSError:
                pass
        for stream in self._streams:
            try:
                stream.close()
            except OSError:
                pass
        self._streams.clear()

        # Stilheds-afspilningen skal først slippe enheden, når der ikke
        # optages mere — ellers kunne endpointet nå at sove i halen
        if self._keepalive is not None:
            try:
                self._keepalive.stop_stream()
                self._keepalive.close()
            except OSError:
                pass
            self._keepalive = None

        for writer in self.writers:
            writer.close()
        return self._take_stats()

    def close(self):
        self.stop()
        self._pa.terminate()


class _DevBackend(_Backend):
    """sounddevice: kun mikrofon — til udvikling/test på macOS/Linux."""

    def __init__(self, mic_name: str | None = None):
        import sounddevice as sd

        super().__init__()
        self._sd = sd
        self._mic_name = mic_name
        self._stream = None

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
        self.mic_path = os.path.join(out_dir, MIC_FILENAME)
        self.mic_writer = _WavWriter(self.mic_path, channels, rate)
        writer = self.mic_writer

        def callback(indata, frames, time_info, status):
            writer.mark_first_buffer()
            if status and status.input_overflow:
                writer.overflows += 1
            writer.write(bytes(indata))

        self._stream = sd.RawInputStream(
            device=index,
            samplerate=rate,
            channels=channels,
            dtype="int16",
            blocksize=_FRAMES_PER_BUFFER,
            callback=callback,
        )
        writer.begin(time.monotonic())
        self._stream.start()

    def stop(self):
        if self._stream is not None:
            self._stream.stop()
            self._stream.close()
            self._stream = None
        if self.mic_writer is not None:
            self.mic_writer.close()
        return self._take_stats()

    def close(self):
        self.stop()
