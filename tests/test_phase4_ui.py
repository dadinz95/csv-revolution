"""Fase 4 - Annulla, copia/incolla, navigazione da tastiera, menu Salva, finestra stabile (interfaccia, offscreen).
Esecuzione:  QT_QPA_PLATFORM=offscreen python -m unittest tests.test_phase4_ui -v"""
import os
import unittest

from PySide6.QtCore import Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QTableWidgetSelectionRange

from csv_reader.desktop import app as A
from tests.test_phase3_ui import P3Ui, read_rows
from tests.test_ui import APP, pump, settle, sha

from tests import legacy_types


def setUpModule():
    legacy_types.install()


def tearDownModule():
    legacy_types.uninstall()


CTRL, SHIFT = Qt.ControlModifier, Qt.ShiftModifier


def texts(tab, col=None):
    t = tab.table
    return [[t.item(r, c).text() for c in range(t.columnCount())] for r in range(t.rowCount())]


class P4(P3Ui):
    def key(self, tab, key, mods=Qt.NoModifier):
        tab.table.setFocus()
        QTest.keyClick(tab.table, key, mods)
        settle(self.win)
        APP.processEvents()

    def cell(self, tab, r, c):
        tab.table.setCurrentCell(r, c)
        tab.table.setFocus()
        APP.processEvents()


class TestUndo(P4):
    def test_empty_history_is_safe(self):
        tab = self.open(self.make_csv(rows=10, cols=3))
        self.key(tab, Qt.Key_Z, CTRL)
        self.assertIn("nulla da annullare", tab.status.text())
        self.assertFalse(tab.has_unsaved())

    def test_cell_edit_undo_restores_value_and_unsaved_state(self):
        tab = self.open(self.make_csv(rows=10, cols=3))
        before = texts(tab)
        self.edit(tab, 2, 1, "CIAO")
        self.assertTrue(tab.has_unsaved())
        self.assertEqual(tab.save_button.text(), "Salva (1)")
        self.key(tab, Qt.Key_Z, CTRL)
        self.assertEqual(texts(tab), before)
        self.assertFalse(tab.has_unsaved())
        self.assertEqual(tab.save_button.text(), "Salva")

    def test_undo_with_filter_and_sort_refreshes_view_and_counts(self):
        tab = self.open(self.make_csv(rows=50, cols=3))
        self.drive_filter(tab, "col0", "lt", "10")
        self.assertEqual(tab._filtered_count, 10)
        self.edit(tab, 0, 0, "500")                               # esce dal filtro
        settle(self.win)
        self.assertEqual(tab._filtered_count, 9)
        tab.undo(); settle(self.win)
        self.assertEqual(tab._filtered_count, 10)
        self.assertEqual(tab.table.item(0, 0).text(), "0")

    def test_replace_all_is_one_undo_step(self):
        tab = self.open(self.make_csv(rows=30, cols=3))
        before = tab.engine.con.execute("SELECT * FROM csv_data").fetchall()
        d = tab.open_find() or tab.find_dialog
        d.find_edit.setText("valore"); d.replace_edit.setText("X")
        tab.replace_all(); settle(self.win)
        self.assertEqual(tab.engine.con.execute("SELECT count(*) FROM csv_data WHERE col1 = 'X 1-1'").fetchone()[0], 1)
        self.assertTrue(tab.has_unsaved())
        tab.undo(); settle(self.win)
        self.assertEqual(tab.engine.con.execute("SELECT * FROM csv_data").fetchall(), before)
        self.assertFalse(tab.has_unsaved())
        self.assertFalse(tab.engine.can_undo())

    def test_column_add_rename_delete_undo(self):
        tab = self.open(self.make_csv(rows=10, cols=4))
        cols = list(tab.columns)
        before = tab.engine.con.execute("SELECT * FROM csv_data").fetchall()
        eng = tab.engine
        tab.run_op(lambda: (eng.add_column("nuova", "col1"), eng.count_total())[1], lambda t: tab._structure_changed(t)); settle(self.win)
        tab.run_op(lambda: (eng.rename_column("col2", "due"), eng.count_total())[1], lambda t: tab._renamed("col2", "due", t)); settle(self.win)
        tab.hidden_columns.add("col3"); tab.apply_hidden_columns()
        self.box_answer = A.QMessageBox.Yes
        tab.delete_column_prompt("col3"); settle(self.win)
        self.assertNotIn("col3", tab.columns)
        for _ in range(3):
            tab.undo(); settle(self.win)
        self.assertEqual(tab.columns, cols)
        self.assertEqual(tab.engine.con.execute("SELECT * FROM csv_data").fetchall(), before)
        self.assertIn("col3", tab.hidden_columns)                    # stato dell'interfaccia ripristinato
        self.assertFalse(tab.has_unsaved())

    def test_delete_rows_undo_restores_order_and_numbers(self):
        tab = self.open(self.make_csv(rows=20, cols=3))
        before = tab.engine.con.execute('SELECT * FROM csv_data').fetchall()
        for r in (2, 5, 7):
            tab.table.setRangeSelected(QTableWidgetSelectionRange(r, 0, r, 2), True)
        n = len(tab._selected_rids())
        tab.delete_rows_prompt(); settle(self.win)
        self.assertEqual(tab.total_rows, 20 - n)
        tab.undo(); settle(self.win)
        self.assertEqual(tab.total_rows, 20)
        self.assertEqual(tab.engine.con.execute('SELECT * FROM csv_data').fetchall(), before)
        self.assertFalse(tab.has_unsaved())

    def test_undo_not_intercepted_in_text_fields(self):
        tab = self.open(self.make_csv(rows=10, cols=3))
        self.edit(tab, 1, 1, "UNO")
        tab.search_input.setFocus()
        tab.search_input.setText("abc")
        QTest.keyClick(tab.search_input, Qt.Key_Z, CTRL)
        self.assertTrue(tab.has_unsaved())                            # il dataset non è stato annullato
        tab.inner.setCurrentIndex(1)
        tab.sql_input.setFocus()
        tab.sql_input.setPlainText("select 1")
        QTest.keyClick(tab.sql_input, Qt.Key_Z, CTRL)
        self.assertTrue(tab.has_unsaved())                            # nell'editor SQL vale l'annulla dell'editor

    def test_undo_of_type_conversion(self):
        path = self.p("n.csv")
        with open(path, "w") as fh:
            fh.write("a,b\n" + "".join(f"{i},{i * 2}\n" for i in range(10)))
        tab = self.open(path)
        dtype = tab.engine.dtypes["b"]
        self.edit(tab, 1, 1, "testo")
        self.assertEqual(tab.engine.dtypes["b"], "VARCHAR")
        tab.undo(); settle(self.win)
        self.assertEqual(tab.engine.dtypes["b"], dtype)
        self.assertEqual(tab.table.item(1, 1).text(), "2")

    def test_undo_does_not_touch_excel_original(self):
        path = self.make_xlsx()
        h = sha(path)
        tab = self.open_excel(path)
        self.edit(tab, 1, 2, "ZZ")
        tab.undo(); settle(self.win)
        self.assertEqual(sha(path), h)
        self.assertFalse(tab.has_unsaved())


