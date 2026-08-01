"""Dialogen efter Stop: navngiv og Gem — eller Slet. Med prøvelyt og
advarsel, hvis et af sporene var nær-stille under optagelsen."""

from __future__ import annotations

import os

from PySide6.QtCore import Qt, QThread, QUrl, Signal
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
)

from ..audio.capture import RecordingResult
from ..audio.mixdown import MixdownError, mixdown
from ..library import sanitize_title
from ..logsetup import log
from ..settings import temp_recording_dir
from . import theme
from .widgets import BalanceSlider

try:
    from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
    HAS_AUDIO = True
except ImportError:  # QtMultimedia mangler — skjul blot prøvelyt-knappen
    HAS_AUDIO = False

# Under denne peak-RMS regnes et spor for (nær-)stille — ca. -34 dB
SILENCE_PEAK = 0.02


class _PreviewWorker(QThread):
    done = Signal(str)
    failed = Signal(str)

    def __init__(self, result: RecordingResult, balance: float, normalize: bool,
                 parent=None):
        super().__init__(parent)
        self._result = result
        self._balance = balance
        self._normalize = normalize

    def run(self):
        try:
            path = os.path.join(temp_recording_dir(), "preview.mp3")
            mixdown(self._result.mic_path, self._result.loop_path, path,
                    self._balance, self._normalize)
            self.done.emit(path)
        except MixdownError as exc:
            self.failed.emit(str(exc))


