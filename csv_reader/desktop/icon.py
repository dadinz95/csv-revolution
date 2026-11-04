"""Icona dell'applicazione, disegnata a codice (nessun file esterno)."""
from __future__ import annotations

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPen, QPixmap


def draw_icon(size: int) -> QPixmap:
    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    u = size / 64.0
    p.setPen(Qt.NoPen)
    p.setBrush(QColor("#2447D6"))
    p.drawRoundedRect(QRectF(2 * u, 2 * u, 60 * u, 60 * u), 14 * u, 14 * u)
    p.setBrush(QColor("#FFFFFF"))
    p.drawRoundedRect(QRectF(13 * u, 15 * u, 38 * u, 34 * u), 4 * u, 4 * u)
    p.setBrush(QColor("#DCE4FB"))
    p.drawRect(QRectF(13 * u, 15 * u, 38 * u, 9 * u))
    pen = QPen(QColor("#2447D6"))
    pen.setWidthF(2 * u)
    p.setPen(pen)
    for x in (25.5, 38.5):
        p.drawLine(int(x * u), int(15 * u), int(x * u), int(49 * u))
    for y in (24, 32.5, 41):
        p.drawLine(int(13 * u), int(y * u), int(51 * u), int(y * u))
    p.end()
    return pm


def app_icon() -> QIcon:
    icon = QIcon()
    for size in (16, 24, 32, 48, 64, 128, 256):
        icon.addPixmap(draw_icon(size))
    return icon