class TestCopyPaste(P4):
    def test_copy_is_excel_tsv_with_quoting(self):
        tab = self.open(self.make_csv(rows=5, cols=3))
        self.edit(tab, 0, 1, "a\tb"); self.edit(tab, 0, 2, 'c"d\ne')
        tab.table.setRangeSelected(QTableWidgetSelectionRange(0, 0, 1, 2), True)
        tab.table.setFocus()
        QTest.keyClick(tab.table, Qt.Key_C, CTRL)
        text = QGuiApplication.clipboard().text()
        self.assertTrue(text.startswith('0\t"a\tb"\t"c""d\ne"\n1\t'))

    def test_copy_skips_hidden_columns(self):
        tab = self.open(self.make_csv(rows=5, cols=4))
        tab.hidden_columns.add("col1"); tab.apply_hidden_columns()
        tab.table.selectAll()
        tab.table.copy_selection()
        first = QGuiApplication.clipboard().text().split("\n")[0].split("\t")
        self.assertEqual(len(first), 3)
        self.assertNotIn("valore 0-1", first)

    def test_paste_block_is_single_undo_and_marks_unsaved(self):
        tab = self.open(self.make_csv(rows=10, cols=4))
        before = tab.engine.con.execute("SELECT * FROM csv_data").fetchall()
        QGuiApplication.clipboard().setText("A1\tB1\r\nA2\tB2\r\nA3\tB3\r\n")
        self.cell(tab, 2, 1)
        self.key(tab, Qt.Key_V, CTRL)
        self.assertEqual([tab.table.item(r, c).text() for r in (2, 3, 4) for c in (1, 2)], ["A1", "B1", "A2", "B2", "A3", "B3"])
        self.assertTrue(tab.has_unsaved())
        self.assertIn("Incollate 6 celle", tab.status.text())
        self.key(tab, Qt.Key_Z, CTRL)
        self.assertEqual(tab.engine.con.execute("SELECT * FROM csv_data").fetchall(), before)
        self.assertFalse(tab.has_unsaved())

    def test_paste_refuses_beyond_dataset_without_changes(self):
        tab = self.open(self.make_csv(rows=5, cols=3))
        QGuiApplication.clipboard().setText("1\t2\t3\n4\t5\t6\n7\t8\t9")
        self.cell(tab, 4, 0)
        self.key(tab, Qt.Key_V, CTRL)
        self.assertFalse(tab.has_unsaved())
        self.assertTrue(any(m[1][0] == "Incolla non possibile" for m in self.messages))
        self.cell(tab, 0, 2)
        QGuiApplication.clipboard().setText("1\t2")
        self.key(tab, Qt.Key_V, CTRL)
        self.assertFalse(tab.has_unsaved())
        self.assertEqual(sum(1 for m in self.messages if m[1][0] == "Incolla non possibile"), 2)

    def test_paste_skips_hidden_columns_and_uses_view_order_with_sort_and_pages(self):
        tab = self.open(self.make_csv(rows=120, cols=4))
        tab.page_size = 50
        tab.sort = ("col0", True); tab.filters_changed(); settle(self.win)
        tab.hidden_columns.add("col2"); tab.apply_hidden_columns()
        QGuiApplication.clipboard().setText("X\tY\nZ\tW")
        self.cell(tab, 49, 1)                                         # ultima riga della pagina: la seconda è nella pagina dopo
        self.key(tab, Qt.Key_V, CTRL)
        got = tab.engine.con.execute("SELECT col0, col1, col2, col3 FROM csv_data WHERE col1 IN ('X','Z') OR col3 IN ('Y','W') ORDER BY col0").fetchall()
        by = {r[0]: r for r in got}
        self.assertEqual(by[70][1], "X"); self.assertEqual(by[70][3], "Y")          # la 50ª riga in ordine decrescente è 70
        self.assertEqual(by[69][1], "Z"); self.assertEqual(by[69][3], "W")
        self.assertEqual(by[70][2], "valore 70-2")                                 # la colonna nascosta non è toccata

    def test_paste_single_value_fills_selection(self):
        tab = self.open(self.make_csv(rows=10, cols=3))
        QGuiApplication.clipboard().setText("Q")
        tab.table.setRangeSelected(QTableWidgetSelectionRange(1, 1, 3, 2), True)
        tab.table.setCurrentCell(1, 1, )
        tab.table.setRangeSelected(QTableWidgetSelectionRange(1, 1, 3, 2), True)
        self.key(tab, Qt.Key_V, CTRL)
        self.assertEqual([tab.table.item(r, c).text() for r in (1, 2, 3) for c in (1, 2)], ["Q"] * 6)

    def test_paste_incompatible_text_converts_column_with_message(self):
        path = self.p("n.csv")
        with open(path, "w") as fh:
            fh.write("a,b\n" + "".join(f"{i},{i}\n" for i in range(5)))
        tab = self.open(path)
        QGuiApplication.clipboard().setText("12\nabc")
        self.cell(tab, 0, 1)
        self.key(tab, Qt.Key_V, CTRL)
        self.assertIn("diventate di testo", tab.status.text())
        tab.undo(); settle(self.win)
        self.assertNotEqual(tab.engine.dtypes["b"], "VARCHAR")

    def test_paste_does_not_run_while_typing_in_a_text_field(self):
        tab = self.open(self.make_csv(rows=5, cols=3))
        QGuiApplication.clipboard().setText("ZZZ")
        tab.search_input.setFocus()
        QTest.keyClick(tab.search_input, Qt.Key_V, CTRL)
        self.assertEqual(tab.search_input.text(), "ZZZ")
        self.assertFalse(tab.has_unsaved())


