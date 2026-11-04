"""Fase 5 - interfaccia: file già aperti, marcatore «modificato», messaggi, scorciatoie, rifiniture (offscreen).
Esecuzione:  QT_QPA_PLATFORM=offscreen python -m unittest tests.test_phase5_ui -v"""
import os
import unittest

from PySide6.QtCore import QMimeData, QUrl, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QComboBox, QDialog, QLabel, QLineEdit, QMessageBox, QSpinBox

from csv_reader.desktop import app as A
from csv_reader.desktop import shortcuts as SC
from csv_reader.desktop.theme import build_stylesheet, install_translations, LIGHT
from tests.test_phase4_ui import P4
from tests.test_ui import APP, pump, settle, sha


class TestOpenOnce(P4):
    def test_opening_the_same_file_again_brings_its_tab_to_front(self):
        a = self.open(self.make_csv("a.csv", rows=10, cols=3))
        b = self.open(self.make_csv("b.csv", rows=10, cols=3))
        self.assertIs(self.win.docs.currentWidget(), b)
        n = self.win.docs.count()
        self.win.open_path(a.path)                                    # «Apri file…»
        self.assertEqual(self.win.docs.count(), n)
        self.assertIs(self.win.docs.currentWidget(), a)
        self.win.docs.setCurrentWidget(b)
        self.win.open_path(os.path.join(os.path.dirname(a.path), ".", "a.csv"))      # stesso file, percorso scritto in altro modo
        self.assertEqual(self.win.docs.count(), n)
        self.assertIs(self.win.docs.currentWidget(), a)
        self.win.docs.setCurrentWidget(b)
        self.win.open_paths([a.path])                                 # apertura dal sistema
        self.assertEqual(self.win.docs.count(), n)
        self.assertIs(self.win.docs.currentWidget(), a)

    def test_dropping_an_open_file_does_not_duplicate_it(self):
        a = self.open(self.make_csv("a.csv", rows=10, cols=3))
        self.open(self.make_csv("b.csv", rows=10, cols=3))
        n = self.win.docs.count()

        class Event:
            def __init__(self, path):
                mime = QMimeData()
                mime.setUrls([QUrl.fromLocalFile(path)])
                self._mime = mime

            def mimeData(self):
                return self._mime

        self.win.dropEvent(Event(a.path))
        self.assertEqual(self.win.docs.count(), n)
        self.assertIs(self.win.docs.currentWidget(), a)

    def test_a_symlink_to_an_open_file_is_the_same_file(self):
        a = self.open(self.make_csv("a.csv", rows=5, cols=3))
        link = self.p("link.csv")
        try:
            os.symlink(a.path, link)
        except (OSError, NotImplementedError):                        # pragma: no cover
            self.skipTest("collegamenti simbolici non disponibili")
        n = self.win.docs.count()
        self.win.open_path(link)
        self.assertEqual(self.win.docs.count(), n)

    def test_excel_sheets_of_one_file_stay_distinct_tabs(self):
        path = self.make_xlsx("t.xlsx", rows=5)
        tab = self.open(path)
        first = tab.sheet
        self.assertIsNotNone(first)
        self.assertIs(self.win.find_open_tab(path, first), tab)
        self.assertIsNone(self.win.find_open_tab(path, "Altro foglio"))
        csv = self.make_csv("c.csv", rows=3, cols=2)
        self.assertIsNone(self.win.find_open_tab(csv, None))

    def test_new_csv_over_an_open_file_is_refused_before_asking_to_replace(self):
        a = self.open(self.make_csv("a.csv", rows=5, cols=3))
        win = self.win
        answers = []

        class Fake:
            def __init__(self, parent, folder):
                self.error = QLabel()
                self.spec = None
                self.runs = 0

            def exec(self):
                self.runs += 1
                if self.runs == 1:
                    self.spec = {"path": a.path, "columns": 2, "rows": 1, "delimiter": ",", "encoding": "utf-8"}
                    return QDialog.Accepted
                answers.append(self.error.text())
                return QDialog.Rejected

        orig = A.NewCsvDialog
        A.NewCsvDialog = Fake
        before = sha(a.path)
        try:
            win.new_csv()
        finally:
            A.NewCsvDialog = orig
        self.assertIn("aperto in una scheda", answers[0])             # l'avviso torna nella finestra, senza altre domande
        self.assertFalse(any(m[0] == "question" for m in self.messages))
        self.assertEqual(sha(a.path), before)


