"""Genera icone PNG/ICO/ICNS dall'icona disegnata in csv_reader/desktop/icon.py.
Uso (dalla radice del progetto): python packaging/make_icons.py   (richiede Pillow)"""
import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from PySide6.QtGui import QGuiApplication  # noqa: E402
from PIL import Image  # noqa: E402
from csv_reader.desktop.icon import draw_icon  # noqa: E402

app = QGuiApplication([])
out = ROOT / "packaging" / "icons"
out.mkdir(parents=True, exist_ok=True)
draw_icon(1024).save(str(out / "csv_reader_1024.png"))
draw_icon(256).save(str(out / "csv_reader.png"))
big = Image.open(out / "csv_reader_1024.png").convert("RGBA")
big.save(out / "csv_reader.ico", sizes=[(s, s) for s in (16, 24, 32, 48, 64, 128, 256)])
big.save(out / "csv_reader.icns")
print("Icone create in", out)