class TestNavigation(P4):
    def make_gaps(self, n=300):
        path = self.p("gaps.csv")
        with open(path, "w") as fh:
            fh.write("a,b,c,d\n")
            for i in range(n):
                a = "" if i in (5, 6, 7) else f"v{i}"
                fh.write(f"{a},{'' if i % 2 else 'x'},,z\n")
        return path

    def test_ctrl_down_follows_excel_rules(self):
        tab = self.open(self.make_gaps())
        self.cell(tab, 0, 0)
        self.key(tab, Qt.Key_Down, CTRL)
        self.assertEqual(tab.table.currentRow(), 4)                   # fine del blocco 0..4
        self.key(tab, Qt.Key_Down, CTRL)
        self.assertEqual(tab.table.currentRow(), 8)                   # prima cella piena dopo il vuoto
        self.key(tab, Qt.Key_Up, CTRL)
        self.assertEqual(tab.table.currentRow(), 4)

    def test_ctrl_arrow_leaves_only_the_destination_selected(self):
        tab = self.open(self.make_gaps())
        self.cell(tab, 0, 0)
        for key, expect in ((Qt.Key_Down, (4, 0)), (Qt.Key_Down, (8, 0)), (Qt.Key_Right, (8, 1)), (Qt.Key_Right, (8, 3)), (Qt.Key_Left, (8, 1)), (Qt.Key_Up, (6, 1))):
            self.key(tab, key, CTRL)
            self.assertEqual((tab.table.currentRow(), tab.table.currentColumn()), expect)
            self.assertEqual([(i.row(), i.column()) for i in tab.table.selectedIndexes()], [expect])

    def test_ctrl_arrow_selection_is_independent_of_pressed_modifiers_state(self):
        # Qt decide «sostituisci o aggiungi» leggendo i modificatori globali: con Ctrl premuto setCurrentCell aggiunge
        tab = self.open(self.make_gaps())
        self.cell(tab, 0, 0)
        orig = A.QApplication.keyboardModifiers
        A.QApplication.keyboardModifiers = staticmethod(lambda: Qt.ControlModifier)
        try:
            self.key(tab, Qt.Key_Down, CTRL)
        finally:
            A.QApplication.keyboardModifiers = orig
        self.assertEqual([(i.row(), i.column()) for i in tab.table.selectedIndexes()], [(4, 0)])

    def test_ctrl_down_changes_page_and_ctrl_home_end(self):
        tab = self.open(self.make_gaps(300))
        tab.page_size = 50
        tab.offset = 0; tab.load_page(); settle(self.win)
        self.cell(tab, 10, 3)                                         # colonna d: tutta piena
        self.key(tab, Qt.Key_Down, CTRL)
        settle(self.win)
        self.assertEqual(tab.offset, 250)
        self.assertEqual(tab.offset + tab.table.currentRow(), 299)
        self.key(tab, Qt.Key_Up, CTRL)
        self.assertEqual(tab.offset + tab.table.currentRow(), 0)
        self.key(tab, Qt.Key_End, CTRL)
        self.assertEqual(tab.offset + tab.table.currentRow(), 299)
        self.assertEqual(tab.table.currentColumn(), 3)
        self.key(tab, Qt.Key_Home, CTRL)
        self.assertEqual((tab.offset, tab.table.currentRow(), tab.table.currentColumn()), (0, 0, 0))

    def test_ctrl_shift_down_extends_selection(self):
        tab = self.open(self.make_gaps())
        self.cell(tab, 0, 0)
        self.key(tab, Qt.Key_Down, CTRL | SHIFT)
        rows = tab.table.selected_rows()
        self.assertEqual((rows[0], rows[-1]), (0, 4))
        self.assertEqual(tab.table.currentRow(), 4)

    def test_shift_arrow_extends(self):
        tab = self.open(self.make_gaps())
        self.cell(tab, 2, 1)
        self.key(tab, Qt.Key_Down, SHIFT); self.key(tab, Qt.Key_Right, SHIFT)
        self.assertEqual(len(tab.table.selectedIndexes()), 4)

    def test_horizontal_jump_skips_hidden_columns(self):
        tab = self.open(self.make_gaps())
        tab.hidden_columns.add("b"); tab.apply_hidden_columns()
        self.cell(tab, 0, 0)
        self.key(tab, Qt.Key_Right, CTRL)
        self.assertNotEqual(tab.table.currentColumn(), 1)

    def test_navigation_respects_filter_and_sort(self):
        tab = self.open(self.make_gaps())
        tab.sort = ("a", True); tab.filters_changed(); settle(self.win)
        self.cell(tab, 0, 3)
        self.key(tab, Qt.Key_Down, CTRL)
        self.assertEqual(tab.offset + tab.table.currentRow(), 299)

    def test_no_navigation_while_typing_in_search(self):
        tab = self.open(self.make_gaps())
        self.cell(tab, 0, 0)
        tab.search_input.setFocus()
        QTest.keyClick(tab.search_input, Qt.Key_Down, CTRL)
        self.assertEqual(tab.table.currentRow(), 0)