class TestUnsavedMarker(P4):
    def test_tab_title_and_tooltips_show_unsaved_changes(self):
        a = self.open(self.make_csv("a.csv", rows=10, cols=3))
        b = self.open(self.make_csv("b.csv", rows=10, cols=3))
        bar = self.win.docs
        name = lambda t: bar.tabText(bar.indexOf(t))
        tip = lambda t: bar.tabToolTip(bar.indexOf(t))
        self.assertEqual((name(a), name(b)), ("a.csv", "b.csv"))
        self.assertEqual(a.save_button.toolTip(), "Nessuna modifica da salvare")
        self.edit(a, 1, 1, "MOD")
        self.assertEqual(name(a), "• a.csv")                          # solo il file modificato è marcato
        self.assertEqual(name(b), "b.csv")
        self.assertIn("Modifiche non salvate", tip(a))
        self.assertIn(a.path, tip(a))
        self.assertNotIn("Modifiche non salvate", tip(b))
        self.assertIn("1 modifiche non salvate", a.save_button.toolTip())
        a.undo(); settle(self.win)
        self.assertEqual(name(a), "a.csv")
        self.assertEqual(a.save_button.toolTip(), "Nessuna modifica da salvare")

    def test_marker_follows_save(self):
        a = self.open(self.make_csv("a.csv", rows=10, cols=3))
        self.edit(a, 1, 1, "MOD")
        self.assertTrue(self.win.docs.tabText(0 if self.win.global_tab is None else 1).startswith("• "))
        a.engine.mark_saved()
        a._update_save_label()
        self.assertEqual(self.win.docs.tabText(self.win.docs.indexOf(a)), "a.csv")

    def test_excel_tab_keeps_sheet_line_in_tooltip(self):
        tab = self.open(self.make_xlsx("t.xlsx", rows=5))
        index = self.win.docs.indexOf(tab)
        self.edit(tab, 0, 1, "MOD")
        self.assertIn("Foglio:", self.win.docs.tabToolTip(index))
        self.assertIn("·", self.win.docs.tabText(index))


class TestHonestMessages(P4):
    def test_excel_delete_messages_promise_the_original_is_untouched(self):
        tab = self.open(self.make_xlsx("t.xlsx", rows=8))
        self.assertIn("file Excel originale non viene mai modificato", tab._persist_note())
        self.box_answer = A.QMessageBox.No
        tab.delete_column_prompt(tab.columns[-1]); settle(self.win)
        self.assertIn("file Excel originale non viene mai modificato", self.boxes[-1][1])

    def test_csv_messages_do_not_claim_the_original_is_untouched(self):
        tab = self.open(self.make_csv("a.csv", rows=8, cols=3))
        note = tab._persist_note()
        self.assertIn("«Salva» sostituisce il file aperto", note)
        self.assertNotIn("non viene mai modificato", note)

    def test_replace_all_confirmation_says_it_can_be_undone(self):
        tab = self.open(self.make_csv("a.csv", rows=10, cols=3))
        d = tab.open_find() or tab.find_dialog
        d.find_edit.setText("valore"); d.replace_edit.setText("X")
        self.box_answer = A.QMessageBox.No
        tab.replace_all(); settle(self.win)
        text = self.boxes[-1][1]
        self.assertIn("Puoi annullare", text)
        self.assertNotIn("non può essere annullata", text)

    def test_confirmations_use_explicit_button_labels(self):
        seen = []
        orig = A.QMessageBox.exec
        A.QMessageBox.exec = lambda b: seen.append((b.windowTitle(), b.button(QMessageBox.Yes).text(),
                                                    b.button(QMessageBox.No).text(),
                                                    b.defaultButton() is b.button(QMessageBox.No))) or QMessageBox.No
        try:
            tab = self.open(self.make_csv("a.csv", rows=10, cols=3))
            tab.delete_column_prompt("col1")
            tab.table.selectRow(0)
            tab.delete_rows_prompt()
        finally:
            A.QMessageBox.exec = orig
        self.assertEqual(seen[0], ("Elimina colonna", "Elimina colonna", "Annulla", True))
        self.assertEqual(seen[1], ("Elimina righe", "Elimina riga", "Annulla", True))

    def test_italian_translation_for_standard_buttons(self):
        self.assertTrue(install_translations(APP))
        try:
            box = QMessageBox()
            box.setStandardButtons(QMessageBox.Yes | QMessageBox.No)
            self.assertEqual(box.button(QMessageBox.Yes).text().replace("&", ""), "Sì")
        finally:
            for t in getattr(APP, "_translators", []):
                APP.removeTranslator(t)

    def test_export_sql_to_the_open_file_is_refused_and_the_file_is_intact(self):
        tab = self.open(self.make_csv("a.csv", rows=20, cols=3))
        before = sha(tab.path)
        orig = A.QFileDialog.getSaveFileName
        A.QFileDialog.getSaveFileName = staticmethod(lambda *a, **k: (tab.path, "CSV (*.csv)"))
        try:
            tab.sql_input.setPlainText("SELECT * FROM csv_data LIMIT 3")
            tab.export_sql()
            settle(self.win)
        finally:
            A.QFileDialog.getSaveFileName = orig
        self.assertEqual(sha(tab.path), before)
        self.assertTrue(any(m[0] == "critical" and "aperto" in str(m[1]) for m in self.messages), self.messages)


