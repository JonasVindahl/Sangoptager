"""Designsystem: mørkt tema, farvepalette, global stylesheet og app-ikon.

Alt styles centralt herfra, så dialoger og widgets ser ens ud uden lokale
stylesheets spredt i koden. Widgets med særlige roller markeres med
setObjectName ("primary", "danger", "ghost", "titleLabel", ...).
"""

from __future__ import annotations

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import (
    QColor,
    QIcon,
    QLinearGradient,
    QPainter,
    QPalette,
    QPixmap,
)
from PySide6.QtWidgets import QApplication

# ── Palette ──────────────────────────────────────────────────────────────────

BG        = "#121417"   # vinduesbaggrund
SURFACE   = "#1b1f24"   # kort/paneler
SURFACE_2 = "#22272e"   # hævede elementer (inputs, knapper)
BORDER    = "#2c333c"
TEXT      = "#e8eaed"
SUBTEXT   = "#8b95a1"
ACCENT    = "#4c8dff"   # primær handling (Gem)
RED       = "#e5484d"   # optagelse
AMBER     = "#f5a524"   # afventer handling
GREEN     = "#30c463"   # meter
YELLOW    = "#f1c40f"   # meter

METER_GRADIENT = ((0.0, GREEN), (0.62, GREEN), (0.78, YELLOW), (0.92, RED))


QSS = f"""
* {{
    color: {TEXT};
    font-family: "Segoe UI", "SF Pro Text", "Helvetica Neue", sans-serif;
    font-size: 13px;
}}
QMainWindow, QDialog {{
    background: {BG};
}}
QWidget#card {{
    background: {SURFACE};
    border: 1px solid {BORDER};
    border-radius: 12px;
}}
QLabel {{
    background: transparent;
}}
QLabel#titleLabel {{
    font-size: 16px;
    font-weight: 700;
    letter-spacing: 0.5px;
}}
QLabel#deviceLabel, QLabel#hintLabel {{
    color: {SUBTEXT};
    font-size: 11px;
}}
QLabel#versionLabel {{
    color: {SUBTEXT};
    font-family: "Consolas", "Menlo", "SF Mono", monospace;
    font-size: 11px;
    background: {SURFACE_2};
    border: 1px solid {BORDER};
    border-radius: 6px;
    padding: 2px 7px;
}}
QLabel#timerLabel {{
    font-family: "Consolas", "Menlo", "SF Mono", monospace;
    font-size: 40px;
    font-weight: 300;
    color: {TEXT};
}}
QLabel#meterName {{
    color: {SUBTEXT};
    font-size: 12px;
    font-weight: 600;
}}
QLabel#meterDb {{
    color: {SUBTEXT};
    font-family: "Consolas", "Menlo", "SF Mono", monospace;
    font-size: 11px;
}}
QLabel#balanceValue {{
    color: {SUBTEXT};
    font-family: "Consolas", "Menlo", "SF Mono", monospace;
    font-size: 11px;
}}

QPushButton {{
    background: {SURFACE_2};
    border: 1px solid {BORDER};
    border-radius: 8px;
    padding: 8px 18px;
    font-weight: 600;
}}
QPushButton:hover {{ background: #2a3038; }}
QPushButton:pressed {{ background: #1e232a; }}
QPushButton:disabled {{ color: {SUBTEXT}; background: {SURFACE}; }}

QPushButton#primary {{
    background: {ACCENT};
    border: none;
    color: white;
}}
QPushButton#primary:hover {{ background: #659cff; }}
QPushButton#primary:pressed {{ background: #3f7ae0; }}

QPushButton#danger {{
    background: transparent;
    border: 1px solid {BORDER};
    color: {RED};
}}
QPushButton#danger:hover {{ background: rgba(229, 72, 77, 0.12); border-color: {RED}; }}

QPushButton#gear {{
    background: transparent;
    border: none;
    border-radius: 8px;
    font-size: 16px;
    color: {SUBTEXT};
    padding: 4px;
}}
QPushButton#gear:hover {{ background: {SURFACE_2}; color: {TEXT}; }}

QLineEdit {{
    background: {SURFACE_2};
    border: 1px solid {BORDER};
    border-radius: 8px;
    padding: 8px 10px;
    selection-background-color: {ACCENT};
}}
QLineEdit:focus {{ border-color: {ACCENT}; }}

QComboBox {{
    background: {SURFACE_2};
    border: 1px solid {BORDER};
    border-radius: 8px;
    padding: 7px 10px;
}}
QComboBox:hover {{ border-color: {SUBTEXT}; }}
QComboBox::drop-down {{ border: none; width: 24px; }}
QComboBox::down-arrow {{
    image: none;
    border-left: 5px solid transparent;
    border-right: 5px solid transparent;
    border-top: 6px solid {SUBTEXT};
    margin-right: 8px;
}}
QComboBox QAbstractItemView {{
    background: {SURFACE_2};
    border: 1px solid {BORDER};
    selection-background-color: {ACCENT};
    outline: none;
}}

QSlider::groove:horizontal {{
    height: 6px;
    border-radius: 3px;
    background: {SURFACE_2};
    border: 1px solid {BORDER};
}}
QSlider::handle:horizontal {{
    width: 16px;
    height: 16px;
    margin: -6px 0;
    border-radius: 8px;
    background: {TEXT};
    border: 2px solid {BG};
}}
QSlider::handle:horizontal:hover {{ background: white; }}

QWidget#updateBanner {{
    background: rgba(76, 141, 255, 0.12);
    border: 1px solid {ACCENT};
    border-radius: 10px;
}}
QLabel#updateLabel {{
    color: {TEXT};
    font-weight: 600;
}}

QCheckBox {{ spacing: 8px; }}
QCheckBox::indicator {{
    width: 18px;
    height: 18px;
    border-radius: 5px;
    border: 1px solid {BORDER};
    background: {SURFACE_2};
}}
QCheckBox::indicator:hover {{ border-color: {SUBTEXT}; }}
QCheckBox::indicator:checked {{ background: {ACCENT}; border-color: {ACCENT}; }}

QStatusBar {{
    background: {SURFACE};
    border-top: 1px solid {BORDER};
    color: {SUBTEXT};
    font-size: 12px;
}}
QStatusBar::item {{ border: none; }}

QMessageBox, QInputDialog {{ background: {SURFACE}; }}
QToolTip {{
    background: {SURFACE_2};
    color: {TEXT};
    border: 1px solid {BORDER};
    padding: 4px 8px;
}}
"""


