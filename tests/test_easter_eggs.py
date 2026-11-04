"""v0.19.0 - easter egg: attivazione dal logo, finestra di attivazione, registro dei giochi, Snake (offscreen).
Esecuzione:  QT_QPA_PLATFORM=offscreen python -m unittest tests.test_easter_eggs -v"""
import random
import sys
import types
import unittest

import shiboken6
from PySide6.QtCore import QEvent, QPoint, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QLineEdit, QPushButton

import easter_eggs
from easter_eggs import manager
from easter_eggs.activation import ActivationDialog, open_activation
from easter_eggs.games.snake.game import DOWN, LEFT, RIGHT, UP, SnakeGame, SnakeWindow
from tests.test_phase4_ui import P4
from tests.test_ui import APP, settle


def shown(widget) -> bool:
    """Visibile e non ancora distrutta (le finestre dei giochi hanno WA_DeleteOnClose)."""
    return shiboken6.isValid(widget) and widget.isVisible()


def close_if_alive(widget):
    if shiboken6.isValid(widget):
        widget.close()


class TestManager(unittest.TestCase):
    def test_snake_is_found_ignoring_case_and_spaces(self):
        for text in ("snake", "Snake", "  SNAKE  ", "sn ake", "Sn-ake", "sn_ake"):
            self.assertIsNotNone(manager.find(text), text)
        for text in ("", "   ", None, "snakes", "tetris"):
            self.assertIsNone(manager.find(text), text)
        self.assertIn("snake", manager.available())

    def test_registering_a_new_game_needs_no_changes_to_the_manager(self):
        calls = []
        egg = manager.register("Fake Game", lambda: calls.append(1), aliases=("fg",))
        self.addCleanup(lambda: [manager._registry.pop(k, None) for k in ("fakegame", "fg")])
        self.assertEqual(egg.name, "fakegame")
        self.assertIs(manager.find("FAKE game"), egg)
        self.assertIs(manager.find("FG"), egg)
        manager.start("fake game")
        self.assertEqual(calls, [1])
        with self.assertRaises(ValueError):
            manager.register("other", lambda: None, aliases=("fg",))      # alias già usato
        with self.assertRaises(ValueError):
            manager.register("   ", lambda: None)

    def test_game_package_is_discovered_from_the_folder(self):
        import easter_eggs.games as games
        pkg = types.ModuleType("easter_eggs.games.dummy")
        pkg.NAME, pkg.launch = "dummy", lambda: None
        pkg.__path__ = []
        sys.modules["easter_eggs.games.dummy"] = pkg
        self.addCleanup(lambda: (sys.modules.pop("easter_eggs.games.dummy"), manager._registry.pop("dummy", None),
                                 setattr(games, "KNOWN_GAMES", ("snake",))))
        games.KNOWN_GAMES = ("snake", "dummy")                          # simula la cartella games/dummy/
        manager.discover(force=True)
        self.assertIsNotNone(manager.find("dummy"))

    def test_broken_game_is_skipped_and_does_not_break_others(self):
        import easter_eggs.games as games
        games.KNOWN_GAMES = ("snake", "missing_game")
        self.addCleanup(setattr, games, "KNOWN_GAMES", ("snake",))
        manager.discover(force=True)
        self.assertIsNotNone(manager.find("snake"))
        self.assertIsNone(manager.find("missing_game"))

    def test_launch_failure_returns_none(self):
        manager.register("boom", lambda: 1 / 0)
        self.addCleanup(manager._registry.pop, "boom", None)
        self.assertIsNone(manager.start("boom"))


