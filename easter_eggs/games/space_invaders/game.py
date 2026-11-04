"""Space Invaders in stile arcade retrò (PySide6). Indipendente da CSV Revolution.

* ``InvadersGame``: le regole (astronave, nemici a file, proiettili, collisioni, vite, ondate, punteggio), senza interfaccia.
* ``InvadersWindow``: finestra a sé con disegno, timer e tastiera.

Comandi: frecce sinistra/destra (o A/D) muovono l'astronave, Spazio spara, P pausa, R/Invio ricomincia dopo la fine,
Esc esce. Si vince battendo 5 ondate; si perde con 3 colpi subiti o se i nemici raggiungono l'astronave."""
from __future__ import annotations

import random

from PySide6.QtCore import QEvent, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QFontDatabase, QKeyEvent, QPainter, QPen
from PySide6.QtWidgets import QHBoxLayout, QPushButton, QVBoxLayout, QWidget

W, H = 480, 560
PLAYER_W, PLAYER_H = 40, 18
PLAYER_Y = H - 48
PLAYER_SPEED = 5.0
SHOT_SPEED = 11.0
ENEMY_SHOT_SPEED = 4.0
INV_W, INV_H = 28, 20
COLS, ROWS = 9, 5
GAP_X, GAP_Y = 44, 34
ROW_POINTS = [30, 20, 20, 10, 10]                # dall'alto verso il basso
LIVES = 3
WAVES_TO_WIN = 5
HIT_PAUSE = 60                                   # passi di invulnerabilità dopo un colpo subito


def rects_overlap(ax, ay, aw, ah, bx, by, bw, bh) -> bool:
    return ax < bx + bw and bx < ax + aw and ay < by + bh and by < ay + ah


