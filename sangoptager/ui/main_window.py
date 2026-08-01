"""Hovedvinduet: Optag/Stop, niveaumetre, balance, timer og gem-flowet."""

from __future__ import annotations

import datetime
import os
import shutil
import wave

from PySide6.QtCore import Qt, QThread, QTimer, Signal
from PySide6.QtGui import QKeySequence, QShortcut
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
from ..library import album_folder, build_filename, retag_folder
from ..settings import Settings, temp_recording_dir
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
                    tmp_mp3, self._balance)

            dest = os.path.join(dest_dir, build_filename(self._title, now))
            shutil.move(tmp_mp3, dest)

            total = retag_folder(dest_dir, album, self._settings.artist)

            # Først NU er det sikkert at rydde de rå spor op
            _cleanup_temp()
            self.done.emit(f"✓ Gemt — {total} sange i {album}")
        except (MixdownError, OSError) as exc:
            self.failed.emit(str(exc))


def _cleanup_temp():
    shutil.rmtree(temp_recording_dir(), ignore_errors=True)


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

        self._build_ui()
        if self.settings.window_geometry:
            try:
                self.restoreGeometry(
                    bytes.fromhex(self.settings.window_geometry)
                )
            except ValueError:
                pass
        self._init_recorder()

        self._poll = QTimer(self)
        self._poll.setInterval(80)
        self._poll.timeout.connect(self._update_live)
        self._poll.start()

        QTimer.singleShot(200, self._offer_recovery)

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

        header = QHBoxLayout()
        title = QLabel("Sangoptager")
        title.setObjectName("titleLabel")
        header.addWidget(title)
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

    def _open_settings(self):
        mics = self.recorder.list_mics() if self.recorder else []
        loopbacks = self.recorder.list_loopbacks() if self.recorder else []
        dialog = SettingsDialog(self.settings, mics, loopbacks, self)
        dialog.exec()
        if dialog.devices_changed and not self.recording:
            if self.recorder:
                self.recorder.close()
                self.recorder = None
            self._init_recorder()

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

    # ── Live-opdatering ────────────────────────────────────────────────────

    def _update_live(self):
        if self.recording and self.recorder:
            self._elapsed += self._poll.interval() / 1000.0
            mins, secs = divmod(int(self._elapsed), 60)
            self.timer_label.setText(f"{mins:02d}:{secs:02d}")
            self.mic_meter.set_level(self.recorder.mic_level)
            self.loop_meter.set_level(self.recorder.loop_level)
        elif not self.recording:
            self.mic_meter.reset()
            if self.recorder is None or self.recorder.has_loopback:
                self.loop_meter.reset()

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

    def _start_recording(self):
        if self.recorder is None:
            self._init_recorder()
            if self.recorder is None:
                QMessageBox.critical(self, "Lydfejl",
                                     "Kunne ikke starte lydsystemet.\n"
                                     + self.device_label.text())
                return
        _cleanup_temp()
        try:
            self.recorder.start(temp_recording_dir())
        except CaptureError as exc:
            QMessageBox.critical(self, "Kan ikke optage", str(exc))
            return
        self.recording = True
        self._elapsed = 0.0
        self.record_btn.set_state("recording")
        self.status.showMessage("Optager…")

    def _stop_recording(self):
        self.pending = self.recorder.stop()
        self.recording = False
        self.record_btn.set_state("idle")
        self.status.showMessage("Optagelse stoppet")
        self._resolve_pending()

    def _resolve_pending(self):
        if self.pending is None:
            return
        dialog = SaveDialog(
            balance=self.settings.balance,
            duration=self.pending.duration,
            has_loopback=self.pending.loop_path is not None,
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
        self._worker = SaveWorker(result, title, balance, self.settings, self)
        self._worker.done.connect(self._save_done)
        self._worker.failed.connect(self._save_failed)
        self._worker.start()

    def _save_done(self, message: str):
        self.record_btn.set_state("idle")
        self.status.showMessage(message)

    def _save_failed(self, message: str):
        self.status.showMessage("⚠ Kunne ikke gemme")
        # De rå WAV-spor er bevaret, så intet er tabt — prøv igen
        self.pending = _pending_recording()
        self.record_btn.set_state("resolve" if self.pending else "idle")
        QMessageBox.critical(
            self, "Kunne ikke gemme",
            f"{message}\n\nOptagelsen er ikke slettet — prøv at gemme igen.",
        )

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
