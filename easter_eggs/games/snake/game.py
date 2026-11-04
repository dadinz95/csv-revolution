"""Snake in stile arcade retrò (PySide6). Indipendente da CSV Revolution: nessuna importazione dall'applicazione.

* ``SnakeGame``: le regole (griglia, direzione, cibo, crescita, collisioni, punteggio), senza interfaccia: si prova con i test.
* ``SnakeWindow``: finestra a sé con disegno, timer, tastiera e schermata di Game Over.

Comandi: frecce (anche WASD) per muoversi, Spazio/P pausa, Invio/R ricomincia dopo il Game Over, Esc esce."""
from __future__ import annotations

import random
from collections import deque

from PySide6.QtCore import QEvent, QPointF, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QFontDatabase, QKeyEvent, QPainter, QPen
from PySide6.QtWidgets import QHBoxLayout, QPushButton, QVBoxLayout, QWidget

UP, DOWN, LEFT, RIGHT = (0, -1), (0, 1), (-1, 0), (1, 0)
FOOD_POINTS = 10


class SnakeGame:
    """Regole del gioco. Il serpente è una lista di celle (x, y) con la testa per prima."""

    def __init__(self, cols: int = 28, rows: int = 20, rng: random.Random | None = None):
        self.cols, self.rows = cols, rows
        self.rng = rng or random.Random()
        self.reset()

    def reset(self) -> None:
        cx, cy = self.cols // 2, self.rows // 2
        self.snake: list[tuple[int, int]] = [(cx, cy), (cx - 1, cy), (cx - 2, cy)]
        self.direction = RIGHT
        self.pending: deque[tuple[int, int]] = deque()      # svolte richieste, applicate una per passo
        self.score = 0
        self.over = False
        self.won = False
        self.food = self._spawn_food()

    # ---- comandi
    def turn(self, direction: tuple[int, int]) -> bool:
        """Mette in coda una svolta. Sono ignorate: l'inversione a 180° (vale anche rispetto all'ultima svolta in coda,
        così due tasti rapidi non fanno scontrare il serpente con sé stesso) e la stessa direzione. Ritorna se accettata."""
        last = self.pending[-1] if self.pending else self.direction
        if direction == last or direction == (-last[0], -last[1]) or len(self.pending) >= 2:
            return False
        self.pending.append(direction)
        return True

    # ---- avanzamento
    def step(self) -> str:
        """Un passo: ritorna "move", "eat" o "dead" (urto contro il bordo o contro il corpo)."""
        if self.over:
            return "dead"
        if self.pending:
            self.direction = self.pending.popleft()
        hx, hy = self.snake[0]
        head = (hx + self.direction[0], hy + self.direction[1])
        eating = head == self.food
        if not (0 <= head[0] < self.cols and 0 <= head[1] < self.rows):
            self.over = True
            return "dead"
        body = self.snake if eating else self.snake[:-1]     # la coda si sposta: la sua cella è libera se non si mangia
        if head in body:
            self.over = True
            return "dead"
        self.snake.insert(0, head)
        if eating:
            self.score += FOOD_POINTS
            self.food = self._spawn_food()
            if self.food is None:                            # nessuna cella libera: vittoria
                self.over = self.won = True
            return "eat"
        self.snake.pop()
        return "move"

    def _spawn_food(self) -> tuple[int, int] | None:
        taken = set(self.snake)
        free = [(x, y) for y in range(self.rows) for x in range(self.cols) if (x, y) not in taken]
        return self.rng.choice(free) if free else None

    def interval_ms(self) -> int:
        """Millisecondi tra due passi: più veloce a ogni cibo mangiato, con un minimo."""
        return max(55, 140 - 4 * (self.score // FOOD_POINTS))


# --------------------------------------------------------------------------- finestra

PHOSPHOR = QColor("#39ff14")
PHOSPHOR_DIM = QColor("#1c7a0b")
FOOD_A, FOOD_B = QColor("#ff3b30"), QColor("#ffd60a")
BACKGROUND = QColor("#050805")
KEYS = {Qt.Key_Up: UP, Qt.Key_Down: DOWN, Qt.Key_Left: LEFT, Qt.Key_Right: RIGHT,
        Qt.Key_W: UP, Qt.Key_S: DOWN, Qt.Key_A: LEFT, Qt.Key_D: RIGHT}


class SnakeWindow(QWidget):
    """Finestra autonoma del gioco. Stati: "ready" (attende il primo tasto), "running", "paused", "over"."""

    CELL = 22
    HUD = 46

    def __init__(self, cols: int = 28, rows: int = 20, rng: random.Random | None = None):
        super().__init__(None)                 # nessun genitore: finestra indipendente dall'applicazione
        self.setWindowTitle("SNAKE")
        self.setAttribute(Qt.WA_DeleteOnClose)
        self.setFocusPolicy(Qt.StrongFocus)
        self.game = SnakeGame(cols, rows, rng)
        self.best = 0
        self.state = "ready"
        self._blink = False
        self.setFixedSize(cols * self.CELL + 2 * 16, rows * self.CELL + self.HUD + 16 + 16)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self._tick)
        self.blink_timer = QTimer(self)
        self.blink_timer.setInterval(350)
        self.blink_timer.timeout.connect(self._toggle_blink)
        self.blink_timer.start()
        mono = QFont(QFontDatabase.systemFont(QFontDatabase.FixedFont))
        mono.setBold(True)
        self._font = mono
        self.setStyleSheet("SnakeWindow { background: #050805; }")

        # pulsanti della schermata di Game Over (senza focus: i tasti restano al gioco)
        self.restart_button = self._button("Ricomincia", self.restart)
        self.quit_button = self._button("Chiudi", self.close)
        row = QHBoxLayout()
        row.addStretch(1)
        row.addWidget(self.restart_button)
        row.addWidget(self.quit_button)
        row.addStretch(1)
        outer = QVBoxLayout(self)
        outer.addStretch(1)
        outer.addLayout(row)
        outer.addSpacing(self.height() // 5)
        outer.setContentsMargins(0, 0, 0, 0)
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
    def start_running(self) -> None:
        self.state = "running"
        self.timer.start(self.game.interval_ms())

    def restart(self) -> None:
        self.timer.stop()
        self.game.reset()
        self.state = "ready"
        for b in (self.restart_button, self.quit_button):
            b.hide()
        self.setFocus()
        self.update()

    def toggle_pause(self) -> None:
        if self.state == "running":
            self.timer.stop()
            self.state = "paused"
        elif self.state == "paused":
            self.start_running()
        self.update()

    def _tick(self) -> None:
        result = self.game.step()
        if result == "dead":
            self._game_over()
        else:
            if result == "eat":
                self.timer.setInterval(self.game.interval_ms())
                if self.game.over:                           # vittoria: la griglia è piena
                    self._game_over()
        self.update()

    def _game_over(self) -> None:
        self.timer.stop()
        self.state = "over"
        self.best = max(self.best, self.game.score)
        for b in (self.restart_button, self.quit_button):
            b.show()

    def _toggle_blink(self) -> None:
        self._blink = not self._blink
        self.update()

    # ---- tastiera
    def keyPressEvent(self, event: QKeyEvent) -> None:
        key = event.key()
        if key == Qt.Key_Escape:
            self.close()
            return
        if self.state == "over":
            if key in (Qt.Key_Return, Qt.Key_Enter, Qt.Key_R, Qt.Key_Space):
                self.restart()
            return
        if key in (Qt.Key_Space, Qt.Key_P):
            self.toggle_pause()
            return
        direction = KEYS.get(key)
        if direction is None:
            super().keyPressEvent(event)
            return
        if self.state == "paused":
            self.toggle_pause()
        if self.state == "ready":
            if direction == LEFT:                             # il serpente parte verso destra: niente inversione
                return
            self.game.turn(direction) if direction != RIGHT else None
            self.start_running()
        else:
            self.game.turn(direction)

    # ---- chiusura: nessun timer resta attivo
    def closeEvent(self, event) -> None:
        self.timer.stop()
        self.blink_timer.stop()
        super().closeEvent(event)

    def changeEvent(self, event) -> None:
        super().changeEvent(event)
        if event.type() == QEvent.ActivationChange and not self.isActiveWindow() and self.state == "running":
            self.toggle_pause()                               # in pausa se si passa a un'altra finestra

    # ---- disegno
    def _field(self) -> QRectF:
        return QRectF(16, self.HUD, self.game.cols * self.CELL, self.game.rows * self.CELL)

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, False)
        p.fillRect(self.rect(), BACKGROUND)
        field = self._field()
        c = self.CELL
        # punteggio
        p.setFont(self._font)
        p.setPen(PHOSPHOR)
        f = QFont(self._font)
        f.setPointSize(14)
        p.setFont(f)
        p.drawText(QRectF(16, 6, field.width() / 2, 30), Qt.AlignLeft | Qt.AlignVCenter, f"SCORE {self.game.score:04d}")
        p.drawText(QRectF(16 + field.width() / 2, 6, field.width() / 2, 30), Qt.AlignRight | Qt.AlignVCenter,
                   f"HI {max(self.best, self.game.score):04d}")
        # campo: cornice e puntini della griglia
        pen = QPen(PHOSPHOR, 3)
        p.setPen(pen)
        p.drawRect(field.adjusted(-2, -2, 2, 2))
        p.setPen(PHOSPHOR_DIM)
        for gx in range(self.game.cols):
            for gy in range(self.game.rows):
                p.drawPoint(QPointF(field.left() + gx * c + c / 2, field.top() + gy * c + c / 2))
        # cibo (lampeggia)
        if self.game.food is not None:
            fx, fy = self.game.food
            p.fillRect(QRectF(field.left() + fx * c + 4, field.top() + fy * c + 4, c - 8, c - 8), FOOD_A if self._blink else FOOD_B)
        # serpente
        for i, (x, y) in enumerate(self.game.snake):
            cell = QRectF(field.left() + x * c + 1, field.top() + y * c + 1, c - 2, c - 2)
            p.fillRect(cell, PHOSPHOR if i else QColor("#b6ff9e"))
            p.fillRect(cell.adjusted(4, 4, -4, -4), PHOSPHOR_DIM if i else QColor("#39ff14"))
        # messaggi
        if self.state == "ready":
            self._overlay(p, field, "PREMI UNA FRECCIA", "")
        elif self.state == "paused":
            self._overlay(p, field, "PAUSA", "SPAZIO PER RIPRENDERE")
        elif self.state == "over":
            self._overlay(p, field, "VITTORIA!" if self.game.won else "GAME OVER", f"PUNTEGGIO {self.game.score}   "
                          f"RECORD {self.best}", shift=-30)
        # scanline: righe orizzontali sottili che richiamano un monitor a tubo catodico
        p.setPen(QPen(QColor(0, 0, 0, 60), 1))
        for y in range(0, self.height(), 3):
            p.drawLine(0, y, self.width(), y)
        p.end()

    def _overlay(self, p: QPainter, field: QRectF, title: str, subtitle: str, shift: int = 0) -> None:
        p.fillRect(field, QColor(5, 8, 5, 190))
        big = QFont(self._font)
        big.setPointSize(26)
        p.setFont(big)
        p.setPen(PHOSPHOR)
        mid = field.center().y() + shift
        p.drawText(QRectF(field.left(), mid - 40, field.width(), 50), Qt.AlignCenter, title)
        small = QFont(self._font)
        small.setPointSize(12)
        p.setFont(small)
        p.drawText(QRectF(field.left(), mid + 10, field.width(), 30), Qt.AlignCenter, subtitle)


def launch() -> SnakeWindow:
    """Crea e mostra la finestra del gioco."""
    window = SnakeWindow()
    window.show()
    window.raise_()
    window.activateWindow()
    window.setFocus()
    return window