def apply_theme(app: QApplication) -> None:
    app.setStyle("Fusion")

    palette = QPalette()
    palette.setColor(QPalette.Window, QColor(BG))
    palette.setColor(QPalette.WindowText, QColor(TEXT))
    palette.setColor(QPalette.Base, QColor(SURFACE_2))
    palette.setColor(QPalette.AlternateBase, QColor(SURFACE))
    palette.setColor(QPalette.Text, QColor(TEXT))
    palette.setColor(QPalette.Button, QColor(SURFACE_2))
    palette.setColor(QPalette.ButtonText, QColor(TEXT))
    palette.setColor(QPalette.Highlight, QColor(ACCENT))
    palette.setColor(QPalette.HighlightedText, QColor("white"))
    palette.setColor(QPalette.PlaceholderText, QColor(SUBTEXT))
    palette.setColor(QPalette.ToolTipBase, QColor(SURFACE_2))
    palette.setColor(QPalette.ToolTipText, QColor(TEXT))
    app.setPalette(palette)
    app.setStyleSheet(QSS)
    app.setWindowIcon(make_app_icon())


def make_app_icon(size: int = 256) -> QIcon:
    """Tegner app-ikonet programmatisk: rød plade med hvid lydbølge."""
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.transparent)

    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing)

    grad = QLinearGradient(0, 0, 0, size)
    grad.setColorAt(0.0, QColor("#f0565b"))
    grad.setColorAt(1.0, QColor("#c23237"))
    painter.setBrush(grad)
    painter.setPen(Qt.NoPen)
    radius = size * 0.22
    painter.drawRoundedRect(QRectF(0, 0, size, size), radius, radius)

    # Lydbølge: fem afrundede søjler
    painter.setBrush(QColor("white"))
    heights = (0.28, 0.52, 0.72, 0.44, 0.30)
    bar_w = size * 0.075
    gap = size * 0.055
    total = len(heights) * bar_w + (len(heights) - 1) * gap
    x = (size - total) / 2
    for h in heights:
        bar_h = size * h
        y = (size - bar_h) / 2
        painter.drawRoundedRect(QRectF(x, y, bar_w, bar_h), bar_w / 2, bar_w / 2)
        x += bar_w + gap

    painter.end()
    return QIcon(pixmap)
