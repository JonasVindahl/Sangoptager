"""Hovedvinduet: Optag/Stop, niveaumetre, balance, timer og gem-flowet."""

from __future__ import annotations

import datetime
import os
import shutil
import sys
import time
import wave

from PySide6.QtCore import Qt, QThread, QTimer, QUrl, Signal
from PySide6.QtGui import (
    QDesktopServices,
    QGuiApplication,
    QKeySequence,
    QShortcut,
)
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QStatusBar,
    QVBoxLayout,
    QWidget,
)

from ..audio.capture import (
    LOOP_FILENAME,
    MIC_FILENAME,
    CaptureError,
    DualRecorder,
    RecordingResult,
)
from ..audio.mixdown import MixdownError, mixdown
from ..library import (
    album_folder,
    build_filename,
    collect_titles,
    retag_folder,
    unique_path,
)
from .. import __version__
from ..logsetup import log
from ..rawarchive import archive_recording
from ..settings import Settings, temp_recording_dir
from ..update import (
    RELEASES_PAGE,
    UpdateCheckWorker,
    UpdateDownloadWorker,
    UpdateInfo,
    can_self_update,
    install_dir,
    install_dir_writable,
    launch_updater,
    mark_update_pending,
    take_pending_update,
    update_took_effect,
)
from .save_dialog import SaveDialog
from .settings_dialog import SettingsDialog
from .widgets import BalanceSlider, LevelMeter, RecordButton


class SaveWorker(QThread):
    """Mixer i temp, verificerer, flytter til sync-mappen og re-tagger albummet."""

    done = Signal(str)
    failed = Signal(str)

    def __init__(self, result: RecordingResult, title: str, balance: float,
                 settings: Settings, parent=None):
        super().__init__(parent)
        self._result = result
        self._title = title
        self._balance = balance
        self._settings = settings

    def run(self):
        try:
            now = datetime.datetime.now()
            dest_dir = album_folder(self._settings.output_dir, now)
            album = os.path.basename(dest_dir)
            os.makedirs(dest_dir, exist_ok=True)

            tmp_mp3 = os.path.join(temp_recording_dir(), "mix.mp3")
            mixdown(self._result.mic_path, self._result.loop_path,
                    tmp_mp3, self._balance, self._settings.normalize)

            dest = unique_path(
                os.path.join(dest_dir, build_filename(self._title, now)))
            shutil.move(tmp_mp3, dest)

            total = retag_folder(dest_dir, album, self._settings.artist)

            # MP3'en er på plads — flyt de rå spor til "sort boks"-arkivet,
            # så en skæv optagelse kan re-mixes i stedet for at synges om
            archive_recording(
                self._result.mic_path, self._result.loop_path,
                now.strftime("%Y-%m-%d_%H-%M-%S_") + self._title,
                dict(titel=self._title, destination=dest,
                     balance=self._balance, normalize=self._settings.normalize,
                     udfyldte_huller=self._result.loop_gaps.count,
                     udfyldt_sek=round(self._result.loop_gaps.seconds, 2),
                     overflows=self._result.overflows),
            )
            _cleanup_temp()
            log.info("Gemt '%s' → %s (balance=%.2f, normalize=%s)",
                     self._title, dest, self._balance,
                     self._settings.normalize)
            self.done.emit(f"✓ Gemt — {total} sange i {album}")
        except (MixdownError, OSError) as exc:
            log.error("Gem fejlede for '%s': %s", self._title, exc)
            self.failed.emit(str(exc))


def _cleanup_temp():
    shutil.rmtree(temp_recording_dir(), ignore_errors=True)


class TitlesWorker(QThread):
    """Skanner biblioteket for eksisterende titler uden at fryse UI'et —
    et bibliotek på et netværksdrev kan være længe om at svare."""

    done = Signal(list)

    def __init__(self, root: str, parent=None):
        super().__init__(parent)
        self._root = root

    def run(self):
        try:
            self.done.emit(collect_titles(self._root))
        except OSError as exc:
            log.warning("Kunne ikke læse titler fra biblioteket: %s", exc)


