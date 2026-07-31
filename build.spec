# PyInstaller-spec — kør på WINDOWS:  pyinstaller build.spec
# Kræver at resources\ffmpeg.exe findes (hentes fra https://www.gyan.dev/ffmpeg/builds/)

import os

datas = []
if os.path.isfile(os.path.join("resources", "ffmpeg.exe")):
    datas.append((os.path.join("resources", "ffmpeg.exe"), "."))
else:
    raise SystemExit("FEJL: resources\\ffmpeg.exe mangler — hent den før bygning.")

a = Analysis(
    ["run.py"],
    pathex=[],
    binaries=[],
    datas=datas,
    hiddenimports=["pyaudiowpatch"],
    excludes=["tkinter"],
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    exclude_binaries=True,
    name="Sangoptager",
    console=False,   # ingen sort konsolvindue
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    name="Sangoptager",
)