class SaveDialog(QDialog):
    """Returnerer via .result_action: ('save', titel, balance) eller ('delete',)."""

    def __init__(self, result: RecordingResult, balance: float, normalize: bool,
                 parent=None):
        super().__init__(parent)
        self.setWindowTitle("Gem optagelse")
        self.setWindowFlags(self.windowFlags() | Qt.WindowStaysOnTopHint)
        self.setMinimumWidth(380)
        self.result_action: tuple | None = None
        self._result = result
        self._normalize = normalize
        self._player = None
        self._audio_out = None
        self._preview_worker: _PreviewWorker | None = None
        self._preview_path: str | None = None
        self._preview_balance: float | None = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 18, 18, 16)
        layout.setSpacing(12)

        mins, secs = divmod(int(result.duration), 60)
        info = QLabel(f"Optagelse på {mins:02d}:{secs:02d} — hvad hedder sangen?")
        layout.addWidget(info)

        for warning in self._warnings():
            banner = QLabel(f"⚠  {warning}")
            banner.setWordWrap(True)
            banner.setStyleSheet(
                f"background: rgba(245, 165, 36, 0.14); color: {theme.AMBER};"
                f"border: 1px solid {theme.AMBER}; border-radius: 8px;"
                "padding: 8px 10px; font-weight: 600;"
            )
            layout.addWidget(banner)

        self._title_edit = QLineEdit()
        self._title_edit.setPlaceholderText("Sangens navn…")
        font = self._title_edit.font()
        font.setPointSize(font.pointSize() + 4)
        self._title_edit.setFont(font)
        layout.addWidget(self._title_edit)

        self._balance = BalanceSlider(balance)
        if result.loop_path is not None:
            layout.addWidget(self._balance)

        buttons = QHBoxLayout()
        delete_btn = QPushButton("Slet optagelse")
        delete_btn.setObjectName("danger")
        delete_btn.clicked.connect(self._on_delete)
        buttons.addWidget(delete_btn)
        buttons.addStretch(1)

        if HAS_AUDIO:
            self._listen_btn = QPushButton("▶  Lyt")
            self._listen_btn.setToolTip(
                "Hør mixet med den valgte balance, før du gemmer"
            )
            self._listen_btn.clicked.connect(self._on_listen)
            buttons.addWidget(self._listen_btn)
        else:
            self._listen_btn = None

        save_btn = QPushButton("Gem")
        save_btn.setObjectName("primary")
        save_btn.setDefault(True)
        save_btn.setMinimumWidth(110)
        save_btn.clicked.connect(self._on_save)
        buttons.addWidget(save_btn)
        layout.addLayout(buttons)

        self._title_edit.returnPressed.connect(self._on_save)
        self._title_edit.setFocus()

        # Kun bredden må trækkes — højden følger indholdet
        self.setFixedHeight(self.sizeHint().height())

    # ── Advarsler ──────────────────────────────────────────────────────────

    def _warnings(self) -> list[str]:
        warnings = []
        peak = self._result.mic_peak
        if peak is not None and peak < SILENCE_PEAK:
            warnings.append(
                "Stemmen var meget lav eller helt stille under optagelsen — "
                "tjek mikrofonen, før du gemmer."
            )
        peak = self._result.loop_peak
        if self._result.loop_path is not None and peak is not None \
                and peak < SILENCE_PEAK:
            warnings.append(
                "Melodien var næsten stille — spillede musikken på PC'en?"
            )
        if warnings:
            log.warning("Stilheds-advarsel ved gem: %s", "; ".join(warnings))
        return warnings

    # ── Prøvelyt ───────────────────────────────────────────────────────────

    def _on_listen(self):
        if self._player is not None and \
                self._player.playbackState() == QMediaPlayer.PlayingState:
            self._stop_playback()
            return

        balance = self._balance.value
        if self._preview_path and self._preview_balance == balance:
            self._play(self._preview_path)
            return

        self._listen_btn.setEnabled(False)
        self._listen_btn.setText("…  Mixer")
        self._preview_worker = _PreviewWorker(
            self._result, balance, self._normalize, self
        )
        self._preview_balance = balance
        self._preview_worker.done.connect(self._preview_ready)
        self._preview_worker.failed.connect(self._preview_failed)
        self._preview_worker.start()

    def _preview_ready(self, path: str):
        self._preview_path = path
        self._listen_btn.setEnabled(True)
        self._play(path)

    def _preview_failed(self, message: str):
        self._listen_btn.setEnabled(True)
        self._listen_btn.setText("▶  Lyt")
        self._preview_balance = None
        QMessageBox.warning(self, "Prøvelyt", f"Kunne ikke lave prøvelyt:\n{message}")

    def _play(self, path: str):
        if self._player is None:
            self._player = QMediaPlayer(self)
            self._audio_out = QAudioOutput(self)
            self._player.setAudioOutput(self._audio_out)
            self._player.playbackStateChanged.connect(self._playback_changed)
        self._player.setSource(QUrl.fromLocalFile(path))
        self._player.play()

    def _playback_changed(self, state):
        if self._listen_btn is None:
            return
        if state == QMediaPlayer.PlayingState:
            self._listen_btn.setText("⏹  Stop")
        else:
            self._listen_btn.setText("▶  Lyt")

    def _stop_playback(self):
        if self._player is not None:
            self._player.stop()
            # Frigiv filhåndtaget, ellers kan temp-mappen ikke ryddes på Windows
            self._player.setSource(QUrl())

    # ── Gem/Slet ───────────────────────────────────────────────────────────

    def _on_save(self):
        title = sanitize_title(self._title_edit.text())
        if not title:
            QMessageBox.warning(self, "Mangler navn", "Skriv sangens navn først.")
            self._title_edit.setFocus()
            return
        self._finish(("save", title, self._balance.value))

    def _on_delete(self):
        answer = QMessageBox.question(
            self,
            "Slet optagelse",
            "Er du sikker på, at optagelsen skal slettes?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if answer == QMessageBox.Yes:
            self._finish(("delete",))

    def _finish(self, action: tuple):
        self._stop_playback()
        if self._preview_worker is not None and self._preview_worker.isRunning():
            self._preview_worker.wait(15_000)
        self.result_action = action
        self.accept()

    def closeEvent(self, event):
        self._stop_playback()
        if self._preview_worker is not None and self._preview_worker.isRunning():
            self._preview_worker.wait(15_000)
        # Luk med X = behold intet valg; hovedvinduet spørger igen næste gang
        if self.result_action is None:
            self.result_action = ("keep",)
        event.accept()