class TestSqlErrors(P4):
    def crit(self):
        return [m[1][1] for m in self.messages if m[0] == "critical"]

    def run_sql(self, tab, sql):
        tab.inner.setCurrentIndex(1)
        tab.sql_input.setPlainText(sql)
        tab.run_sql()
        pump(lambda: not tab.is_busy(), 10)
        settle(self.win)

    def test_errors_show_only_the_duckdb_message(self):
        import logging
        log_path = A.setup_logging()
        tab = self.open(self.make_csv("q.csv", 10, 3))
        self.run_sql(tab, "SELECT col0 FROM csv_data LIMIT 2")
        self.assertEqual(self.crit(), [])
        self.assertEqual(tab.sql_result.rowCount(), 2)
        cases = [("SELECT * FROM t13", ["Catalog Error", "t13", "does not exist", "LINE 1"]),
                 ("SELECT nonesiste FROM csv_data", ["Binder Error", "nonesiste", "LINE 1"]),
                 ("SELEC 1", ["Parser Error", "syntax error", "LINE 1"])]
        for sql, needles in cases:
            self.messages.clear()
            self.run_sql(tab, sql)
            shown = self.crit()
            self.assertEqual(len(shown), 1, sql)
            for needle in needles:
                self.assertIn(needle, shown[0], sql)
            for forbidden in ("Traceback", "File \"", "app.py", "engine.py", "line ", "_duckdb.", "self.fn"):
                self.assertNotIn(forbidden, shown[0].replace("LINE ", "LINE_"), sql)
        for h in logging.getLogger("csv_revolution").handlers:
            h.flush()
        with open(log_path, encoding="utf-8") as fh:
            text = fh.read()
        self.assertIn("Traceback (most recent call last)", text)       # il dettaglio tecnico resta nel log
        self.assertIn("Catalog Error", text)

    def test_global_sql_errors_have_no_traceback(self):
        self.open(self.make_csv("a.csv", 5, 2)); self.open(self.make_csv("b.csv", 5, 2))
        g = self.win.global_tab
        self.win.docs.setCurrentWidget(g)
        g.sql_input.setPlainText("SELECT * FROM t13")
        g.run_sql()
        pump(lambda: not g.is_busy(), 10)
        settle(self.win)
        shown = self.crit()
        self.assertEqual(len(shown), 1)
        self.assertIn('Did you mean', shown[0])
        self.assertNotIn("Traceback", shown[0])
        self.assertNotIn("app.py", shown[0])


