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

from ..settings import Settings

AUTO = "Automatisk (systemets standard)"


class SettingsDialog(QDialog):
    """Sat .devices_changed hvis mikrofon/melodikilde blev ændret."""

    def __init__(self, settings: Settings, mics: list[str], loopbacks: list[str],
                 parent=None):
        super().__init__(parent)
        self.setWindowTitle("Indstillinger")
        self.setMinimumWidth(460)
        self._settings = settings
        self.devices_changed = False

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

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText("Gem")
        buttons.button(QDialogButtonBox.Ok).setObjectName("primary")
        buttons.button(QDialogButtonBox.Cancel).setText("Annullér")
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)

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