class TestShortcutsAndMenus(P4):
    def test_ctrl_f_from_the_sql_editor_does_nothing_but_works_on_data(self):
        tab = self.open(self.make_csv("a.csv", rows=10, cols=3))
        tab.inner.setCurrentIndex(1)
        settle(self.win)
        tab.sql_input.setFocus()
        APP.processEvents()
        QTest.keyClick(tab.sql_input, Qt.Key_F, Qt.ControlModifier)
        APP.processEvents()
        self.assertTrue(tab.find_dialog is None or not tab.find_dialog.isVisible())
        tab.inner.setCurrentIndex(0)
        tab.table.setFocus()
        QTest.keyClick(tab.table, Qt.Key_F, Qt.ControlModifier)
        APP.processEvents()
        self.assertTrue(tab.find_dialog is not None and tab.find_dialog.isVisible())
        tab.find_dialog.hide()

    def test_tab_key_is_documented_and_reachable_from_the_menu(self):
        row = next(r for r in SC.SHORTCUTS if r[1] == "Dettaglio record")
        self.assertEqual(row[2], "Tab")
        self.assertIn("Tab", SC.hint("Dettaglio record"))
        tab = self.open(self.make_csv("a.csv", rows=5, cols=3))
        labels = [a.text() for a in tab.edit_menu.actions()]
        self.assertTrue(any(t.startswith("Dettaglio record selezionati") and "Tab" in t for t in labels), labels)

    def test_menu_tooltips_are_enabled(self):
        tab = self.open(self.make_csv("a.csv", rows=5, cols=3))
        self.assertTrue(tab.edit_menu.toolTipsVisible())
        self.assertTrue(tab.save_menu.toolTipsVisible())
        self.assertIn("Ctrl+F", tab.find_button.toolTip())


class TestPolish(P4):
    def test_status_text_is_elided_with_full_text_in_tooltip(self):
        tab = self.open(self.make_csv("a.csv", rows=5, cols=3))
        text = "Righe 1–200 di 369.974 filtrate · Totale 1.000.000"
        tab.status.setFixedWidth(80)                                  # il layout non deve ridimensionarla di nascosto
        tab.status.setText(text)
        APP.processEvents()
        self.assertEqual(tab.status.text(), text)                     # il testo vero non cambia
        self.assertEqual(tab.status.toolTip(), text)
        tab.status.grab()                                             # il disegno con «…» non deve dare errori
        tab.status.setFixedWidth(700)
        APP.processEvents()
        self.assertEqual(tab.status.toolTip(), "")

    def test_combo_boxes_have_the_same_height_as_text_fields(self):
        edit, combo, spin = QLineEdit(), QComboBox(), QSpinBox()
        for w in (edit, combo, spin):
            w.ensurePolished()
        heights = (edit.sizeHint().height(), combo.sizeHint().height(), spin.sizeHint().height())
        self.assertLessEqual(max(heights) - min(heights), 1, heights)

    def test_clear_filters_is_enabled_only_when_there_is_something_to_clear(self):
        tab = self.open(self.make_csv("a.csv", rows=30, cols=3))
        self.assertFalse(tab.clear_filters_button.isEnabled())
        tab.search_input.setText("valore 1")
        self.assertTrue(tab.clear_filters_button.isEnabled())
        tab.search_input.clear()
        self.assertFalse(tab.clear_filters_button.isEnabled())
        self.drive_filter(tab, "col0", "lt", "10")
        self.assertTrue(tab.clear_filters_button.isEnabled())
        tab.clear_filters(); settle(self.win)
        self.assertFalse(tab.clear_filters_button.isEnabled())

    def test_ghost_buttons_look_different_enabled_and_disabled(self):
        css = build_stylesheet(LIGHT, "Sans", "Mono")
        self.assertIn("QPushButton#ghost:disabled", css)
        enabled = css.split("QPushButton#ghost {")[1].split("}")[0]
        self.assertIn(LIGHT["accent"], enabled)                       # attivo: colore d'accento, non il grigio del testo semplice
        self.assertNotIn(LIGHT["muted"], enabled)
        tab = self.open(self.make_csv("a.csv", rows=5, cols=3))
        self.assertFalse(tab.restore_sql_button.isEnabled())


if __name__ == "__main__":
    unittest.main()