class TestSnakeRules(unittest.TestCase):
    def game(self):
        return SnakeGame(10, 8, random.Random(1))

    def test_initial_state(self):
        g = self.game()
        self.assertEqual(len(g.snake), 3)
        self.assertEqual(g.direction, RIGHT)
        self.assertEqual(g.score, 0)
        self.assertNotIn(g.food, g.snake)

    def test_moves_and_cannot_reverse(self):
        g = self.game()
        head = g.snake[0]
        self.assertEqual(g.step(), "move")
        self.assertEqual(g.snake[0], (head[0] + 1, head[1]))
        self.assertFalse(g.turn(LEFT))                                # inversione a 180°
        self.assertFalse(g.turn(RIGHT))                               # stessa direzione
        self.assertTrue(g.turn(UP))
        self.assertFalse(g.turn(DOWN))                                # inversione rispetto all'ultima svolta in coda
        g.step()
        self.assertEqual(g.snake[0], (head[0] + 1, head[1] - 1))

    def test_eating_grows_scores_and_respawns_food_on_a_free_cell(self):
        g = self.game()
        g.food = (g.snake[0][0] + 1, g.snake[0][1])
        self.assertEqual(g.step(), "eat")
        self.assertEqual((len(g.snake), g.score), (4, 10))
        self.assertNotIn(g.food, g.snake)
        n = len(g.snake)
        g.step()
        self.assertEqual(len(g.snake), n)                              # senza cibo non cresce

    def test_wall_collision_ends_the_game(self):
        g = self.game()
        g.food = (0, 0)
        result = None
        for _ in range(20):
            result = g.step()
            if result == "dead":
                break
        self.assertEqual(result, "dead")
        self.assertTrue(g.over)
        self.assertEqual(g.step(), "dead")

    def test_self_collision_ends_the_game_but_following_the_tail_is_allowed(self):
        g = SnakeGame(10, 10, random.Random(2))
        g.snake = [(5, 5), (5, 6), (4, 6), (4, 5), (4, 4)]            # a U: la testa può seguire la coda
        g.direction, g.food = UP, (0, 0)
        g.pending.clear()
        g.turn(LEFT)
        self.assertEqual(g.step(), "dead")                             # (4,5) è corpo e non è la coda: collisione
        self.assertTrue(g.over)
        g2 = SnakeGame(10, 10, random.Random(2))
        g2.snake = [(5, 5), (6, 5), (6, 4), (5, 4)]                    # quadrato: la coda (5,4) si libera nello stesso passo
        g2.direction, g2.food = UP, (0, 0)
        self.assertEqual(g2.step(), "move")
        self.assertFalse(g2.over)

    def test_speed_increases_with_score_with_a_floor(self):
        g = self.game()
        base = g.interval_ms()
        g.score = 100
        self.assertLess(g.interval_ms(), base)
        g.score = 100000
        self.assertEqual(g.interval_ms(), 55)

    def test_filling_the_grid_is_a_win(self):
        g = SnakeGame(4, 1, random.Random(3))
        g.snake, g.direction, g.food = [(1, 0), (0, 0)], RIGHT, (2, 0)
        g.step()
        self.assertEqual(g.food, (3, 0))
        g.step()
        self.assertTrue(g.over and g.won)


