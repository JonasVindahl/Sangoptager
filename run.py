"""Startpunkt — bruges både til udvikling og af PyInstaller."""

import sys

from sangoptager.app import main

if __name__ == "__main__":
    sys.exit(main())