class TestPolishAndName(P4):
    def test_application_name_is_csv_revolution(self):
        self.assertEqual(self.win.windowTitle(), "CSV Revolution")
        labels = [l.text() for l in self.win.top_bar.findChildren(A.QLabel)]
        self.assertIn("CSV Revolution", labels)
        import re
        for rel in ("packaging/csv_reader.spec", "packaging/build.ps1", "packaging/build.sh", "packaging/windows/installer.iss",
                    "packaging/linux/install.sh", "packaging/linux/csv-revolution.desktop", "README.md", "README_LITE.md",
                    "DESCRIZIONE_PROGETTO.md", "csv_reader/desktop/app.py"):
            with open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), rel), encoding="utf-8") as fh:
                text = fh.read()
            self.assertIsNone(re.search(r"CSV Reader", text), rel)

    def test_grid_has_thin_vertical_lines_also_when_scrolled(self):
        tab = self.open(self.make_csv("g.csv", 200, 30))
        self.win.resize(900, 600); settle(self.win)
        t = tab.table
        t.setColumnWidth(0, 100)

        def lines_visible():
            img = t.viewport().grab().toImage()
            y = 40
            x = t.columnViewportPosition(1) - 1
            pix = img.pixelColor(x, y)
            neighbour = img.pixelColor(x - 8, y)
            return pix != neighbour

        self.assertTrue(lines_visible())
        t.horizontalScrollBar().setValue(60); t.verticalScrollBar().setValue(300); APP.processEvents()
        self.assertTrue(lines_visible())

    def test_save_and_import_buttons_have_equal_height_at_several_window_sizes(self):
        tab = self.open(self.make_csv(rows=5, cols=3))
        for w, h in ((1200, 760), (800, 600), (1600, 900)):
            self.win.resize(w, h); settle(self.win)
            self.assertEqual(tab.import_button.height(), tab.save_button.height())
            self.assertEqual(tab.import_button.geometry().top(), tab.save_button.geometry().top())
        self.win.set_top_hidden(True); settle(self.win)
        self.assertEqual(tab.import_button.height(), tab.save_button.height())

    def test_rows_per_page_field_has_inner_padding(self):
        from csv_reader.desktop.theme import build_stylesheet, LIGHT
        css = build_stylesheet(LIGHT, "Arial", "Courier").replace("  ", " ")
        self.assertIn("QSpinBox { padding: 5px 10px 5px 10px;", css)               # verifica statica del foglio di stile
        tab = self.open(self.make_csv(rows=5, cols=3))
        tab.page_size_box.setValue(250000); APP.processEvents()
        self.assertGreaterEqual(tab.page_size_box.width(), tab.page_size_box.fontMetrics().horizontalAdvance("250000") + 20)


