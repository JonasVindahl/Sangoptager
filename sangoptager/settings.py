"""Indstillinger gemt som JSON i brugerens app-data-mappe.

Windows: %APPDATA%\\Sangoptager\\config.json
macOS/Linux (udvikling): ~/.config/sangoptager/config.json
"""

from __future__ import annotations

import json
import os
import sys
from dataclasses import asdict, dataclass, field


def _config_dir() -> str:
    if sys.platform == "win32":
        base = os.environ.get("APPDATA", os.path.expanduser("~"))
        return os.path.join(base, "Sangoptager")
    return os.path.join(os.path.expanduser("~"), ".config", "sangoptager")


def _default_output_dir() -> str:
    if sys.platform == "win32":
        return os.path.join(os.path.expanduser("~"), "Nextcloud", "Sange")
    return os.path.join(os.path.expanduser("~"), "Sange")


@dataclass
class Settings:
    output_dir: str = field(default_factory=_default_output_dir)
    artist: str = "Far"
    balance: float = 0.5  # 0 = kun melodi, 1 = kun mikrofon
    mic_device: str | None = None   # None = systemets standard
    loop_device: str | None = None  # None = standard-højttaler
    window_geometry: str = ""       # QMainWindow.saveGeometry() som hex

    @property
    def path(self) -> str:
        return os.path.join(_config_dir(), "config.json")

    @classmethod
    def load(cls) -> "Settings":
        settings = cls()
        try:
            with open(settings.path, encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, ValueError):
            return settings
        for key in ("output_dir", "artist", "balance", "mic_device",
                    "loop_device", "window_geometry"):
            if key in data:
                setattr(settings, key, data[key])
        settings.balance = min(1.0, max(0.0, float(settings.balance)))
        return settings

    def save(self) -> None:
        os.makedirs(_config_dir(), exist_ok=True)
        with open(self.path, "w", encoding="utf-8") as fh:
            json.dump(asdict(self), fh, indent=2, ensure_ascii=False)


def temp_recording_dir() -> str:
    """Mappe til de midlertidige WAV-spor under/efter optagelse."""
    return os.path.join(_config_dir(), "seneste_optagelse")
