"""Pong in stile arcade retrò (PySide6). Indipendente da CSV Revolution.

* ``PongGame``: le regole (palla, racchette, rimbalzi, punti, avversario controllato dal computer), senza interfaccia.
* ``PongWindow``: finestra a sé con disegno, timer e tastiera.

Comandi: W/S o frecce su/giù muovono la racchetta sinistra; 1 = contro il computer, 2 = due giocatori (a destra: frecce);
Spazio/P pausa o avvio, R ricomincia, Esc esce. Vince chi arriva a 7 punti."""
from __future__ import annotations

import random

from PySide6.QtCore import QEvent, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QFontDatabase, QKeyEvent, QPainter, QPen
from PySide6.QtWidgets import QHBoxLayout, QPushButton, QVBoxLayout, QWidget

W, H = 640, 400
PADDLE_W, PADDLE_H = 10, 72
BALL = 10
MARGIN_X = 24
PADDLE_SPEED = 6.0
CPU_SPEED = 4.4
BALL_START, BALL_MAX = 5.0, 12.0
WIN_SCORE = 7
SERVE_TICKS = 45


class PongGame:
    """Regole del gioco, in passi (un passo = un tick del timer). Giocatore 0 a sinistra, 1 a destra."""

    def __init__(self, mode: str = "cpu", rng: random.Random | None = None):
        self.rng = rng or random.Random()
        self.mode = mode                        # "cpu" | "two"
        self.reset()

    def reset(self) -> None:
        self.score = [0, 0]
        self.paddles = [(H - PADDLE_H) / 2, (H - PADDLE_H) / 2]       # coordinata y del bordo alto
        self.inputs = [0, 0]                    # -1 su, 0 fermo, +1 giù
        self.winner: int | None = None
        self.rallies = 0
        self._serve(self.rng.choice((0, 1)))

    @property
    def over(self) -> bool:
        return self.winner is not None

    def set_mode(self, mode: str) -> None:
        if mode in ("cpu", "two"):
            self.mode = mode
            self.reset()

    def _serve(self, toward: int) -> None:
        """Palla al centro, ferma per un attimo; poi parte verso il giocatore ``toward``."""
        self.bx, self.by = W / 2, H / 2
        self.serve_wait = SERVE_TICKS
        self.speed = BALL_START
        angle = self.rng.uniform(-0.5, 0.5)
        self.vx = self.speed * (1 if toward else -1)
        self.vy = self.speed * angle

    def set_input(self, player: int, direction: int) -> None:
        self.inputs[player] = max(-1, min(1, direction))

    def _move_paddle(self, i: int, delta: float) -> None:
        self.paddles[i] = max(0.0, min(H - PADDLE_H, self.paddles[i] + delta))

    def _cpu(self) -> None:
        """Il computer insegue la palla a velocità limitata (con una zona morta): si può battere."""
        center = self.paddles[1] + PADDLE_H / 2
        target = self.by if self.vx > 0 else H / 2
        diff = target - center
        if abs(diff) > 8:
            self._move_paddle(1, max(-CPU_SPEED, min(CPU_SPEED, diff)))

    def step(self) -> str:
        """Un passo. Ritorna "" o l'evento: "wall", "paddle", "score" (punto assegnato), "win" (fine partita)."""
        if self.over:
            return ""
        self._move_paddle(0, self.inputs[0] * PADDLE_SPEED)
        if self.mode == "cpu":
            self._cpu()
        else:
            self._move_paddle(1, self.inputs[1] * PADDLE_SPEED)
        if self.serve_wait > 0:
            self.serve_wait -= 1
            return ""
        self.bx += self.vx
        self.by += self.vy
        event = ""
        if self.by <= 0 or self.by + BALL >= H:                       # pareti alta e bassa
            self.by = max(0.0, min(H - BALL, self.by))
            self.vy = -self.vy
            event = "wall"
        left_face = MARGIN_X + PADDLE_W
        right_face = W - MARGIN_X - PADDLE_W
        for i, face in ((0, left_face), (1, right_face)):
            top = self.paddles[i]
            hits_x = (self.bx <= face and self.vx < 0) if i == 0 else (self.bx + BALL >= face and self.vx > 0)
            if hits_x and top - BALL <= self.by <= top + PADDLE_H:
                offset = (self.by + BALL / 2 - (top + PADDLE_H / 2)) / (PADDLE_H / 2)       # -1 (alto) .. +1 (basso)
                self.speed = min(BALL_MAX, self.speed * 1.06)
                direction = 1 if i == 0 else -1
                self.vx = direction * self.speed * 0.9
                self.vy = max(-self.speed * 0.85, min(self.speed * 0.85, offset * self.speed * 0.8))
                self.bx = face if i == 0 else face - BALL
                self.rallies += 1
                event = "paddle"
        if self.bx + BALL < 0:                                         # la palla esce a sinistra: punto a destra
            return self._point(1)
        if self.bx > W:
            return self._point(0)
        return event

    def _point(self, scorer: int) -> str:
        self.score[scorer] += 1
        if self.score[scorer] >= WIN_SCORE:
            self.winner = scorer
            return "win"
        self._serve(1 - scorer)                                        # serve chi ha subito il punto
        return "score"