class TestEnterInCell(P4):
    def edit_with_enter(self, tab, row, col, text, key=Qt.Key_Return):
        t = tab.table
        self.cell(tab, row, col)
        t.edit(t.currentIndex()); APP.processEvents()
        editor = APP.focusWidget()
        self.assertIsInstance(editor, A.QLineEdit)
        editor.setText(text)
        QTest.keyClick(editor, key)
        settle(self.win); APP.processEvents()

    def test_enter_saves_the_value_and_moves_to_the_cell_below(self):
        tab = self.open(self.make_csv("e.csv", 20, 3))
        self.edit_with_enter(tab, 2, 1, "NUOVO")
        self.assertEqual(tab.engine.cell_text(2, "col1"), "NUOVO")                  # salvato nel dataset
        self.assertTrue(tab.has_unsaved())
        self.assertEqual((tab.table.currentRow(), tab.table.currentColumn()), (3, 1))
        self.assertEqual([(i.row(), i.column()) for i in tab.table.selectedIndexes()], [(3, 1)])
        self.assertNotEqual(tab.table.state(), A.QAbstractItemView.EditingState)
        self.edit_with_enter(tab, 3, 1, "ANCORA", Qt.Key_Enter)
        self.assertEqual(tab.engine.cell_text(3, "col1"), "ANCORA")
        self.assertEqual(tab.table.currentRow(), 4)

    def test_enter_on_last_row_stays_and_escape_does_not_save_or_move(self):
        tab = self.open(self.make_csv("e.csv", 5, 3))
        self.edit_with_enter(tab, 4, 0, "FINE")
        self.assertEqual(tab.engine.cell_text(4, "col0"), "FINE")
        self.assertEqual(tab.table.currentRow(), 4)
        self.edit_with_enter(tab, 1, 1, "NO", Qt.Key_Escape)
        self.assertNotEqual(tab.engine.cell_text(1, "col1"), "NO")
        self.assertEqual(tab.table.currentRow(), 1)

    def test_enter_at_end_of_page_goes_to_next_page(self):
        tab = self.open(self.make_csv("e.csv", 120, 3))
        tab.page_size = 50; tab.offset = 0; tab.load_page(); settle(self.win)
        self.edit_with_enter(tab, 49, 1, "P")
        pump(lambda: tab.offset == 50 and not tab.is_busy(), 10); settle(self.win)
        self.assertEqual((tab.offset, tab.table.currentRow()), (50, 0))
        self.assertEqual(tab.engine.cell_text(49, "col1"), "P")

    def test_enter_follows_view_order_with_sort(self):
        tab = self.open(self.make_csv("e.csv", 20, 3))
        tab.sort = ("col0", True); tab.filters_changed(); settle(self.win)
        self.edit_with_enter(tab, 0, 1, "TOP")                                      # record 19 in cima
        self.assertEqual(tab.engine.cell_text(19, "col1"), "TOP")
        self.assertEqual(tab.table.currentRow(), 1)


class TestNoLayoutJump(P4):
    def test_progress_bar_appearing_does_not_move_the_interface(self):
        tab = self.open(self.make_csv("j.csv", 300, 4))
        self.win.show(); settle(self.win)
        def geoms():
            return (tab.table.geometry(), tab.status.mapTo(self.win, tab.status.rect().topLeft()),
                    tab.page_size_box.mapTo(self.win, tab.page_size_box.rect().topLeft()))
        self.assertTrue(tab.progress.sizePolicy().retainSizeWhenHidden())
        before = geoms()
        tab.progress.show(); APP.processEvents()
        self.assertEqual(geoms(), before)
        tab.progress.hide(); APP.processEvents()
        self.assertEqual(geoms(), before)

    def test_layout_is_stable_during_ctrl_arrow_and_undo(self):
        tab = self.open(self.make_csv("j.csv", 300, 4))
        self.win.show(); settle(self.win)
        self.edit(tab, 1, 1, "X")
        seen = set()
        def sample():
            seen.add((tab.table.geometry().getRect(), tab.status.mapTo(self.win, tab.status.rect().topLeft()).y()))
        timer = A.QTimer(); timer.timeout.connect(sample); timer.start(1)
        self.cell(tab, 0, 0)
        self.key(tab, Qt.Key_Down, CTRL)
        tab.undo()
        pump(lambda: not tab.is_busy() and not tab._ops, 10); settle(self.win)
        timer.stop()
        self.assertEqual(len(seen), 1, seen)


class TestSaveMenuAndLabels(P4):
    def test_save_is_a_dropdown_and_cf_renamed(self):
        tab = self.open(self.make_csv(rows=5, cols=3))
        self.assertEqual(tab.save_button.popupMode(), A.QToolButton.InstantPopup)
        self.assertEqual([a.text().split("\t")[0] for a in tab.save_menu.actions()], ["Salva", "Salva con nome…"])
        self.assertTrue(tab.cf_button.text().startswith("Formattazione condizionale…"))
        self.assertNotIn("Formattazione", [a.text() for a in tab.edit_menu.actions()])

    def test_save_dialogs_carry_the_former_tooltips(self):
        tab = self.open(self.make_csv(rows=5, cols=3))
        self.edit(tab)
        seen = self.drive_save(accept=False)
        tab.save(); settle(self.win)
        self.assertEqual(seen["dialogs"], 1)
        from csv_reader.desktop.tools_dialogs import SaveDialog
        d = SaveDialog(tab, "Salva", mode="save", working_path=tab.path, working_format="csv", protected_path=None,
                       default_format="csv", defaults=tab.engine.export_defaults(), notes=tab._save_notes())
        self.assertIn("nel file corrente", d.intro.text())                 # CSV: nessun riferimento a Excel
        self.assertNotIn("Excel", d.intro.text())
        dx = SaveDialog(tab, "Salva", mode="save", working_path=tab.path, working_format="xlsx", protected_path=tab.path,
                        default_format="xlsx", defaults=tab.engine.export_defaults(), notes=tab._save_notes())
        self.assertIn("un file Excel originale non viene mai sovrascritto", dx.intro.text())
        d2 = SaveDialog(tab, "Salva con nome", mode="save_as", working_path=tab.path, working_format="csv", protected_path=None,
                        default_format="csv", defaults=tab.engine.export_defaults(), notes=tab._save_notes())
        self.assertIn("CSV o XLSX", d2.intro.text())


