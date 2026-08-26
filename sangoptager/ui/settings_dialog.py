"""Indstillinger: lydenheder, sync-mappe (Nextcloud/Syncthing) og kunstnernavn."""

from __future__ import annotations

import os

from PySide6.QtCore import QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
)

from .. import __version__
from ..logsetup import log
from ..settings import Settings
from ..update import UpdateCheckWorker

AUTO = "Automatisk (systemets standard)"


class SettingsDialog(QDialog):
    """Sat .devices_changed hvis mikrofon/melodikilde blev ændret."""

    def __init__(self, settings: Settings, mics: list[str], loopbacks: list[str],
                 parent=None, on_update_found=None):
        super().__init__(parent)
        self.setWindowTitle("Indstillinger")
        self.setMinimumWidth(460)
        self._settings = settings
        self.devices_changed = False
        self._on_update_found = on_update_found
        self._update_worker: UpdateCheckWorker | None = None

        form = QFormLayout(self)
        form.setVerticalSpacing(12)

        self._mic_combo = QComboBox()
        self._mic_combo.addItem(AUTO)
        self._mic_combo.addItems(mics)
        if settings.mic_device in mics:
            self._mic_combo.setCurrentText(settings.mic_device)
        form.addRow("Mikrofon:", self._mic_combo)

        self._loop_combo = QComboBox()
        self._loop_combo.addItem(AUTO)
        self._loop_combo.addItems(loopbacks)
        if settings.loop_device in loopbacks:
            self._loop_combo.setCurrentText(settings.loop_device)
        if loopbacks:
            form.addRow("Melodikilde:", self._loop_combo)
            hint = QLabel("Melodikilden er den højttaler/hovedtelefon, "
                          "melodien afspilles på.")
            hint.setObjectName("hintLabel")
            hint.setWordWrap(True)
            form.addRow("", hint)
        else:
            self._loop_combo.setEnabled(False)

        dir_row = QHBoxLayout()
        self._dir_edit = QLineEdit(settings.output_dir)
        browse = QPushButton("Gennemse…")
        browse.clicked.connect(self._browse)
        open_btn = QPushButton("Åbn")
        open_btn.setToolTip("Åbn mappen i stifinderen")
        open_btn.clicked.connect(self._open_folder)
        dir_row.addWidget(self._dir_edit, stretch=1)
        dir_row.addWidget(browse)
        dir_row.addWidget(open_btn)
        form.addRow("Gem sange i:", dir_row)

        self._artist_edit = QLineEdit(settings.artist)
        form.addRow("Kunstner (MP3-tag):", self._artist_edit)

        self._normalize_check = QCheckBox("Ensart lydstyrke på tværs af sange (anbefalet)")
        self._normalize_check.setChecked(settings.normalize)
        form.addRow("Lydstyrke:", self._normalize_check)

        update_row = QHBoxLayout()
        self._update_status = QLabel(f"Installeret version: v{__version__}")
        self._update_status.setObjectName("hintLabel")
        self._update_status.setWordWrap(True)
        self._check_btn = QPushButton("Søg nu")
        self._check_btn.setToolTip("Se om der er en nyere version")
        self._check_btn.clicked.connect(self._check_update)
        update_row.addWidget(self._update_status, stretch=1)
        update_row.addWidget(self._check_btn)
        form.addRow("Opdatering:", update_row)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText("Gem")
        buttons.button(QDialogButtonBox.Ok).setObjectName("primary")
        buttons.button(QDialogButtonBox.Cancel).setText("Annullér")
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)

    # ── Manuelt opdaterings-tjek ───────────────────────────────────────────

    def _check_update(self):
        if self._update_worker is not None and self._update_worker.isRunning():
            return
        self._check_btn.setEnabled(False)
        self._update_status.setText("Søger efter opdatering…")
        self._update_worker = UpdateCheckWorker(self)
        self._update_worker.found.connect(self._update_available)
        self._update_worker.up_to_date.connect(self._update_none)
        self._update_worker.failed.connect(self._update_check_failed)
        self._update_worker.start()

    def _update_available(self, info):
        self._update_status.setText(
            f"Ny version {info.tag} er klar — luk vinduet her og brug "
            "banneret i hovedvinduet."
        )
        self._check_btn.setEnabled(True)
        if self._on_update_found is not None:
            self._on_update_found(info)

    def _update_none(self):
        self._update_status.setText(
            f"v{__version__} er den nyeste — alt er opdateret."
        )
        self._check_btn.setEnabled(True)

    def _update_check_failed(self, message: str):
        # Den tekniske fejl hører hjemme i loggen, ikke i dialogen
        log.info("Manuelt opdaterings-tjek fejlede: %s", message)
        self._update_status.setText(
            f"Kunne ikke tjekke lige nu (er der internet?) — v{__version__}"
        )
        self._check_btn.setEnabled(True)

    def done(self, result):
        # QThread må ikke destrueres mens den kører — vent den ud først
        if self._update_worker is not None and self._update_worker.isRunning():
            self._update_worker.wait(10_000)
        super().done(result)

    def _browse(self):
        chosen = QFileDialog.getExistingDirectory(
            self, "Vælg mappe til sangene", self._dir_edit.text()
        )
        if chosen:
            self._dir_edit.setText(chosen)

    def _open_folder(self):
        path = self._dir_edit.text().strip()
        if os.path.isdir(path):
            QDesktopServices.openUrl(QUrl.fromLocalFile(path))

    def _combo_value(self, combo: QComboBox) -> str | None:
        text = combo.currentText()
        return None if text == AUTO else text

    def _save(self):
        new_mic = self._combo_value(self._mic_combo)
        new_loop = self._combo_value(self._loop_combo)
        self.devices_changed = (new_mic != self._settings.mic_device
                                or new_loop != self._settings.loop_device)
        self._settings.mic_device = new_mic
        self._settings.loop_device = new_loop
        self._settings.output_dir = self._dir_edit.text().strip()
        self._settings.artist = self._artist_edit.text().strip() or "Far"
        self._settings.normalize = self._normalize_check.isChecked()
        self._settings.save()
        self.accept()
