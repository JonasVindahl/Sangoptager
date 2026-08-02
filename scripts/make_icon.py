"""Genererer resources/app.ico fra det programmatiske app-ikon.

Køres i CI før PyInstaller, så selve Sangoptager.exe får ikonet i
Stifinder/proceslinjen: python scripts/make_icon.py [udsti]
"""

import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtCore import QSize  # noqa: E402
from PySide6.QtGui import QGuiApplication, QImage, QPainter  # noqa: E402

from sangoptager.ui.theme import make_app_icon  # noqa: E402


def main() -> int:
    out_path = sys.argv[1] if len(sys.argv) > 1 else os.path.join(
        "resources", "app.ico")
    app = QGuiApplication(sys.argv)  # noqa: F841 — kræves af QPixmap

    icon = make_app_icon(256)
    # Qt's ICO-writer pakker alle billeder i én .ico når man skriver flere
    # størrelser via QImage — men simplest er at skrive 256px; Windows
    # nedskalerer selv pænt til 16/32/48.
    image = icon.pixmap(QSize(256, 256)).toImage().convertToFormat(
        QImage.Format_ARGB32)
    if not image.save(out_path, "ICO"):
        print(f"FEJL: kunne ikke skrive {out_path}")
        return 1
    print(f"Skrev {out_path} ({os.path.getsize(out_path)} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
