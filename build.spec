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
    # numpy bruges kun af testene; Qt-moduler appen ikke rører fylder blot
    # download ved hver selv-opdatering
    excludes=[
        "tkinter", "numpy", "pytest",
        "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets",
        "PySide6.Qt3DCore", "PySide6.QtCharts", "PySide6.QtDataVisualization",
        "PySide6.QtQuick", "PySide6.QtQml", "PySide6.QtDesigner",
    ],
)
pyz = PYZ(a.pure)

icon_path = os.path.join("resources", "app.ico")

exe = EXE(
    pyz,
    a.scripts,
    exclude_binaries=True,
    name="Sangoptager",
    console=False,   # ingen sort konsolvindue
    icon=icon_path if os.path.isfile(icon_path) else None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    name="Sangoptager",
)
