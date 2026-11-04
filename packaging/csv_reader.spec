# -*- mode: python ; coding: utf-8 -*-
# Compilazione con PyInstaller:  pyinstaller packaging/csv_reader.spec --noconfirm
# Funziona su Windows, macOS e Linux (va lanciato sul sistema di destinazione).
import sys
from pathlib import Path

ROOT = Path(SPECPATH).parent
ICONS = ROOT / "packaging" / "icons"

# Traduzione italiana dei pulsanti standard di Qt (Sì/No/Annulla…): inclusa esplicitamente.
QM_DATAS = []
try:
    from PySide6.QtCore import QLibraryInfo
    _qm = Path(QLibraryInfo.path(QLibraryInfo.LibraryPath.TranslationsPath)) / "qtbase_it.qm"
    if _qm.exists():
        QM_DATAS = [(str(_qm), "PySide6/Qt/translations")]
except Exception:
    pass

# Easter egg: i giochi sono trovati a runtime; PyInstaller non può vederli, quindi si elencano tutti i moduli di easter_eggs/
EGG_MODULES = sorted(
    ".".join(path.relative_to(ROOT).with_suffix("").parts).removesuffix(".__init__")
    for path in (ROOT / "easter_eggs").rglob("*.py"))

a = Analysis(
    [str(ROOT / "run_csv_reader.py")],
    pathex=[str(ROOT)],
    binaries=[],
    datas=QM_DATAS,
    hiddenimports=["duckdb", "openpyxl", "xlrd", *EGG_MODULES],
    excludes=["tkinter", "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets", "PySide6.Qt3DCore",
              "PySide6.QtQuick", "PySide6.QtQml", "PySide6.QtMultimedia", "PySide6.QtCharts"],
    noarchive=False,
)
pyz = PYZ(a.pure)

if sys.platform == "darwin":
    icon = str(ICONS / "csv_reader.icns")
elif sys.platform == "win32":
    icon = str(ICONS / "csv_reader.ico")
else:
    icon = None

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="csv-revolution" if sys.platform.startswith("linux") else "CSV Revolution",
    console=False,          # applicazione grafica, nessuna finestra del terminale
    icon=icon,
    argv_emulation=False,   # su macOS i file arrivano come evento FileOpen (gestito dall'app)
)
coll = COLLECT(exe, a.binaries, a.datas, name="CSV Revolution")

if sys.platform == "darwin":
    app = BUNDLE(
        coll,
        name="CSV Revolution.app",
        icon=icon,
        bundle_identifier="it.csvreader.app",
        info_plist={
            "CFBundleName": "CSV Revolution",
            "CFBundleDisplayName": "CSV Revolution",
            "CFBundleShortVersionString": "0.20.0",
            "NSHighResolutionCapable": True,
            # Associazione ai file CSV: compare in «Apri con» del Finder.
            "CFBundleDocumentTypes": [
                {
                    "CFBundleTypeName": "File CSV",
                    "CFBundleTypeRole": "Editor",          # oppure "Viewer"
                    "LSHandlerRank": "Alternate",          # non ruba l'app predefinita
                    "CFBundleTypeExtensions": ["csv"],
                    "CFBundleTypeMIMETypes": ["text/csv"],
                    "LSItemContentTypes": ["public.comma-separated-values-text"],
                }
            ],
        },
    )
