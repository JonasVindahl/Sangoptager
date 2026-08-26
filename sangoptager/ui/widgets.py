"""Custom widgets: pro-audio niveaumeter med peak-hold og klip-visning,
balance-slider og den store optage-knap med pulserende REC-indikator."""

from __future__ import annotations

import math

from PySide6.QtCore import QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QLinearGradient, QPainter, QPainterPath
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from . import theme

# ── Niveaumeter ──────────────────────────────────────────────────────────────

# Peak-stregen falder ca. 12 dB/sek. Ved 80 ms mellem opdateringer bliver det
# knap 1 dB pr. kald — langsomt nok til at nå at aflæse toppene.
_PEAK_FALL = 1.0 / -theme.METER_FLOOR_DB


class _MeterBar(QWidget):
    """Bjælken: RMS-fyld, hvid peak-hold-streg og rød klip-markering.

    Fyld og streg ligger på den samme dB-akse (theme.meter_position), så
    afstanden mellem dem er stemmens dynamik, og stregens plads i farverne
    viser, hvor tæt toppene er på loftet. Farverne siger altså noget om
    peak-stregen — fyldet er RMS og ligger naturligt et godt stykke lavere.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self._level = 0.0     # RMS som 0..1-position på bjælken
        self._peak = 0.0      # peak-hold som 0..1-position
        self._clipping = False
        self.setFixedHeight(16)

    def set_levels(self, rms: float, peak: float, clipping: bool):
        self._level = theme.meter_position(rms)
        self._peak = max(theme.meter_position(peak), self._peak - _PEAK_FALL)
        self._clipping = clipping
        self.update()

    def reset(self):
        if self._level == 0.0 and self._peak == 0.0 and not self._clipping:
            return
        self._level = 0.0
        self._peak = 0.0
        self._clipping = False
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)

        path = QPainterPath()
        path.addRoundedRect(rect, 5, 5)
        painter.setClipPath(path)

        painter.fillRect(rect, QColor(theme.SURFACE_2))

        # Skalamærker ved farveskiftene, så -12 og -6 dB kan aflæses, også
        # når bjælken står lavt og farverne endnu ikke er nået derop
        tick = QColor(theme.BORDER).lighter(150)
        for db in theme.METER_TICKS_DB:
            x = rect.left() + rect.width() * theme.meter_position_db(db)
            painter.fillRect(QRectF(x - 0.5, rect.top(), 1.0, rect.height()),
                             tick)

        if self._level > 0.0:
            # Gradienten spænder hele bjælken, ikke kun fyldet, så farverne
            # bliver ved med at betyde de samme dB uanset udslaget
            grad = QLinearGradient(rect.left(), 0, rect.right(), 0)
            for stop, color in theme.METER_GRADIENT:
                grad.setColorAt(stop, QColor(color))
            fill = QRectF(rect)
            fill.setWidth(rect.width() * self._level)
            painter.fillRect(fill, grad)

        if self._peak > 0.0:
            x = rect.left() + rect.width() * self._peak
            painter.fillRect(QRectF(x - 1.5, rect.top(), 2.5, rect.height()),
                             QColor(255, 255, 255, 190))

        if self._clipping:
            # Massiv blok yderst i skalaen — det er dén, og ikke et højt
            # udslag, der betyder at lyden faktisk bliver forvrænget
            width = max(6.0, rect.width() * 0.05)
            painter.fillRect(
                QRectF(rect.right() - width, rect.top(), width, rect.height()),
                QColor(theme.CLIP),
            )

        painter.setClipping(False)
        painter.setPen(QColor(theme.BORDER))
        painter.setBrush(Qt.NoBrush)
        painter.drawRoundedRect(rect, 5, 5)


class LevelMeter(QWidget):
    """Navn + bjælke + dB-udlæsning, f.eks.  Stemme ▓▓▓▓░░░░  -12 dB

    Udlæsningen er sporets højeste sampleværdi (peak) i dBFS — samme tal som
    den hvide streg på bjælken. Det er dét, der siger noget om afstanden til
    loftet; klipper signalet, står der KLIP i stedet.
    """

    def __init__(self, label: str, parent=None):
        super().__init__(parent)
        self._clipping = False
        self._db_font_px = 11
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)

        self._name = QLabel(label)
        self._name.setObjectName("meterName")
        self._name.setMinimumWidth(64)
        layout.addWidget(self._name)

        self._bar = _MeterBar()
        layout.addWidget(self._bar, stretch=1)

        self._db = QLabel("–∞ dB")
        self._db.setObjectName("meterDb")
        self._db.setMinimumWidth(52)
        self._db.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        layout.addWidget(self._db)

    def set_scale(self, s: float):
        """Skalér bjælkehøjde og typografi med vinduets skalafaktor."""
        self._bar.setFixedHeight(round(16 * s))
        self._name.setMinimumWidth(round(64 * s))
        self._name.setStyleSheet(f"font-size: {round(12 * s)}px;")
        self._db.setMinimumWidth(round(52 * s))
        self._db_font_px = round(11 * s)
        self._restyle_db()

    def _restyle_db(self):
        """Rød udlæsning mens signalet klipper. Kaldes kun ved skift — et nyt
        stylesheet koster en gennemregning af hele widgeten."""
        color = f" color: {theme.CLIP};" if self._clipping else ""
        self._db.setStyleSheet(f"font-size: {self._db_font_px}px;{color}")

    def set_levels(self, rms: float, peak: float, clipping: bool = False):
        self._bar.set_levels(rms, peak, clipping)
        if clipping != self._clipping:
            self._clipping = clipping
            self._restyle_db()
        if clipping:
            self._db.setText("KLIP")
        elif peak > 0.0:
            db = max(theme.METER_FLOOR_DB, 20 * math.log10(peak))
            self._db.setText(f"{db:.0f} dB")
        else:
            self._db.setText("–∞ dB")

    def reset(self):
        self._bar.reset()
        if self._clipping:
            self._clipping = False
            self._restyle_db()
        self._db.setText("–∞ dB")

    def set_enabled_look(self, enabled: bool):
        for widget in (self._name, self._bar, self._db):
            widget.setEnabled(enabled)
        if not enabled:
            self._db.setText("—")


# ── Balance ──────────────────────────────────────────────────────────────────


class BalanceSlider(QWidget):
    """'Melodi ── ● ── Stemme' med procent-udlæsning. Værdi 0..1 (1 = stemme)."""

    def __init__(self, value: float = 0.5, parent=None):
        super().__init__(parent)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(4)

        header = QHBoxLayout()
        title = QLabel("Lydbalance")
        title.setObjectName("meterName")
        header.addWidget(title)
        header.addStretch(1)
        self._value_label = QLabel("")
        self._value_label.setObjectName("balanceValue")
        header.addWidget(self._value_label)
        outer.addLayout(header)

        row = QHBoxLayout()
        row.setSpacing(10)
        self._left = QLabel("Melodi")
        self._left.setObjectName("hintLabel")
        row.addWidget(self._left)

        self._slider = QSlider(Qt.Horizontal)
        self._slider.setRange(0, 100)
        self._slider.setValue(int(value * 100))
        self._slider.valueChanged.connect(self._update_label)
        row.addWidget(self._slider, stretch=1)

        self._right = QLabel("Stemme")
        self._right.setObjectName("hintLabel")
        row.addWidget(self._right)
        outer.addLayout(row)

        self._title = title
        self._update_label()

    def set_scale(self, s: float):
        self._title.setStyleSheet(f"font-size: {round(12 * s)}px;")
        self._value_label.setStyleSheet(f"font-size: {round(11 * s)}px;")
        for side in (self._left, self._right):
            side.setStyleSheet(f"font-size: {round(11 * s)}px;")

    def _update_label(self):
        voice = self._slider.value()
        if voice == 50:
            self._value_label.setText("neutral")
        else:
            self._value_label.setText(f"{100 - voice} · {voice}")

    @property
    def value(self) -> float:
        return self._slider.value() / 100.0

    def set_value(self, value: float):
        self._slider.setValue(int(min(1.0, max(0.0, value)) * 100))

    @property
    def valueChanged(self):
        return self._slider.valueChanged


# ── Optage-knap ──────────────────────────────────────────────────────────────


class RecordButton(QPushButton):
    """Stor tilstandsknap: idle → recording (pulserende dot) → resolve/busy."""

    _STATES = {
        "idle":      dict(bg="#2a3038", bg_hover="#333a44", text_color=theme.TEXT,
                          label="Optag", dot=theme.RED),
        "recording": dict(bg=theme.RED, bg_hover="#ef5a5f", text_color="white",
                          label="Stop", dot="white"),
        "resolve":   dict(bg=theme.AMBER, bg_hover="#ffb84d", text_color="#1b1f24",
                          label="Gem eller slet optagelsen", dot="#1b1f24"),
        "busy":      dict(bg=theme.SURFACE, bg_hover=theme.SURFACE,
                          text_color=theme.SUBTEXT, label="Gemmer…", dot=None),
    }

    def __init__(self, parent=None):
        super().__init__(parent)
        self.state = "idle"
        self._phase = 0.0
        self.setMinimumHeight(72)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setCursor(Qt.PointingHandCursor)
        self.setFocusPolicy(Qt.NoFocus)

        self._pulse = QTimer(self)
        self._pulse.setInterval(40)
        self._pulse.timeout.connect(self._tick)

    def set_state(self, state: str):
        self.state = state
        if state == "recording":
            self._pulse.start()
        else:
            self._pulse.stop()
        self.setEnabled(state != "busy")
        self.update()

    def _tick(self):
        self._phase += 0.18
        self.update()

    def paintEvent(self, event):
        conf = self._STATES[self.state]
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)

        bg = QColor(conf["bg_hover"] if (self.underMouse() and self.isEnabled())
                    else conf["bg"])
        painter.setPen(Qt.NoPen)
        painter.setBrush(bg)
        radius = min(14.0, rect.height() * 0.18)
        painter.drawRoundedRect(rect, radius, radius)

        # Typografi og dot skalerer med knappens højde, så knappen kan vokse
        # med vinduet uden at indholdet ser fortabt ud
        height = rect.height()
        font = painter.font()
        font.setPointSizeF(min(24.0, max(15.0, height * 0.20)))
        font.setBold(True)
        painter.setFont(font)

        label = conf["label"]
        metrics = painter.fontMetrics()
        text_w = metrics.horizontalAdvance(label)

        dot_r = min(11.0, max(7.0, height * 0.10))
        gap = dot_r + 5.0
        has_dot = conf["dot"] is not None
        group_w = text_w + (dot_r * 2 + gap if has_dot else 0)
        x = rect.center().x() - group_w / 2

        if has_dot:
            dot_color = QColor(conf["dot"])
            if self.state == "recording":
                alpha = 0.45 + 0.55 * (0.5 + 0.5 * math.sin(self._phase))
                dot_color.setAlphaF(alpha)
            painter.setBrush(dot_color)
            cy = rect.center().y()
            if self.state == "recording":
                painter.drawRoundedRect(
                    QRectF(x, cy - dot_r, dot_r * 2, dot_r * 2),
                    dot_r * 0.4, dot_r * 0.4,
                )
            else:
                painter.drawEllipse(QRectF(x, cy - dot_r, dot_r * 2, dot_r * 2))
            x += dot_r * 2 + gap

        painter.setPen(QColor(conf["text_color"]))
        painter.drawText(QRectF(x, rect.top(), text_w + 4, rect.height()),
                         Qt.AlignVCenter | Qt.AlignLeft, label)