class TestSnakeWindow(unittest.TestCase):
    def setUp(self):
        self.w = SnakeWindow(12, 10, random.Random(5))
        self.addCleanup(close_if_alive, self.w)
        self.w.show()
        APP.processEvents()

    def key(self, key):
        QTest.keyClick(self.w, key)
        APP.processEvents()

    def test_waits_for_first_arrow_then_runs_and_arrows_steer(self):
        self.assertEqual(self.w.state, "ready")
        self.assertFalse(self.w.timer.isActive())
        self.key(Qt.Key_Up)
        self.assertEqual(self.w.state, "running")
        self.assertTrue(self.w.timer.isActive())
        head = self.w.game.snake[0]
        self.w._tick()
        self.assertEqual(self.w.game.snake[0], (head[0], head[1] - 1))
        self.key(Qt.Key_Left)
        self.w._tick()
        self.assertEqual(self.w.game.snake[0], (head[0] - 1, head[1] - 1))

    def test_first_key_left_is_ignored_because_the_snake_faces_right(self):
        self.key(Qt.Key_Left)
        self.assertEqual(self.w.state, "ready")

    def test_game_over_shows_buttons_and_restart_works(self):
        self.key(Qt.Key_Down)
        for _ in range(30):
            if self.w.state == "over":
                break
            self.w._tick()
        self.assertEqual(self.w.state, "over")
        self.assertFalse(self.w.timer.isActive())
        self.assertFalse(self.w.restart_button.isHidden())
        self.assertFalse(self.w.quit_button.isHidden())
        self.w.grab()                                                 # il disegno di Game Over non solleva eccezioni
        self.key(Qt.Key_Return)
        self.assertEqual(self.w.state, "ready")
        self.assertEqual(self.w.game.score, 0)
        self.assertTrue(self.w.restart_button.isHidden())

    def test_best_score_is_kept_across_restarts(self):
        self.key(Qt.Key_Down)
        self.w.game.score = 40
        for _ in range(30):
            if self.w.state == "over":
                break
            self.w._tick()
        self.w.restart()
        self.assertEqual(self.w.best, 40)

    def test_pause_and_escape(self):
        self.key(Qt.Key_Down)
        self.key(Qt.Key_Space)
        self.assertEqual(self.w.state, "paused")
        self.assertFalse(self.w.timer.isActive())
        self.key(Qt.Key_Space)
        self.assertEqual(self.w.state, "running")
        self.key(Qt.Key_Escape)
        self.assertFalse(shown(self.w))

    def test_closing_stops_every_timer_and_deletes_the_window(self):
        self.key(Qt.Key_Down)
        timers = (self.w.timer, self.w.blink_timer)
        self.assertTrue(all(t.isActive() for t in timers))
        self.w.close()
        self.assertFalse(any(t.isActive() for t in timers))

    def test_window_is_independent_and_all_screens_paint(self):
        self.assertIsNone(self.w.parent())
        for state in ("ready", "running", "paused", "over"):
            self.w.state = state
            self.w.grab()


class TestActivationDialog(unittest.TestCase):
    def setUp(self):
        self.opened = []
        self.addCleanup(lambda: [close_if_alive(w) for w in manager.open_windows()])

    def test_contains_only_input_start_and_cancel(self):
        d = ActivationDialog()
        self.addCleanup(d.close)
        self.assertEqual(len(d.findChildren(QLineEdit)), 1)
        self.assertEqual(sorted(b.text() for b in d.findChildren(QPushButton)), ["Annulla", "Avvia"])
        self.assertEqual(d.input.text(), "")

    def test_start_recognised_name_opens_independent_window_and_closes_dialog(self):
        d = open_activation()
        d.input.setText("  Snake ")
        QTest.mouseClick(d.start_button, Qt.LeftButton)
        APP.processEvents()
        self.assertIsInstance(d.game, SnakeWindow)
        self.assertTrue(shown(d.game))
        self.assertIsNone(d.game.parent())
        self.assertFalse(shown(d))
        self.assertIn(d.game, manager.open_windows())

    def test_enter_does_the_same_as_start(self):
        d = open_activation()
        QTest.keyClicks(d.input, "snake")
        QTest.keyClick(d.input, Qt.Key_Return)
        APP.processEvents()
        self.assertIsInstance(d.game, SnakeWindow)
        self.assertFalse(shown(d))

    def test_unknown_name_just_closes(self):
        before = len(manager.open_windows())
        d = open_activation()
        d.input.setText("pippo")
        QTest.mouseClick(d.start_button, Qt.LeftButton)
        APP.processEvents()
        self.assertIsNone(d.game)
        self.assertFalse(shown(d))
        self.assertLessEqual(len(manager.open_windows()), before)      # nessuna finestra in più (se ne può essere chiusa una vecchia)

    def test_empty_input_closes_without_starting(self):
        d = open_activation()
        QTest.keyClick(d.input, Qt.Key_Return)
        APP.processEvents()
        self.assertIsNone(d.game)
        self.assertFalse(shown(d))

    def test_cancel_and_escape_start_nothing(self):
        before = len(manager.open_windows())
        d = open_activation()
        d.input.setText("snake")
        QTest.mouseClick(d.cancel_button, Qt.LeftButton)
        APP.processEvents()
        self.assertFalse(shown(d))
        d = open_activation()
        d.input.setText("snake")
        QTest.keyClick(d, Qt.Key_Escape)
        APP.processEvents()
        self.assertFalse(shown(d))
        self.assertLessEqual(len(manager.open_windows()), before)      # nessuna finestra in più (se ne può essere chiusa una vecchia)

    def test_closed_game_is_released_from_the_manager(self):
        d = open_activation()
        d.input.setText("snake")
        d.launch()
        game = d.game
        game.close()
        for _ in range(5):
            APP.sendPostedEvents(None, QEvent.DeferredDelete)
            APP.processEvents()
        self.assertEqual([w for w in manager.open_windows() if w is game], [])