# --------------------------------------------------------------------------- finestra

PHOSPHOR = QColor("#39ff14")
PHOSPHOR_DIM = QColor("#1c7a0b")
BACKGROUND = QColor("#050805")
HUD_H = 46
PAD = 16


class PongWindow(QWidget):
    """Finestra autonoma. Stati: "ready", "running", "paused", "over"."""

    def __init__(self, mode: str = "cpu", rng: random.Random | None = None):
        super().__init__(None)
        self.setWindowTitle("PONG")
        self.setAttribute(Qt.WA_DeleteOnClose)
        self.setFocusPolicy(Qt.StrongFocus)
        self.game = PongGame(mode, rng)
        self.state = "ready"
        self.held: set[int] = set()
        self.setFixedSize(W + 2 * PAD, H + HUD_H + 2 * PAD)
        self.timer = QTimer(self)
        self.timer.setInterval(16)
        self.timer.timeout.connect(self._tick)
        mono = QFont(QFontDatabase.systemFont(QFontDatabase.FixedFont))
        mono.setBold(True)
        self._font = mono
        self.setStyleSheet("PongWindow { background: #050805; }")
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
    def start_running(self) -> None:
        self.state = "running"
        self.timer.start()

    def restart(self) -> None:
        self.timer.stop()
        self.game.reset()
        self.held.clear()
        self.state = "ready"
        for b in (self.restart_button, self.quit_button):
            b.hide()
        self.setFocus()
        self.update()

    def set_mode(self, mode: str) -> None:
        self.game.set_mode(mode)
        self.restart()

    def toggle_pause(self) -> None:
        if self.state == "running":
            self.timer.stop()
            self.state = "paused"
        elif self.state in ("paused", "ready"):
            self.start_running()
        self.update()

    def _tick(self) -> None:
        self._apply_inputs()
        if self.game.step() == "win":
            self.timer.stop()
            self.state = "over"
            for b in (self.restart_button, self.quit_button):
                b.show()
        self.update()

    def _apply_inputs(self) -> None:
        up_l = Qt.Key_W in self.held or (self.game.mode == "cpu" and Qt.Key_Up in self.held)
        down_l = Qt.Key_S in self.held or (self.game.mode == "cpu" and Qt.Key_Down in self.held)
        self.game.set_input(0, int(down_l) - int(up_l))
        if self.game.mode == "two":
            self.game.set_input(1, int(Qt.Key_Down in self.held) - int(Qt.Key_Up in self.held))

    # ---- tastiera
    def keyPressEvent(self, event: QKeyEvent) -> None:
        key = event.key()
        if key == Qt.Key_Escape:
            self.close()
        elif key == Qt.Key_R:
            self.restart()
        elif key == Qt.Key_1:
            self.set_mode("cpu")
        elif key == Qt.Key_2:
            self.set_mode("two")
        elif key in (Qt.Key_Space, Qt.Key_P):
            if self.state == "over":
                self.restart()
            else:
                self.toggle_pause()
        elif key in (Qt.Key_W, Qt.Key_S, Qt.Key_Up, Qt.Key_Down):
            self.held.add(key)
            if self.state == "ready":
                self.start_running()
        else:
            super().keyPressEvent(event)

    def keyReleaseEvent(self, event: QKeyEvent) -> None:
        if not event.isAutoRepeat():
            self.held.discard(event.key())
        super().keyReleaseEvent(event)

    def closeEvent(self, event) -> None:
        self.timer.stop()
        super().closeEvent(event)

    def changeEvent(self, event) -> None:
        super().changeEvent(event)
        if event.type() == QEvent.ActivationChange and not self.isActiveWindow():
            self.held.clear()
            if self.state == "running":
                self.toggle_pause()

    # ---- disegno
    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, False)
        p.fillRect(self.rect(), BACKGROUND)
        g = self.game
        ox, oy = PAD, HUD_H + PAD // 2
        hud = QFont(self._font)
        hud.setPointSize(14)
        p.setFont(hud)
        p.setPen(PHOSPHOR)
        label = "VS COMPUTER" if g.mode == "cpu" else "2 GIOCATORI"
        p.drawText(QRectF(ox, 6, W, 30), Qt.AlignCenter, label)
        p.drawText(QRectF(ox, 6, W / 3, 30), Qt.AlignLeft | Qt.AlignVCenter, f"{g.score[0]:02d}")
        p.drawText(QRectF(ox + 2 * W / 3, 6, W / 3, 30), Qt.AlignRight | Qt.AlignVCenter, f"{g.score[1]:02d}")
        p.setPen(QPen(PHOSPHOR, 3))
        p.drawRect(QRectF(ox - 2, oy - 2, W + 4, H + 4))
        p.setPen(QPen(PHOSPHOR_DIM, 3))
        for y in range(0, H, 24):                                     # rete tratteggiata
            p.drawLine(int(ox + W / 2), int(oy + y), int(ox + W / 2), int(oy + y + 12))
        p.fillRect(QRectF(ox + MARGIN_X, oy + g.paddles[0], PADDLE_W, PADDLE_H), PHOSPHOR)
        p.fillRect(QRectF(ox + W - MARGIN_X - PADDLE_W, oy + g.paddles[1], PADDLE_W, PADDLE_H), PHOSPHOR)
        if g.serve_wait == 0 or self.state != "running" or g.serve_wait % 10 < 6:
            p.fillRect(QRectF(ox + g.bx, oy + g.by, BALL, BALL), QColor("#b6ff9e"))
        field = QRectF(ox, oy, W, H)
        if self.state == "ready":
            self._overlay(p, field, "PONG", "1: VS COMPUTER · 2: DUE GIOCATORI · SPAZIO PER INIZIARE")
        elif self.state == "paused":
            self._overlay(p, field, "PAUSA", "SPAZIO PER RIPRENDERE")
        elif self.state == "over":
            who = "HAI VINTO!" if g.winner == 0 and g.mode == "cpu" else \
                "VINCE IL COMPUTER" if g.mode == "cpu" else f"VINCE IL GIOCATORE {g.winner + 1}"
            self._overlay(p, field, "VITTORIA!" if g.winner == 0 and g.mode == "cpu" else "GAME OVER", who, shift=-30)
        p.setPen(QPen(QColor(0, 0, 0, 60), 1))
        for y in range(0, self.height(), 3):
            p.drawLine(0, y, self.width(), y)
        p.end()

    def _overlay(self, p: QPainter, field: QRectF, title: str, subtitle: str, shift: int = 0) -> None:
        mid = field.center().y() + shift
        p.fillRect(QRectF(field.left(), mid - 50, field.width(), 100), QColor(5, 8, 5, 225))
        big = QFont(self._font)
        big.setPointSize(26)
        p.setFont(big)
        p.setPen(PHOSPHOR)
        p.drawText(QRectF(field.left(), mid - 40, field.width(), 50), Qt.AlignCenter, title)
        small = QFont(self._font)
        small.setPointSize(11)
        p.setFont(small)
        p.drawText(QRectF(field.left(), mid + 10, field.width(), 30), Qt.AlignCenter, subtitle)


def launch() -> PongWindow:
    """Crea e mostra la finestra del gioco in primo piano."""
    window = PongWindow()
    window.show()
    window.raise_()
    window.activateWindow()
    window.setFocus()
    return window
