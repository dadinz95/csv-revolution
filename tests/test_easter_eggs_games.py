"""Nuovi easter egg: Campo Minato, Space Invaders, Pong (offscreen)."""
import random
import unittest

from PySide6.QtCore import QPointF, QEvent, Qt
from PySide6.QtGui import QKeyEvent, QMouseEvent
from PySide6.QtWidgets import QApplication

from easter_eggs import manager
from easter_eggs.games.campo_minato import game as mg
from easter_eggs.games.pong import game as pg
from easter_eggs.games.space_invaders import game as sg

APP = QApplication.instance() or QApplication([])


def key(w, k, mod=Qt.NoModifier):
    w.keyPressEvent(QKeyEvent(QEvent.KeyPress, k, mod))


class TestRegistry(unittest.TestCase):
    def test_all_games_registered(self):
        manager.discover(force=True)
        for n in ("snake", "campo_minato", "space_invaders", "pong"):
            self.assertIn(manager.normalize(n), manager.available())
        self.assertIs(manager.find(" Campo Minato "), manager.find("campo_minato"))
        self.assertIsNone(manager.find("inesistente"))

    def test_each_game_opens_in_foreground_and_cleans_up(self):
        for n in ("campo_minato", "space_invaders", "pong"):
            w = manager.start(n)
            self.assertIsNotNone(w, n)
            self.assertTrue(w.isVisible())
            self.assertIsNone(w.parent())
            w.close()
            self.assertFalse(w.timer.isActive())

    def test_bring_to_front_is_called_and_survives_closed_window(self):
        calls = []
        orig = manager.bring_to_front
        manager.bring_to_front = lambda w: calls.append(w)
        try:
            w = manager.start("pong")
        finally:
            manager.bring_to_front = orig
        self.assertEqual(calls, [w])
        w.close()
        manager.bring_to_front(w)          # nessuna eccezione dopo la chiusura