class TestWindowStability(P4):
    def run_cycle(self, hidden):
        long = "x" * 140
        path = self.make_csv(long + ".csv", 20, 3)
        tab = self.open(path)
        self.win.show(); settle(self.win)
        self.win.set_top_hidden(hidden)
        settle(self.win)
        self.edit(tab)
        w0, h0 = self.win.width(), self.win.height()
        m0 = self.win.minimumSizeHint().width()
        dest = self.p(("y" * 120) + ".xlsx")
        self.drive_save(path=dest, fmt="xlsx")
        self.box_click = "Continua sul file corrente"
        tab.save_as(); settle(self.win)
        tab.status.setText("Z" * 400)
        settle(self.win)
        self.assertEqual((self.win.width(), self.win.height()), (w0, h0))
        self.assertLessEqual(self.win.minimumSizeHint().width(), max(m0, 900))

    def test_window_does_not_grow_with_top_bar_visible(self):
        self.run_cycle(False)

    def test_window_does_not_grow_with_top_bar_hidden(self):
        self.run_cycle(True)

    def test_hide_and_restore_top_bar_keeps_layout(self):
        tab = self.open(self.make_csv(rows=5, cols=3))
        self.win.show(); settle(self.win)
        size = self.win.size()
        self.win.set_top_hidden(True); settle(self.win)
        self.win.set_top_hidden(False); settle(self.win)
        self.assertEqual(self.win.size(), size)