def _pending_recording() -> RecordingResult | None:
    """Ligger der en ikke-gemt optagelse fra en tidligere (crashet) kørsel?"""
    tmp = temp_recording_dir()
    mic = os.path.join(tmp, MIC_FILENAME)
    loop = os.path.join(tmp, LOOP_FILENAME)
    if not os.path.isfile(mic):
        return None
    try:
        with wave.open(mic, "rb") as wf:
            duration = wf.getnframes() / (wf.getframerate() or 1)
    except (OSError, wave.Error):
        return None
    if duration < 1.0:
        return None
    return RecordingResult(mic, loop if os.path.isfile(loop) else None, duration)


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.settings = Settings.load()
        self.recorder: DualRecorder | None = None
        self.recording = False
        self.pending: RecordingResult | None = None
        self._worker: SaveWorker | None = None
        self._elapsed = 0.0
        self._titles: list[str] = []
        self._titles_worker: TitlesWorker | None = None
        self._failed_result: RecordingResult | None = None
        self._started_at = 0.0
        self._disk_warned = False

        self._build_ui()
        if self.settings.window_geometry:
            try:
                self.restoreGeometry(
                    bytes.fromhex(self.settings.window_geometry)
                )
            except ValueError:
                pass
            self._ensure_on_screen()
        self._init_recorder()
        self._refresh_titles()

        self._poll = QTimer(self)
        self._poll.setInterval(80)
        self._poll.timeout.connect(self._update_live)
        self._poll.start()

        QTimer.singleShot(200, self._offer_recovery)

        self._update_check: UpdateCheckWorker | None = None
        self._update_dl: UpdateDownloadWorker | None = None
        self._failed_update_tag: str | None = None
        self._check_previous_update()
        if can_self_update():
            QTimer.singleShot(3000, self._check_updates)

    # ── UI ──────────────────────────────────────────────────────────────────

    def _build_ui(self):
        self.setWindowTitle("Sangoptager")
        self.setWindowFlags(self.windowFlags() | Qt.WindowStaysOnTopHint)
        self.setMinimumSize(420, 380)

        central = QWidget(self)
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)
        layout.setContentsMargins(16, 14, 16, 12)
        layout.setSpacing(12)
        self._root_layout = layout

        header = QHBoxLayout()
        title = QLabel("Sangoptager")
        title.setObjectName("titleLabel")
        self._title_label = title
        header.addWidget(title)

        # Synligt versionsnummer: så man kan se med det samme, om en
        # opdatering rent faktisk er slået igennem
        self.version_label = QLabel(f"v{__version__}")
        self.version_label.setObjectName("versionLabel")
        self.version_label.setToolTip("Installeret version")
        header.addWidget(self.version_label)

        header.addStretch(1)
        gear = QPushButton("⚙")
        gear.setObjectName("gear")
        gear.setFixedSize(32, 32)
        gear.setToolTip("Indstillinger")
        gear.setFocusPolicy(Qt.NoFocus)
        gear.setCursor(Qt.PointingHandCursor)
        gear.clicked.connect(self._open_settings)
        header.addWidget(gear)
        layout.addLayout(header)

        self.device_label = QLabel("")
        self.device_label.setObjectName("deviceLabel")
        self.device_label.setWordWrap(True)
        layout.addWidget(self.device_label)

        # Opdaterings-banner — skjult indtil en ny version er fundet
        self.update_banner = QWidget()
        self.update_banner.setObjectName("updateBanner")
        banner_layout = QHBoxLayout(self.update_banner)
        banner_layout.setContentsMargins(12, 8, 8, 8)
        self.update_label = QLabel("")
        self.update_label.setObjectName("updateLabel")
        banner_layout.addWidget(self.update_label, stretch=1)
        self.update_btn = QPushButton("Opdatér nu")
        self.update_btn.setObjectName("primary")
        self.update_btn.clicked.connect(self._start_update)
        banner_layout.addWidget(self.update_btn)
        self.update_banner.hide()
        layout.addWidget(self.update_banner)

        self.record_btn = RecordButton()
        self.record_btn.clicked.connect(self._toggle)
        # stretch=1: al ekstra lodret plads ved resize går til optage-knappen
        layout.addWidget(self.record_btn, 1)

        self.timer_label = QLabel("00:00")
        self.timer_label.setObjectName("timerLabel")
        self.timer_label.setAlignment(Qt.AlignHCenter)
        layout.addWidget(self.timer_label)

        card = QWidget()
        card.setObjectName("card")
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(14, 14, 14, 14)
        card_layout.setSpacing(12)
        self._card_layout = card_layout

        self.mic_meter = LevelMeter("Stemme")
        self.loop_meter = LevelMeter("Melodi")
        card_layout.addWidget(self.mic_meter)
        card_layout.addWidget(self.loop_meter)

        self.balance = BalanceSlider(self.settings.balance)
        self.balance.valueChanged.connect(self._balance_changed)
        card_layout.addWidget(self.balance)
        layout.addWidget(card)

        self.status = QStatusBar(self)
        self.setStatusBar(self.status)
        self.status.showMessage("Klar")

        QShortcut(QKeySequence(Qt.Key_Space), self, activated=self._toggle)

        self._scale = 1.0
        self._apply_scale(1.0)

    # ── Proportional skalering ─────────────────────────────────────────────

    _BASE_HEIGHT = 430  # design-højden; skalafaktor = vindueshøjde / denne

    def resizeEvent(self, event):
        super().resizeEvent(event)
        s = min(1.7, max(0.9, self.height() / self._BASE_HEIGHT))
        if abs(s - self._scale) > 0.03:
            self._scale = s
            self._apply_scale(s)

    def _apply_scale(self, s: float):
        """Skalér typografi, metre og luft, så appens proportioner holder."""
        self._title_label.setStyleSheet(f"font-size: {round(16 * s)}px;")
        self.version_label.setStyleSheet(f"font-size: {round(11 * s)}px;")
        self.device_label.setStyleSheet(f"font-size: {round(11 * s)}px;")
        self.timer_label.setStyleSheet(f"font-size: {round(40 * s)}px;")
        self.mic_meter.set_scale(s)
        self.loop_meter.set_scale(s)
        self.balance.set_scale(s)
        self._root_layout.setContentsMargins(
            round(16 * s), round(14 * s), round(16 * s), round(12 * s))
        self._root_layout.setSpacing(round(12 * s))
        self._card_layout.setContentsMargins(*(round(14 * s),) * 4)
        self._card_layout.setSpacing(round(12 * s))

    def _ensure_on_screen(self):
        """Var vinduet sidst på en skærm der nu er koblet fra, ville det ligge
        usynligt uden for skrivebordet — centrér det i stedet."""
        frame = self.frameGeometry()
        for screen in QGuiApplication.screens():
            if screen.availableGeometry().intersects(frame):
                return
        screen = QGuiApplication.primaryScreen()
        if screen is not None:
            frame.moveCenter(screen.availableGeometry().center())
            self.move(frame.topLeft())
            log.info("Gemt vinduesplacering var uden for skærmen — centreret")

    def _refresh_titles(self):
        """Opdatér autocomplete-listen i baggrunden (kaldes ved start og gem)."""
        if self._titles_worker is not None and self._titles_worker.isRunning():
            return
        self._titles_worker = TitlesWorker(self.settings.output_dir, self)
        self._titles_worker.done.connect(self._titles_ready)
        self._titles_worker.finished.connect(self._titles_finished)
        self._titles_worker.start()

    def _titles_ready(self, titles: list):
        self._titles = titles

    def _titles_finished(self):
        """Slip tråden, når den er færdig. Referencen SKAL ryddes samtidig —
        ellers kalder næste _refresh_titles isRunning() på et slettet
        C++-objekt, og PySide6 rejser RuntimeError."""
        worker = self._titles_worker
        self._titles_worker = None
        if worker is not None:
            worker.deleteLater()

    def _open_settings(self):
        mics = self.recorder.list_mics() if self.recorder else []
        loopbacks = self.recorder.list_loopbacks() if self.recorder else []
        old_output = self.settings.output_dir
        dialog = SettingsDialog(self.settings, mics, loopbacks, self,
                                on_update_found=self._update_found)
        dialog.exec()
        if dialog.devices_changed and not self.recording:
            if self.recorder:
                self.recorder.close()
                self.recorder = None
            self._init_recorder()
        if self.settings.output_dir != old_output:
            self._refresh_titles()

    def _init_recorder(self):
        try:
            self.recorder = DualRecorder(
                mic_name=self.settings.mic_device,
                loop_name=self.settings.loop_device,
            )
            self.device_label.setText(self.recorder.device_summary())
            self.loop_meter.set_enabled_look(self.recorder.has_loopback)
        except Exception as exc:  # manglende driver/enhed må ikke crashe appen
            self.recorder = None
            self.device_label.setText(f"⚠ Lydfejl: {exc}")
            log.error("Kunne ikke initialisere lydsystemet: %s", exc)

    # ── Live-opdatering ────────────────────────────────────────────────────

    # Sekunder uden mic-data før vagthunden slår alarm
    _MIC_STALL_S = 2.0

    def _update_live(self):
        if self.recording and self.recorder:
            # Ur-tid, ikke summerede poll-intervaller: et hakkende UI må ikke
            # få timeren til at vise mindre end der faktisk er optaget
            self._elapsed = time.monotonic() - self._started_at
            mins, secs = divmod(int(self._elapsed), 60)
            self.timer_label.setText(f"{mins:02d}:{secs:02d}")
            self.mic_meter.set_level(self.recorder.mic_level)
            self.loop_meter.set_level(self.recorder.loop_level)
            self._watch_mic()
        elif not self.recording:
            self.mic_meter.reset()
            if self.recorder is None or self.recorder.has_loopback:
                self.loop_meter.reset()

    def _watch_mic(self):
        """Alarm hvis mikrofonen holder op med at levere data midt i optagelsen
        (USB hevet ud, Bluetooth død), eller hvis disken svigter. Kun mic —
        loopback er legitimt stille, når PC'en ikke afspiller noget."""
        if self.recorder.disk_failed and not self._disk_warned:
            self._disk_warned = True
            self.status.showMessage(
                "⚠ KAN IKKE GEMME TIL DISKEN — stop og tjek pladsen!")
            log.error("Disk-skrivning svigtede under optagelse")
            return

        bytes_now = self.recorder.mic_bytes
        if bytes_now != self._mic_watch_bytes:
            self._mic_watch_bytes = bytes_now
            self._mic_watch_stall = 0.0
            if self._mic_stalled:
                self._mic_stalled = False
                self.status.showMessage("Optager…")
                log.info("Mikrofonen leverer data igen")
            return
        self._mic_watch_stall += self._poll.interval() / 1000.0
        if self._mic_watch_stall >= self._MIC_STALL_S and not self._mic_stalled:
            self._mic_stalled = True
            self.status.showMessage(
                "⚠ MIKROFONEN SENDER INGEN LYD — tjek forbindelsen!")
            log.warning("Mikrofonen er holdt op med at levere data under "
                        "optagelse (%.0f s uden buffere)", self._mic_watch_stall)

    def _balance_changed(self):
        self.settings.balance = self.balance.value
        self.settings.save()

    # ── Optag/Stop ─────────────────────────────────────────────────────────

    def _toggle(self):
        if self._worker is not None and self._worker.isRunning():
            self.status.showMessage("Vent — er ved at gemme…")
            return
        if self.pending is not None:
            self._resolve_pending()
            return
        if not self.recording:
            self._start_recording()
        else:
            self._stop_recording()

    # To rå spor i 48 kHz/16-bit stereo fylder ca. 0,4 MB/sek. tilsammen;
    # 500 MB rækker til over 20 minutters sang
    _MIN_FREE_MB = 500

    def _check_disk_space(self) -> bool:
        """Advar før optagelsen, hvis der næsten ikke er plads til de rå spor."""
        try:
            free_mb = shutil.disk_usage(
                os.path.dirname(temp_recording_dir())).free // (1024 * 1024)
        except OSError as exc:
            log.warning("Kunne ikke tjekke diskplads: %s", exc)
            return True
        if free_mb >= self._MIN_FREE_MB:
            return True
        log.warning("Lav diskplads før optagelse: %d MB fri", free_mb)
        answer = QMessageBox.warning(
            self, "Lidt plads tilbage",
            f"Der er kun {free_mb} MB fri plads på disken.\n"
            "En lang optagelse kan løbe tør undervejs.\n\nOptag alligevel?",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes,
        )
        return answer == QMessageBox.Yes

    def _start_recording(self):
        if self.recorder is None:
            self._init_recorder()
            if self.recorder is None:
                QMessageBox.critical(self, "Lydfejl",
                                     "Kunne ikke starte lydsystemet.\n"
                                     + self.device_label.text())
                return
        _cleanup_temp()
        if not self._check_disk_space():
            return
        try:
            self.recorder.start(temp_recording_dir())
        except CaptureError as exc:
            QMessageBox.critical(self, "Kan ikke optage", str(exc))
            return
        self.recording = True
        self._elapsed = 0.0
        self._started_at = time.monotonic()
        self._mic_watch_bytes = -1
        self._mic_watch_stall = 0.0
        self._mic_stalled = False
        self._disk_warned = False
        self.record_btn.set_state("recording")
        self.status.showMessage("Optager…")
        log.info("Optagelse startet (%s)", self.recorder.device_summary())

    def _stop_recording(self):
        self.pending = self.recorder.stop()
        self.recording = False
        self.record_btn.set_state("idle")
        self.status.showMessage("Optagelse stoppet")
        p = self.pending
        log.info("Optagelse stoppet: %.1f sek, peak stemme=%s melodi=%s, "
                 "overflows=%d, sporlængder=%s/%s sek",
                 p.duration, p.mic_peak, p.loop_peak,
                 p.overflows, p.mic_seconds, p.loop_seconds)
        if p.loop_gaps:
            log.info("Melodien tav %d gang(e), i alt %.1f sek — fyldt ud med "
                     "stilhed, så sporet ikke mister tid", p.loop_gaps.count,
                     p.loop_gaps.seconds)
        if p.mic_gaps:
            log.warning("Mikrofonen tav %d gang(e), i alt %.1f sek — usædvanligt",
                        p.mic_gaps.count, p.mic_gaps.seconds)
        # Sporene skal nu dække samme tidsrum uanset hvad. Gør de ikke det,
        # er der noget, udfyldningen ikke fangede
        if p.mic_seconds and p.loop_seconds \
                and abs(p.mic_seconds - p.loop_seconds) > 0.25:
            log.warning("Sporlængder afviger %.0f ms trods udfyldning — "
                        "melodien kan ligge forskudt i denne optagelse",
                        abs(p.mic_seconds - p.loop_seconds) * 1000)
        self._resolve_pending()

    def _resolve_pending(self):
        if self.pending is None:
            return
        dialog = SaveDialog(
            result=self.pending,
            balance=self.settings.balance,
            normalize=self.settings.normalize,
            titles=self._titles,
            parent=self,
        )
        dialog.exec()
        action = dialog.result_action or ("keep",)

        if action[0] == "save":
            _, title, balance = action
            self.settings.balance = balance
            self.settings.save()
            self._save(self.pending, title, balance)
            self.pending = None
        elif action[0] == "delete":
            _cleanup_temp()
            self.pending = None
            self.record_btn.set_state("idle")
            self.status.showMessage("Optagelse slettet")
        else:  # keep — brugeren lukkede dialogen
            self.status.showMessage(
                "Optagelsen er IKKE gemt — tryk på knappen for at gemme eller slette"
            )
            self.record_btn.set_state("resolve")

    # ── Gem i baggrunden ───────────────────────────────────────────────────

    def _save(self, result: RecordingResult, title: str, balance: float):
        self.status.showMessage(f"Gemmer '{title}'…")
        self.record_btn.set_state("busy")
        self._failed_result = result  # bevares hvis gemmet fejler
        self._worker = SaveWorker(result, title, balance, self.settings, self)
        self._worker.done.connect(self._save_done)
        self._worker.failed.connect(self._save_failed)
        self._worker.start()

    def _save_done(self, message: str):
        self.record_btn.set_state("idle")
        self.status.showMessage(message)
        self._refresh_titles()

    def _save_failed(self, message: str):
        self.status.showMessage("⚠ Kunne ikke gemme")
        # De rå WAV-spor er bevaret, så intet er tabt — prøv igen. Behold det
        # oprindelige resultat, så offset-kompensation og advarsler ikke går
        # tabt ved andet forsøg; kun hvis sporene er væk, læses de fra disk.
        if self._failed_result is not None and \
                self._failed_result.mic_path is not None and \
                os.path.isfile(self._failed_result.mic_path):
            self.pending = self._failed_result
        else:
            self.pending = _pending_recording()
        self._failed_result = None
        self.record_btn.set_state("resolve" if self.pending else "idle")
        QMessageBox.critical(
            self, "Kunne ikke gemme",
            f"{message}\n\nOptagelsen er ikke slettet — prøv at gemme igen.",
        )

    # ── Selv-opdatering ────────────────────────────────────────────────────

    def _check_updates(self):
        self._update_check = UpdateCheckWorker(self)
        self._update_check.found.connect(self._update_found)
        self._update_check.start()

    def _check_previous_update(self):
        """Slog sidste opdateringsforsøg overhovedet igennem?

        Uden det her kan en updater, der fejler i tavshed, blive ved med at
        tilbyde den samme version ved hver opstart — appen genstarter jo som
        den gamle udgave og finder "en ny version" igen.
        """
        marker = take_pending_update()
        if marker is None:
            return
        tag = marker.get("tag", "?")
        if update_took_effect(marker):
            log.info("Opdatering til %s gennemført", tag)
            self.status.showMessage(f"✓ Opdateret til {tag}")
            return
        self._failed_update_tag = tag
        log.error("Opdatering til %s slog IKKE igennem — kører stadig v%s "
                  "fra %s", tag, __version__, install_dir())
        self._show_manual_update_banner(tag)

    def _set_update_button(self, text: str, handler):
        """Sæt bannerknappens tekst og handling — den skifter mellem
        'Opdatér nu' og 'Hent manuelt', så begge dele skal nulstilles samlet."""
        self.update_btn.setText(text)
        try:
            self.update_btn.clicked.disconnect()
        except (RuntimeError, TypeError):
            pass
        self.update_btn.clicked.connect(handler)
        self.update_btn.setEnabled(True)
        self.update_btn.show()

    def _show_manual_update_banner(self, tag: str):
        """Selv-opdateringen virker ikke her — send brugeren til download."""
        self.update_label.setText(
            f"Opdateringen til {tag} gik ikke igennem — appen kører stadig "
            f"v{__version__}. Hent den nye version manuelt."
        )
        self._set_update_button("Hent manuelt", self._open_releases_page)
        self.update_banner.show()

    def _open_releases_page(self):
        QDesktopServices.openUrl(QUrl(RELEASES_PAGE))

    def _update_found(self, info: UpdateInfo):
        self._update_info = info
        if not can_self_update():
            # Udviklingskørsel (ikke den byggede exe): vis kun beskeden —
            # updateren ville ellers forsøge at udskifte Pythons egen mappe
            self.update_label.setText(
                f"Ny version {info.tag} findes — hent den fra GitHub."
            )
            self._set_update_button("Hent manuelt", self._open_releases_page)
            self.update_banner.show()
            return
        if self._failed_update_tag == info.tag:
            # Selv-opdatering til netop denne version er allerede prøvet og
            # mislykkedes — tilbyd ikke den samme knap igen
            self._show_manual_update_banner(info.tag)
            return
        if not install_dir_writable():
            # Fx pakket ud i Program Files: sig det i stedet for at hente
            # hele zippen og fejle bagefter
            self.update_label.setText(
                f"Ny version {info.tag} findes, men appen kan ikke opdatere "
                f"sig selv her ({install_dir()}). Flyt mappen til fx "
                "C:\\Sangoptager."
            )
            self.update_btn.hide()
        else:
            self.update_label.setText(f"Ny version {info.tag} er klar")
            self._set_update_button("Opdatér nu", self._start_update)
        self.update_banner.show()

    def _start_update(self):
        if self.recording or self.pending is not None or (
                self._worker is not None and self._worker.isRunning()):
            self.status.showMessage(
                "Gør optagelsen færdig først — opdatér bagefter")
            return
        self.update_btn.setEnabled(False)
        self.update_label.setText("Henter opdatering… 0%")
        self._update_dl = UpdateDownloadWorker(self._update_info, self)
        self._update_dl.progress.connect(
            lambda pct: self.update_label.setText(f"Henter opdatering… {pct}%"))
        self._update_dl.ready.connect(self._apply_update)
        self._update_dl.failed.connect(self._update_failed)
        self._update_dl.start()

    def _apply_update(self, new_dir: str, tmp_root: str):
        self.update_label.setText("Genstarter…")
        # Notér hvad vi forsøger, så næste opstart kan afsløre, om exe'en
        # rent faktisk blev udskiftet
        mark_update_pending(self._update_info.tag)
        launch_updater(new_dir, tmp_root)
        self.close()

    def _update_failed(self, message: str):
        self.update_label.setText("Opdatering mislykkedes — prøver igen næste gang")
        self.update_btn.setEnabled(True)
        self.status.showMessage(f"⚠ Opdatering: {message}")

    # ── Gendannelse efter crash ────────────────────────────────────────────

    def _offer_recovery(self):
        if self.pending is not None or self.recording:
            return
        recovered = _pending_recording()
        if recovered is None:
            return
        answer = QMessageBox.question(
            self, "Ikke-gemt optagelse",
            "Der ligger en optagelse fra sidst, som aldrig blev gemt.\n"
            "Vil du gemme den nu?",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes,
        )
        if answer == QMessageBox.Yes:
            self.pending = recovered
            self._resolve_pending()
        else:
            _cleanup_temp()

    # ── Luk ────────────────────────────────────────────────────────────────

    def closeEvent(self, event):
        self.settings.window_geometry = bytes(self.saveGeometry()).hex()
        self.settings.save()
        if self.recording and self.recorder:
            self.pending = self.recorder.stop()
            self.recording = False
            self._resolve_pending()
        if self._worker is not None and self._worker.isRunning():
            self._worker.wait(30_000)
        if self.recorder:
            self.recorder.close()
        event.accept()