class TestMines(unittest.TestCase):
    def test_first_click_safe_and_flood(self):
        for seed in range(20):
            g = mg.MinesGame(10, 8, 12, random.Random(seed))
            g.reveal(4, 4)
            self.assertNotIn((4, 4), g.mines)
            self.assertEqual(len(g.mines), 12)
            self.assertIn((4, 4), g.revealed)
            self.assertEqual(g.count(4, 4), 0)          # i vicini sono liberi: scoperta a catena
            self.assertGreater(len(g.revealed), 1)

    def test_numbers_flags_win_and_lose(self):
        g = mg.MinesGame(5, 5, 3, random.Random(1))
        g.reveal(2, 2)
        m = sorted(g.mines)
        self.assertEqual(g.count(*next(iter(g.neighbors(*m[0])))) >= 1, True)
        safe = [(x, y) for y in range(5) for x in range(5) if (x, y) not in g.mines]
        g.toggle_flag(*m[0])
        self.assertIn(m[0], g.flags)
        self.assertEqual(g.reveal(*m[0]), [])            # la bandierina protegge
        self.assertEqual(g.state, "playing")
        g.toggle_flag(*m[0])
        for c in safe:
            g.reveal(*c)
        self.assertEqual(g.state, "won")
        h = mg.MinesGame(5, 5, 3, random.Random(1))
        h.reveal(2, 2)
        h.reveal(*sorted(h.mines)[0])
        self.assertEqual((h.state, h.exploded), ("lost", sorted(h.mines)[0]))
        self.assertEqual(h.reveal(0, 0), [])

    def test_chord(self):
        g = mg.MinesGame(5, 5, 3, random.Random(1))
        g.reveal(2, 2)
        for c in g.neighbors(0, 0):
            pass
        cell = next(c for c in g.revealed if g.count(*c) > 0)
        for n in g.neighbors(*cell):
            if n in g.mines:
                g.toggle_flag(*n)
        before = len(g.revealed)
        g.chord(*cell)
        self.assertGreaterEqual(len(g.revealed), before)

    def test_window_mouse(self):
        w = mg.MinesWindow(8, 6, 6, random.Random(3))
        w.show()
        pos = QPointF(w.MARGIN + 3.5 * w.CELL, w.HUD + w.MARGIN // 2 + 2.5 * w.CELL)
        w.mousePressEvent(QMouseEvent(QEvent.MouseButtonPress, pos, pos, Qt.LeftButton, Qt.LeftButton, Qt.NoModifier))
        self.assertEqual(w.game.state in ("playing", "won"), True)
        self.assertIn((3, 2), w.game.revealed)
        if w.game.state == "playing":
            self.assertTrue(w.timer.isActive())
        w.close()
        self.assertFalse(w.timer.isActive())
        w.deleteLater()


class TestInvaders(unittest.TestCase):
    def test_fire_kill_score_and_one_shot_at_a_time(self):
        g = sg.InvadersGame(random.Random(1))
        g.enemy_shots = []
        inv = g.invaders[-1]                              # riga in basso (10 punti)
        g.px = inv[0] + sg.INV_W / 2 - sg.PLAYER_W / 2
        self.assertTrue(g.fire())
        self.assertFalse(g.fire())
        g.shot[1] = inv[1] + sg.INV_H + 4                 # proiettile appena sotto il nemico
        n = len(g.invaders)
        g.rng.random = lambda: 1.0
        self.assertIn("kill", g.step())
        self.assertEqual(len(g.invaders), n - 1)
        self.assertEqual(g.score, 10)
        self.assertIsNone(g.shot)

    def test_hit_lives_and_game_over(self):
        g = sg.InvadersGame(random.Random(1))
        for lives in (2, 1, 0):
            g.enemy_shots = [[g.px + 20, sg.PLAYER_Y]]
            g.invulnerable = 0
            g.step()
            self.assertEqual(g.lives, lives)
        self.assertTrue(g.over and not g.won)

    def test_invaders_reaching_player_loses_and_waves_win(self):
        g = sg.InvadersGame(random.Random(1))
        for i in g.invaders:
            i[1] = sg.PLAYER_Y
        self.assertIn("lose", g.step())
        g = sg.InvadersGame(random.Random(1))
        for w in range(sg.WAVES_TO_WIN):
            g.invaders = []
            ev = g.step()
            self.assertIn("win" if w == sg.WAVES_TO_WIN - 1 else "wave", ev)
        self.assertTrue(g.won and g.over)

    def test_formation_moves_and_descends_at_the_edge(self):
        g = sg.InvadersGame(random.Random(1))
        g.rng.random = lambda: 1.0                       # nessun sparo nemico
        y0 = max(i[1] for i in g.invaders)
        for _ in range(2000):
            g.step()
            if g.over:
                break
        self.assertTrue(g.over or max(i[1] for i in g.invaders) > y0)

    def test_window_keys_and_timers(self):
        w = sg.InvadersWindow(random.Random(2))
        w.show()
        key(w, Qt.Key_Right)
        self.assertEqual(w.state, "running")
        self.assertTrue(w.timer.isActive())
        x = w.game.px
        w._tick()
        self.assertGreater(w.game.px, x)
        key(w, Qt.Key_P)
        self.assertEqual((w.state, w.timer.isActive()), ("paused", False))
        key(w, Qt.Key_P)
        key(w, Qt.Key_Space)
        self.assertIsNotNone(w.game.shot)
        w.close()
        self.assertFalse(w.timer.isActive())
        w.deleteLater()


class TestPong(unittest.TestCase):
    def test_bounces_scores_and_win(self):
        g = pg.PongGame("cpu", random.Random(1))
        g.serve_wait = 0
        g.bx, g.by, g.vx, g.vy = pg.W / 2, 1, 4, -3
        self.assertEqual(g.step(), "wall")
        self.assertGreater(g.vy, 0)
        g.bx, g.by, g.vx, g.vy = pg.MARGIN_X + pg.PADDLE_W + 1, g.paddles[0] + 30, -4, 0
        self.assertEqual(g.step(), "paddle")
        self.assertGreater(g.vx, 0)
        g.bx, g.by, g.vx, g.vy = 5, 5, -4, 0               # fuori a sinistra (racchetta lontana)
        g.paddles[0] = pg.H - pg.PADDLE_H
        for _ in range(10):
            r = g.step()
            if r == "score":
                break
        self.assertEqual(g.score, [0, 1])
        g.score = [0, pg.WIN_SCORE - 1]
        g.serve_wait = 0
        g.bx, g.by, g.vx, g.vy = 5, 5, -4, 0
        g.paddles[0] = pg.H - pg.PADDLE_H
        res = ""
        for _ in range(10):
            res = g.step()
            if res == "win":
                break
        self.assertEqual((res, g.winner), ("win", 1))
        self.assertTrue(g.over)

    def test_cpu_follows_ball_and_modes(self):
        g = pg.PongGame("cpu", random.Random(1))
        g.serve_wait = 99
        g.by, g.vx = 20, 1
        y = g.paddles[1]
        for _ in range(5):
            g.step()
        self.assertLess(g.paddles[1], y)
        self.assertLessEqual(y - g.paddles[1], 5 * pg.CPU_SPEED + 0.01)
        g.set_mode("two")
        self.assertEqual(g.score, [0, 0])
        g.set_input(1, 1)
        y = g.paddles[1]
        g.serve_wait = 99
        g.step()
        self.assertGreater(g.paddles[1], y)

    def test_window(self):
        w = pg.PongWindow(rng=random.Random(4))
        w.show()
        key(w, Qt.Key_W)
        self.assertEqual(w.state, "running")
        key(w, Qt.Key_2)
        self.assertEqual((w.game.mode, w.state), ("two", "ready"))
        self.assertFalse(w.timer.isActive())
        key(w, Qt.Key_Space)
        self.assertTrue(w.timer.isActive())
        w._tick()
        w.close()
        self.assertFalse(w.timer.isActive())
        w.deleteLater()


if __name__ == "__main__":
    unittest.main()
