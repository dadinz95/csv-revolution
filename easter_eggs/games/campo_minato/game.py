"""Campo Minato in stile arcade retrò (PySide6). Indipendente da CSV Revolution.

* ``MinesGame``: le regole (mine, numeri, scopertura a catena, bandierine, vittoria/sconfitta), senza interfaccia.
* ``MinesWindow``: finestra a sé con disegno, timer del tempo e mouse.

Comandi: clic sinistro scopre, clic destro (o Maiusc+clic) mette/toglie la bandierina, clic su un numero già
scoperto con le bandierine giuste scopre i vicini; R/Invio ricomincia, Esc esce. Il primo clic non è mai una mina."""
from __future__ import annotations

import random

from PySide6.QtCore import QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QFontDatabase, QKeyEvent, QMouseEvent, QPainter, QPen
from PySide6.QtWidgets import QHBoxLayout, QPushButton, QVBoxLayout, QWidget


class MinesGame:
    """Regole del gioco. Stati: "ready" (nessuna cella scoperta), "playing", "won", "lost"."""

    def __init__(self, cols: int = 16, rows: int = 12, mines: int = 30, rng: random.Random | None = None):
        if not 0 < mines < cols * rows:
            raise ValueError("Numero di mine non valido.")
        self.cols, self.rows, self.mines_total = cols, rows, mines
        self.rng = rng or random.Random()
        self.reset()

    def reset(self) -> None:
        self.mines: set[tuple[int, int]] = set()
        self.revealed: set[tuple[int, int]] = set()
        self.flags: set[tuple[int, int]] = set()
        self.state = "ready"
        self.exploded: tuple[int, int] | None = None

    @property
    def over(self) -> bool:
        return self.state in ("won", "lost")

    @property
    def flags_left(self) -> int:
        return self.mines_total - len(self.flags)

    def neighbors(self, x: int, y: int):
        for dy in (-1, 0, 1):
            for dx in (-1, 0, 1):
                nx, ny = x + dx, y + dy
                if (dx or dy) and 0 <= nx < self.cols and 0 <= ny < self.rows:
                    yield nx, ny

    def count(self, x: int, y: int) -> int:
        """Mine nelle celle vicine."""
        return sum((n in self.mines) for n in self.neighbors(x, y))

    def _place_mines(self, x: int, y: int) -> None:
        """Mine sparse a caso: la prima cella (e, se c'è posto, i suoi vicini) ne è libera."""
        safe = {(x, y), *self.neighbors(x, y)}
        free = [(cx, cy) for cy in range(self.rows) for cx in range(self.cols) if (cx, cy) not in safe]
        if len(free) < self.mines_total:
            free = [(cx, cy) for cy in range(self.rows) for cx in range(self.cols) if (cx, cy) != (x, y)]
        self.mines = set(self.rng.sample(free, self.mines_total))

    def reveal(self, x: int, y: int) -> list[tuple[int, int]]:
        """Scopre la cella (a catena se non ha mine vicine). Ritorna le celle scoperte; le bandierine proteggono."""
        if self.over or (x, y) in self.flags or (x, y) in self.revealed or not (0 <= x < self.cols and 0 <= y < self.rows):
            return []
        if self.state == "ready":
            self._place_mines(x, y)
            self.state = "playing"
        if (x, y) in self.mines:
            self.state = "lost"
            self.exploded = (x, y)
            return []
        opened, stack = [], [(x, y)]
        while stack:
            cell = stack.pop()
            if cell in self.revealed or cell in self.flags:
                continue
            self.revealed.add(cell)
            opened.append(cell)
            if self.count(*cell) == 0:
                stack.extend(n for n in self.neighbors(*cell) if n not in self.revealed)
        if len(self.revealed) == self.cols * self.rows - self.mines_total:
            self.state = "won"
            self.flags = set(self.mines)
        return opened

    def toggle_flag(self, x: int, y: int) -> bool:
        """Mette o toglie la bandierina su una cella coperta. Ritorna se è cambiato qualcosa."""
        cell = (x, y)
        if self.over or self.state == "ready" or cell in self.revealed:
            return False
        if cell in self.flags:
            self.flags.discard(cell)
            return True
        if len(self.flags) >= self.mines_total:
            return False
        self.flags.add(cell)
        return True

    def chord(self, x: int, y: int) -> list[tuple[int, int]]:
        """Su un numero scoperto con altrettante bandierine vicine: scopre gli altri vicini."""
        if self.over or (x, y) not in self.revealed:
            return []
        around = list(self.neighbors(x, y))
        n = self.count(x, y)
        if n == 0 or sum(c in self.flags for c in around) != n:
            return []
        opened: list[tuple[int, int]] = []
        for c in around:
            if c not in self.flags:
                opened += self.reveal(*c)
        return opened