class TestNewCsvTab(P4):
    def drive_new(self, columns="3", rows="4", name="nuovo", folder=None, encoding=None, delimiter=None, accept=True, repeat=None):
        from csv_reader.desktop.tools_dialogs import NewCsvDialog
        seen = {"runs": 0}
        orig = NewCsvDialog.exec

        def fake(d):
            seen["runs"] += 1
            d.columns_edit.setText(columns); d.rows_edit.setText(rows)
            d.folder_edit.setText(folder or self.dir); d.name_edit.setText(name)
            if encoding:
                d.enc_box.setCurrentIndex(d.enc_box.findData(encoding))
            if delimiter:
                d.delim_box.setCurrentIndex(d.delim_box.findData(delimiter))
            if not accept:
                return A.QDialog.Rejected
            d._accept()
            seen["error"] = d.error.text()
            return A.QDialog.Accepted if d.spec else A.QDialog.Rejected

        NewCsvDialog.exec = fake
        self.addCleanup(setattr, NewCsvDialog, "exec", orig)
        return seen

    def test_start_screen_has_only_open_and_top_bar_has_new_csv_next_to_it(self):
        win = self.win
        self.assertIs(win.stack.currentWidget(), win.empty_state)
        buttons = [b.text() for b in win.empty_state.findChildren(A.QPushButton)]
        self.assertEqual(buttons, ["Apri file…"])                           # al centro solo l'apertura dei file
        self.assertTrue(win.acceptDrops())
        self.assertEqual(win.new_button.text(), "+ Nuovo CSV")
        top = win.top_bar.layout()
        order = [top.itemAt(i).widget() for i in range(top.count()) if top.itemAt(i).widget() is not None]
        self.assertEqual(order.index(win.new_button) + 1, order.index(win.open_button))   # affiancati
        self.assertEqual(win.new_button.height() > 0, True)

    def test_there_is_no_plus_tab_and_global_sql_still_works(self):
        docs = self.win.docs
        self.assertEqual(docs.count(), 0)
        a = self.open(self.make_csv("a.csv", 5, 2))
        self.open(self.make_csv("b.csv", 5, 2))
        self.assertEqual([docs.tabText(i) for i in range(docs.count())], ["SQL globale", "a.csv", "b.csv"])
        self.assertNotIn("+", [docs.tabText(i) for i in range(docs.count())])
        self.win.close_widget(a, force=True); settle(self.win)
        self.win.close_widget(self.win.csv_tabs()[0], force=True); settle(self.win)
        self.assertEqual(docs.count(), 0)
        self.assertIs(self.win.stack.currentWidget(), self.win.empty_state)

    def test_new_csv_button_runs_wizard_with_and_without_open_files(self):
        seen = self.drive_new(accept=False)
        self.win.new_button.click()
        self.assertEqual(seen["runs"], 1)
        self.open(self.make_csv("a.csv", 5, 2))
        self.win.new_button.click()
        self.assertEqual(seen["runs"], 2)
        self.assertEqual(len(self.win.csv_tabs()), 1)

    def test_create_open_edit_and_save(self):
        self.drive_new("3", "4", "mio", delimiter=";", encoding="cp1252")
        self.win.new_csv()
        pump(lambda: self.win.csv_tabs() and self.win.csv_tabs()[0].table.rowCount() > 0, 10)
        settle(self.win)
        tab = self.win.csv_tabs()[0]
        path = self.p("mio.csv")
        self.assertEqual(tab.path, path)
        self.assertEqual(tab.columns, ["Colonna1", "Colonna2", "Colonna3"])
        self.assertEqual(tab.total_rows, 4)
        self.assertEqual(open(path, encoding="cp1252").read().splitlines()[:2], ["Colonna1;Colonna2;Colonna3", ";;"])
        self.assertEqual(tab.table.item(0, 0).text(), "")
        self.assertFalse(tab.has_unsaved())
        self.edit(tab, 1, 1, "ciao")
        self.drive_save(accept=True)
        tab.save(); settle(self.win)
        self.assertEqual(read_rows(path, ";", "cp1252")[2], ["", "ciao", ""])

    def test_header_only_file_opens_and_accepts_new_rows(self):
        self.drive_new("2", "0", "solo_intestazione")
        self.win.new_csv()
        pump(lambda: self.win.csv_tabs() and self.win.csv_tabs()[0].columns, 10)
        settle(self.win)
        tab = self.win.csv_tabs()[0]
        self.assertEqual((tab.total_rows, tab.columns), (0, ["Colonna1", "Colonna2"]))
        tab.add_row_prompt(); settle(self.win)
        self.assertEqual(tab.total_rows, 1)

    def test_invalid_values_show_clear_messages_and_nothing_is_created(self):
        for cols, rows, text in [("abc", "3", "numero intero"), ("", "3", "Indica"), ("0", "3", "almeno 1"),
                                 ("3", "-2", "almeno 0"), ("3", "99999999999", "non può superare"), ("9999", "3", "non può superare")]:
            seen = self.drive_new(cols, rows, "x")
            self.win.new_csv(); settle(self.win)
            self.assertIn(text, seen["error"], (cols, rows))
        self.assertFalse(os.path.exists(self.p("x.csv")))
        self.assertEqual(self.win.csv_tabs(), [])

    def test_existing_file_asks_before_overwrite(self):
        path = self.p("esiste.csv")
        open(path, "w").write("vecchio\n")
        self.answer = A.QMessageBox.No
        seen = self.drive_new("2", "2", "esiste", accept=True)
        # «No» riporta alla finestra: la finestra finta accetta di nuovo, quindi si interrompe dopo poche volte
        runs = {"n": 0}
        from csv_reader.desktop.tools_dialogs import NewCsvDialog
        inner = NewCsvDialog.exec
        def once(d):
            runs["n"] += 1
            return inner(d) if runs["n"] == 1 else A.QDialog.Rejected
        NewCsvDialog.exec = once
        self.addCleanup(setattr, NewCsvDialog, "exec", inner)
        self.win.new_csv(); settle(self.win)
        self.assertEqual(open(path).read(), "vecchio\n")
        self.assertEqual(self.win.csv_tabs(), [])
        self.assertTrue(any(m[0] == "question" for m in self.messages))
        NewCsvDialog.exec = inner
        self.answer = A.QMessageBox.Yes
        self.win.new_csv()
        pump(lambda: self.win.csv_tabs(), 10); settle(self.win)
        self.assertTrue(open(path).read().startswith("Colonna1,Colonna2"))

    def test_permission_error_is_reported(self):
        from csv_reader.core import newcsv
        orig = newcsv.os.replace
        def boom(*a, **k):
            raise PermissionError("no")
        newcsv.os.replace = boom
        crit = []
        self.drive_new("2", "2", "perm")
        try:
            self.win.new_csv()
            pump(lambda: any(m[0] == "critical" for m in self.messages), 10)
        finally:
            newcsv.os.replace = orig
        self.assertTrue(any(m[0] == "critical" and "Impossibile scrivere" in m[1][1] for m in self.messages))
        self.assertFalse(os.path.exists(self.p("perm.csv")))
        self.assertEqual([f for f in os.listdir(self.dir) if f.endswith(".tmp")], [])


if __name__ == "__main__":
    unittest.main()
