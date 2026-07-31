"""Dialogen efter Stop: navngiv sangen og Gem — eller Slet optagelsen."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
)

from ..library import sanitize_title
from .widgets import BalanceSlider


class SaveDialog(QDialog):
    """Returnerer via .result_action: ('save', titel, balance) eller ('delete',)."""

    def __init__(self, balance: float, duration: float, has_loopback: bool, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Gem optagelse")
        self.setWindowFlags(self.windowFlags() | Qt.WindowStaysOnTopHint)
        self.setMinimumWidth(380)
        self.result_action: tuple | None = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 18, 18, 16)
        layout.setSpacing(12)

        mins, secs = divmod(int(duration), 60)
        info = QLabel(f"Optagelse på {mins:02d}:{secs:02d} — hvad hedder sangen?")
        layout.addWidget(info)

        self._title_edit = QLineEdit()
        self._title_edit.setPlaceholderText("Sangens navn…")
        font = self._title_edit.font()
        font.setPointSize(font.pointSize() + 4)
        self._title_edit.setFont(font)
        layout.addWidget(self._title_edit)

        self._balance = BalanceSlider(balance)
        if has_loopback:
            layout.addWidget(self._balance)

        buttons = QHBoxLayout()
        delete_btn = QPushButton("Slet optagelse")
        delete_btn.setObjectName("danger")
        delete_btn.clicked.connect(self._on_delete)
        buttons.addWidget(delete_btn)
        buttons.addStretch(1)

        save_btn = QPushButton("Gem")
        save_btn.setObjectName("primary")
        save_btn.setDefault(True)
        save_btn.setMinimumWidth(110)
        save_btn.clicked.connect(self._on_save)
        buttons.addWidget(save_btn)
        layout.addLayout(buttons)

        self._title_edit.returnPressed.connect(self._on_save)
        self._title_edit.setFocus()

    def _on_save(self):
        title = sanitize_title(self._title_edit.text())
        if not title:
            QMessageBox.warning(self, "Mangler navn", "Skriv sangens navn først.")
            self._title_edit.setFocus()
            return
        self.result_action = ("save", title, self._balance.value)
        self.accept()

    def _on_delete(self):
        answer = QMessageBox.question(
            self,
            "Slet optagelse",
            "Er du sikker på, at optagelsen skal slettes?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if answer == QMessageBox.Yes:
            self.result_action = ("delete",)
            self.accept()

    def closeEvent(self, event):
        # Luk med X = behold intet valg; hovedvinduet spørger igen næste gang
        if self.result_action is None:
            self.result_action = ("keep",)
        event.accept()