# --------------------------------------------------------------------------- finestra

PHOSPHOR = QColor("#39ff14")
PHOSPHOR_DIM = QColor("#1c7a0b")
BACKGROUND = QColor("#050805")
COVER = QColor("#0d240a")
RED, AMBER = QColor("#ff3b30"), QColor("#ffd60a")
NUMBER_COLORS = [None, QColor("#39ff14"), QColor("#64d2ff"), QColor("#ffd60a"), QColor("#ff9f0a"), QColor("#ff453a"),
                 QColor("#ff6ad5"), QColor("#bf5af2"), QColor("#ffffff")]


class MinesWindow(QWidget):
    """Finestra autonoma del gioco."""

    CELL = 34
    HUD = 46
    MARGIN = 16

    def __init__(self, cols: int = 16, rows: int = 12, mines: int = 30, rng: random.Random | None = None):
        super().__init__(None)
        self.setWindowTitle("CAMPO MINATO")
        self.setAttribute(Qt.WA_DeleteOnClose)
        self.setFocusPolicy(Qt.StrongFocus)
        self.game = MinesGame(cols, rows, mines, rng)
        self.seconds = 0
        self.best: int | None = None
        self.setFixedSize(cols * self.CELL + 2 * self.MARGIN, rows * self.CELL + self.HUD + 2 * self.MARGIN)
        self.timer = QTimer(self)
        self.timer.setInterval(1000)
        self.timer.timeout.connect(self._second)
        mono = QFont(QFontDatabase.systemFont(QFontDatabase.FixedFont))
        mono.setBold(True)
        self._font = mono
        self.setStyleSheet("MinesWindow { background: #050805; }")
        self.restart_button = self._button("Ricomincia", self.restart)
        self.quit_button = self._button("Chiudi", self.close)
        row = QHBoxLayout()
        row.addStretch(1)
        row.addWidget(self.restart_button)
        row.addWidget(self.quit_button)
        row.addStretch(1)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addStretch(1)
        outer.addLayout(row)
        outer.addSpacing(self.height() // 5)
        for b in (self.restart_button, self.quit_button):
            b.hide()

    def _button(self, text: str, slot) -> QPushButton:
        b = QPushButton(text)
        b.setFocusPolicy(Qt.NoFocus)
        b.setCursor(Qt.PointingHandCursor)
        b.setStyleSheet("QPushButton { background: #050805; color: #39ff14; border: 2px solid #39ff14; padding: 6px 16px;"
                        " font-weight: bold; } QPushButton:hover { background: #39ff14; color: #050805; }")
        b.clicked.connect(slot)
        return b

    # ---- ciclo di gioco
    def restart(self) -> None:
        self.timer.stop()
        self.game.reset()
        self.seconds = 0
        for b in (self.restart_button, self.quit_button):
            b.hide()
        self.setFocus()
        self.update()

    def _second(self) -> None:
        self.seconds = min(999, self.seconds + 1)
        self.update()

    def _after_move(self) -> None:
        g = self.game
        if g.state == "playing" and not self.timer.isActive():
            self.timer.start()
        if g.over:
            self.timer.stop()
            if g.state == "won":
                self.best = self.seconds if self.best is None else min(self.best, self.seconds)
            for b in (self.restart_button, self.quit_button):
                b.show()
        self.update()

    # ---- mouse e tastiera
    def _cell_at(self, pos) -> tuple[int, int] | None:
        x = int((pos.x() - self.MARGIN) // self.CELL)
        y = int((pos.y() - self.HUD - self.MARGIN // 2) // self.CELL)
        return (x, y) if 0 <= x < self.game.cols and 0 <= y < self.game.rows and pos.x() >= self.MARGIN else None

    def mousePressEvent(self, event: QMouseEvent) -> None:
        cell = self._cell_at(event.position())
        if cell is None or self.game.over:
            return
        flag_click = event.button() == Qt.RightButton or (event.button() == Qt.LeftButton and event.modifiers() & Qt.ShiftModifier)
        if flag_click:
            self.game.toggle_flag(*cell)
        elif event.button() == Qt.LeftButton:
            if cell in self.game.revealed:
                self.game.chord(*cell)
            else:
                self.game.reveal(*cell)
        self._after_move()

    def keyPressEvent(self, event: QKeyEvent) -> None:
        key = event.key()
        if key == Qt.Key_Escape:
            self.close()
        elif key in (Qt.Key_R, Qt.Key_Return, Qt.Key_Enter) and (self.game.over or key == Qt.Key_R):
            self.restart()
        else:
            super().keyPressEvent(event)

    def closeEvent(self, event) -> None:
        self.timer.stop()
        super().closeEvent(event)

    # ---- disegno
    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, False)
        p.fillRect(self.rect(), BACKGROUND)
        g, c = self.game, self.CELL
        hud = QFont(self._font)
        hud.setPointSize(14)
        p.setFont(hud)
        p.setPen(PHOSPHOR)
        width = g.cols * c
        p.drawText(QRectF(self.MARGIN, 6, width / 3, 30), Qt.AlignLeft | Qt.AlignVCenter, f"MINE {g.flags_left:03d}")
        best = "---" if self.best is None else f"{self.best:03d}"
        p.drawText(QRectF(self.MARGIN + width / 3, 6, width / 3, 30), Qt.AlignCenter, f"REC {best}")
        p.drawText(QRectF(self.MARGIN + 2 * width / 3, 6, width / 3, 30), Qt.AlignRight | Qt.AlignVCenter, f"TEMPO {self.seconds:03d}")
        top = self.HUD + self.MARGIN // 2
        p.setPen(QPen(PHOSPHOR, 3))
        p.drawRect(QRectF(self.MARGIN - 2, top - 2, width + 4, g.rows * c + 4))
        cell_font = QFont(self._font)
        cell_font.setPointSize(15)
        for y in range(g.rows):
            for x in range(g.cols):
                rect = QRectF(self.MARGIN + x * c, top + y * c, c, c)
                inner = rect.adjusted(1, 1, -1, -1)
                if (x, y) in g.revealed:
                    p.fillRect(inner, BACKGROUND)
                    n = g.count(x, y)
                    if n:
                        p.setFont(cell_font)
                        p.setPen(NUMBER_COLORS[n])
                        p.drawText(rect, Qt.AlignCenter, str(n))
                    continue
                shown_mine = g.state == "lost" and (x, y) in g.mines and (x, y) not in g.flags
                p.fillRect(inner, RED if (x, y) == g.exploded else COVER)
                p.setPen(QPen(PHOSPHOR_DIM, 1))
                p.drawRect(inner)
                p.setFont(cell_font)
                if (x, y) in g.flags:
                    if g.state == "lost" and (x, y) not in g.mines:
                        p.setPen(RED)
                        p.drawText(rect, Qt.AlignCenter, "X")
                    else:
                        p.setPen(AMBER)
                        p.drawText(rect, Qt.AlignCenter, "P")
                elif shown_mine:
                    p.setPen(RED if (x, y) != g.exploded else BACKGROUND)
                    p.drawText(rect, Qt.AlignCenter, "*")
        if g.state == "ready":
            self._overlay(p, "CAMPO MINATO", "CLIC SX SCOPRE · CLIC DX BANDIERA", top, g.rows * c, dim=False)
        elif g.over:
            self._overlay(p, "VITTORIA!" if g.state == "won" else "GAME OVER",
                          f"TEMPO {self.seconds}s" if g.state == "won" else "HAI CALPESTATO UNA MINA", top, g.rows * c)
        p.setPen(QPen(QColor(0, 0, 0, 60), 1))
        for y in range(0, self.height(), 3):
            p.drawLine(0, y, self.width(), y)
        p.end()

    def _overlay(self, p: QPainter, title: str, subtitle: str, top: int, height: int, dim: bool = True) -> None:
        area = QRectF(self.MARGIN, top, self.game.cols * self.CELL, height)
        mid = area.center().y() - (30 if dim else 0)
        if dim:
            p.fillRect(QRectF(area.left(), mid - 50, area.width(), 100), QColor(5, 8, 5, 215))
        else:
            p.fillRect(QRectF(area.left(), mid - 50, area.width(), 100), QColor(5, 8, 5, 230))
        big = QFont(self._font)
        big.setPointSize(26)
        p.setFont(big)
        p.setPen(PHOSPHOR)
        p.drawText(QRectF(area.left(), mid - 40, area.width(), 50), Qt.AlignCenter, title)
        small = QFont(self._font)
        small.setPointSize(12)
        p.setFont(small)
        p.drawText(QRectF(area.left(), mid + 10, area.width(), 30), Qt.AlignCenter, subtitle)


def launch() -> MinesWindow:
    """Crea e mostra la finestra del gioco in primo piano."""
    window = MinesWindow()
    window.show()
    window.raise_()
    window.activateWindow()
    window.setFocus()
    return window