class InvadersGame:
    """Regole del gioco, in passi. Coordinate in pixel del campo W×H; ``invaders`` = lista di [x, y, riga]."""

    def __init__(self, rng: random.Random | None = None):
        self.rng = rng or random.Random()
        self.reset()

    def reset(self) -> None:
        self.score = 0
        self.lives = LIVES
        self.wave = 1
        self.over = False
        self.won = False
        self.px = (W - PLAYER_W) / 2
        self.move = 0
        self.invulnerable = 0
        self.shot: list[float] | None = None                  # [x, y] del proiettile del giocatore (uno alla volta)
        self.enemy_shots: list[list[float]] = []
        self._new_wave()

    def _new_wave(self) -> None:
        self.invaders = [[24 + c * GAP_X, 56 + r * GAP_Y + min(self.wave - 1, 4) * 10, r]
                         for r in range(ROWS) for c in range(COLS)]
        self.dir = 1
        self.tick_count = 0
        self.shot = None
        self.enemy_shots = []

    # ---- comandi
    def set_move(self, direction: int) -> None:
        self.move = max(-1, min(1, direction))

    def fire(self) -> bool:
        """Spara se non c'è già un proiettile in volo."""
        if self.over or self.shot is not None:
            return False
        self.shot = [self.px + PLAYER_W / 2, PLAYER_Y - 6]
        return True

    # ---- avanzamento
    def _interval(self) -> int:
        """Passi tra due movimenti dei nemici: meno nemici e ondate avanzate = più veloci."""
        return max(2, len(self.invaders) // 4 + 1 - (self.wave - 1))

    def step(self) -> list[str]:
        """Un passo. Ritorna gli eventi accaduti: "kill", "hit" (astronave colpita), "wave", "win", "lose"."""
        events: list[str] = []
        if self.over:
            return events
        self.px = max(0.0, min(W - PLAYER_W, self.px + self.move * PLAYER_SPEED))
        if self.invulnerable:
            self.invulnerable -= 1
        # proiettile del giocatore
        if self.shot is not None:
            self.shot[1] -= SHOT_SPEED
            if self.shot[1] < 0:
                self.shot = None
            else:
                for inv in self.invaders:
                    if rects_overlap(self.shot[0] - 2, self.shot[1], 4, 12, inv[0], inv[1], INV_W, INV_H):
                        self.invaders.remove(inv)
                        self.score += ROW_POINTS[inv[2]] * 1
                        self.shot = None
                        events.append("kill")
                        break
        # nemici
        self.tick_count += 1
        if self.invaders and self.tick_count >= self._interval():
            self.tick_count = 0
            dx = 8 * self.dir
            if any(not (4 <= i[0] + dx <= W - INV_W - 4) for i in self.invaders):
                self.dir = -self.dir
                for i in self.invaders:
                    i[1] += 14
            else:
                for i in self.invaders:
                    i[0] += dx
        if self.invaders and max(i[1] for i in self.invaders) + INV_H >= PLAYER_Y:
            self.lives = 0
            self.over = True
            events.append("lose")
            return events
        # spari nemici: parte il nemico più in basso di una colonna a caso
        if self.invaders and self.rng.random() < 0.025 + 0.006 * self.wave and len(self.enemy_shots) < 3 + self.wave:
            column = self.rng.choice(sorted({i[0] for i in self.invaders}))
            shooter = max((i for i in self.invaders if i[0] == column), key=lambda i: i[1])
            self.enemy_shots.append([shooter[0] + INV_W / 2, shooter[1] + INV_H])
        for s in self.enemy_shots:
            s[1] += ENEMY_SHOT_SPEED + 0.4 * (self.wave - 1)
        self.enemy_shots = [s for s in self.enemy_shots if s[1] < H]
        if not self.invulnerable:
            for s in self.enemy_shots:
                if rects_overlap(s[0] - 2, s[1], 4, 12, self.px, PLAYER_Y, PLAYER_W, PLAYER_H):
                    self.enemy_shots.remove(s)
                    self.lives -= 1
                    self.invulnerable = HIT_PAUSE
                    events.append("hit")
                    break
        if self.lives <= 0:
            self.over = True
            events.append("lose")
        elif not self.invaders:
            if self.wave >= WAVES_TO_WIN:
                self.over = self.won = True
                events.append("win")
            else:
                self.wave += 1
                self._new_wave()
                events.append("wave")
        return events


# --------------------------------------------------------------------------- finestra

PHOSPHOR = QColor("#39ff14")
PHOSPHOR_DIM = QColor("#1c7a0b")
BACKGROUND = QColor("#050805")
RED, AMBER = QColor("#ff3b30"), QColor("#ffd60a")
HUD_H = 46
PAD = 16
ROW_COLORS = [QColor("#ff6ad5"), QColor("#64d2ff"), QColor("#64d2ff"), QColor("#39ff14"), QColor("#39ff14")]


class InvadersWindow(QWidget):
    """Finestra autonoma. Stati: "ready", "running", "paused", "over"."""

    def __init__(self, rng: random.Random | None = None):
        super().__init__(None)
        self.setWindowTitle("SPACE INVADERS")
        self.setAttribute(Qt.WA_DeleteOnClose)
        self.setFocusPolicy(Qt.StrongFocus)
        self.game = InvadersGame(rng)
        self.best = 0
        self.state = "ready"
        self.held: set[int] = set()
        self._frame = 0
        self.setFixedSize(W + 2 * PAD, H + HUD_H + 2 * PAD)
        self.timer = QTimer(self)
        self.timer.setInterval(20)
        self.timer.timeout.connect(self._tick)
        mono = QFont(QFontDatabase.systemFont(QFontDatabase.FixedFont))
        mono.setBold(True)
        self._font = mono
        self.setStyleSheet("InvadersWindow { background: #050805; }")
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

    def toggle_pause(self) -> None:
        if self.state == "running":
            self.timer.stop()
            self.state = "paused"
            self.held.clear()
        elif self.state in ("paused", "ready"):
            self.start_running()
        self.update()

    def _tick(self) -> None:
        self._frame += 1
        self.game.set_move(int(Qt.Key_Right in self.held or Qt.Key_D in self.held)
                           - int(Qt.Key_Left in self.held or Qt.Key_A in self.held))
        self.game.step()
        if self.game.over:
            self.timer.stop()
            self.state = "over"
            self.best = max(self.best, self.game.score)
            for b in (self.restart_button, self.quit_button):
                b.show()
        self.update()

    # ---- tastiera
    def keyPressEvent(self, event: QKeyEvent) -> None:
        key = event.key()
        if key == Qt.Key_Escape:
            self.close()
        elif self.state == "over":
            if key in (Qt.Key_Return, Qt.Key_Enter, Qt.Key_R, Qt.Key_Space):
                self.restart()
        elif key == Qt.Key_P:
            self.toggle_pause()
        elif key == Qt.Key_R:
            self.restart()
        elif key == Qt.Key_Space:
            if self.state == "running":
                if not event.isAutoRepeat():
                    self.game.fire()
            else:
                self.toggle_pause()
        elif key in (Qt.Key_Left, Qt.Key_Right, Qt.Key_A, Qt.Key_D):
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
        p.drawText(QRectF(ox, 6, W / 3, 30), Qt.AlignLeft | Qt.AlignVCenter, f"SCORE {g.score:04d}")
        p.drawText(QRectF(ox + W / 3, 6, W / 3, 30), Qt.AlignCenter, f"ONDATA {g.wave}")
        p.drawText(QRectF(ox + 2 * W / 3, 6, W / 3, 30), Qt.AlignRight | Qt.AlignVCenter, f"VITE {max(0, g.lives)}")
        p.setPen(QPen(PHOSPHOR, 3))
        p.drawRect(QRectF(ox - 2, oy - 2, W + 4, H + 4))
        p.setPen(QPen(PHOSPHOR_DIM, 2))
        p.drawLine(int(ox), int(oy + PLAYER_Y + PLAYER_H + 8), int(ox + W), int(oy + PLAYER_Y + PLAYER_H + 8))
        for x, y, row in g.invaders:                                  # nemici (due fotogrammi alternati)
            self._invader(p, ox + x, oy + y, ROW_COLORS[row], (self._frame // 12 + row) % 2)
        if not (g.invulnerable and (g.invulnerable // 4) % 2):        # astronave (lampeggia dopo un colpo)
            x, y = ox + g.px, oy + PLAYER_Y
            p.fillRect(QRectF(x, y + 8, PLAYER_W, 10), PHOSPHOR)
            p.fillRect(QRectF(x + 14, y + 2, 12, 8), PHOSPHOR)
            p.fillRect(QRectF(x + 18, y - 4, 4, 6), QColor("#b6ff9e"))
        if g.shot is not None:
            p.fillRect(QRectF(ox + g.shot[0] - 2, oy + g.shot[1], 4, 12), QColor("#b6ff9e"))
        for sx, sy in g.enemy_shots:
            p.fillRect(QRectF(ox + sx - 2, oy + sy, 4, 12), RED if int(sy) // 6 % 2 else AMBER)
        field = QRectF(ox, oy, W, H)
        if self.state == "ready":
            self._overlay(p, field, "SPACE INVADERS", "FRECCE PER INIZIARE · SPAZIO SPARA")
        elif self.state == "paused":
            self._overlay(p, field, "PAUSA", "SPAZIO PER RIPRENDERE")
        elif self.state == "over":
            self._overlay(p, field, "VITTORIA!" if g.won else "GAME OVER",
                          f"PUNTEGGIO {g.score}   RECORD {self.best}", shift=-30)
        p.setPen(QPen(QColor(0, 0, 0, 60), 1))
        for y in range(0, self.height(), 3):
            p.drawLine(0, y, self.width(), y)
        p.end()

    @staticmethod
    def _invader(p: QPainter, x: float, y: float, color: QColor, frame: int) -> None:
        p.fillRect(QRectF(x + 4, y + 2, INV_W - 8, 10), color)           # corpo
        p.fillRect(QRectF(x, y + 6, INV_W, 6), color)
        p.fillRect(QRectF(x + 7, y + 5, 4, 4), BACKGROUND)                # occhi
        p.fillRect(QRectF(x + INV_W - 11, y + 5, 4, 4), BACKGROUND)
        legs = (2, INV_W - 6) if frame else (6, INV_W - 10)               # zampe che si muovono
        for lx in legs:
            p.fillRect(QRectF(x + lx, y + 12, 4, 7), color)

    def _overlay(self, p: QPainter, field: QRectF, title: str, subtitle: str, shift: int = 0) -> None:
        mid = field.center().y() + shift
        p.fillRect(QRectF(field.left(), mid - 50, field.width(), 100), QColor(5, 8, 5, 225))
        big = QFont(self._font)
        big.setPointSize(24)
        p.setFont(big)
        p.setPen(PHOSPHOR)
        p.drawText(QRectF(field.left(), mid - 40, field.width(), 50), Qt.AlignCenter, title)
        small = QFont(self._font)
        small.setPointSize(11)
        p.setFont(small)
        p.drawText(QRectF(field.left(), mid + 10, field.width(), 30), Qt.AlignCenter, subtitle)


def launch() -> InvadersWindow:
    """Crea e mostra la finestra del gioco in primo piano."""
    window = InvadersWindow()
    window.show()
    window.raise_()
    window.activateWindow()
    window.setFocus()
    return window