class TestLogoTrigger(P4):
    def logo(self):
        from csv_reader.desktop.app import LogoLabel
        self.win.show()
        APP.processEvents()
        label = self.win.findChild(LogoLabel, "appTitle")
        self.assertIsNotNone(label)
        return label

    def click(self, label, point):
        QTest.mouseClick(label, Qt.LeftButton, Qt.NoModifier, point)

    def test_seven_clicks_on_the_o_open_the_dialog_and_counter_resets(self):
        label = self.logo()
        got = []
        label.secretActivated.connect(lambda: got.append(1))
        centre = label.letter_rect().center()
        for _ in range(6):
            self.click(label, centre)
        self.assertEqual(got, [])
        self.click(label, centre)
        self.assertEqual(got, [1])
        self.assertIsInstance(self.win._hidden_dialog, ActivationDialog)
        self.win._hidden_dialog.close()
        for _ in range(6):
            self.click(label, centre)
        self.assertEqual(got, [1])                                 # dopo l'attivazione il conteggio riparte da zero
        self.click(label, centre)
        self.assertEqual(got, [1, 1])
        self.win._hidden_dialog.close()

    def test_only_the_o_counts(self):
        label = self.logo()
        got = []
        label.secretActivated.connect(lambda: got.append(1))
        fm = label.fontMetrics()
        rect = label.letter_rect()
        self.assertEqual(label.text()[7], "o")
        others = [QPoint(2, rect.center().y()),                                        # «C»
                  QPoint(fm.horizontalAdvance("CSV Rev") - 3, rect.center().y()),      # «v», a sinistra della «o»
                  QPoint(fm.horizontalAdvance("CSV Revo") + 4, rect.center().y()),     # «l», a destra
                  QPoint(rect.center().x(), 1)]                                        # sopra la lettera
        for _ in range(10):
            for pt in others:
                self.click(label, pt)
        self.assertEqual(got, [])

    def test_slow_clicks_restart_the_count_and_logo_still_behaves_as_a_label(self):
        label = self.logo()
        got = []
        label.secretActivated.connect(lambda: got.append(1))
        label.MAX_GAP_MS = 0
        centre = label.letter_rect().center()
        for _ in range(8):
            self.click(label, centre)
            QTest.qWait(3)
        self.assertEqual(got, [])
        self.assertEqual(label.text(), "CSV Revolution")

    def test_app_works_without_the_easter_egg_package(self):
        label = self.logo()
        saved = {k: sys.modules.pop(k) for k in list(sys.modules) if k.startswith("easter_eggs")}
        sys.modules["easter_eggs"] = None                           # l'importazione fallisce
        try:
            self.win._open_hidden_games()                           # non solleva
        finally:
            sys.modules.pop("easter_eggs", None)
            sys.modules.update(saved)
        self.assertTrue(label.isVisible())


if __name__ == "__main__":
    unittest.main()
