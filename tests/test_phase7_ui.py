"""v0.18.0 - finestra «Dettaglio record»: larghezza colonne e selezione sincronizzata (offscreen).
Esecuzione:  QT_QPA_PLATFORM=offscreen python -m unittest tests.test_phase7_ui -v"""
import unittest

from PySide6.QtCore import QItemSelectionModel
from PySide6.QtWidgets import QDialog

from csv_reader.desktop import app as A
from tests.test_phase6_ui import P6
from tests.test_ui import APP, pump, settle


class P7(P6):
    LONG = "parola " * 90

    def tab_with(self, rows=30):
        text = "id,nome,nota\n" + "".join(f"{i},n{i},{self.LONG if i == 3 else 'ok'}\n" for i in range(rows))
        return self.open(self.csv(text))

    def open_detail(self, tab, then):
        """Apre il dettaglio con exec() sostituito: esegue ``then(dialog)`` e chiude."""
        seen = {}
        orig = A.RecordDialog.exec

        def fake(d):
            seen["d"] = d
            then(d)
            return QDialog.Accepted
        A.RecordDialog.exec = fake
        try:
            tab.show_selected_record()
            settle(self.win)
        finally:
            A.RecordDialog.exec = orig
        return seen.get("d")

    def select_rows(self, tab, rows, current=(0, 0)):
        tab.table.clearSelection()
        for r in rows:
            tab.table.selectRow(r) if r == rows[0] else tab.table.selectionModel().select(
                tab.table.model().index(r, 0), QItemSelectionModel.Select | QItemSelectionModel.Rows)
        tab.table.selectionModel().setCurrentIndex(tab.table.model().index(*current), QItemSelectionModel.NoUpdate)

    def select_cells(self, tab, cells, current=None):
        tab.table.clearSelection()
        m = tab.table.model()
        for r, c in cells:
            tab.table.selectionModel().select(m.index(r, c), QItemSelectionModel.Select)
        cur = current or cells[0]
        tab.table.selectionModel().setCurrentIndex(m.index(*cur), QItemSelectionModel.NoUpdate)

    def widths(self, d):
        return [d.table.columnWidth(c) for c in range(d.table.columnCount())]


class TestDetailWidths(P7):
    def test_standard_uniform_width_and_menu_actions(self):
        tab = self.tab_with()
        self.select_rows(tab, [1, 3, 5])
        seen = {}

        def check(d):
            seen["w"] = self.widths(d)
            seen["has"] = hasattr(d.columns_button, "fit_button") and hasattr(d.columns_button, "reset_button")
        self.open_detail(tab, check)
        self.assertEqual(set(seen["w"]), {A.RecordDialog.STANDARD_WIDTH})
        self.assertTrue(seen["has"])

    def test_fit_has_a_maximum_manual_resize_is_free_and_reset_restores(self):
        tab = self.tab_with()
        self.select_rows(tab, [1, 3, 5])
        out = {}

        def run(d):
            d.columns_button.fit_button.click()
            out["fit"] = self.widths(d)
            d.table.setColumnWidth(0, 333)                   # manuale
            out["manual"] = d.table.columnWidth(0)
            d.fit_widths()                                   # ripetibile dopo una modifica manuale
            out["refit"] = self.widths(d)
            d.columns_button.reset_button.click()
            out["reset"] = self.widths(d)
        self.open_detail(tab, run)
        self.assertEqual(out["fit"][1], A.CsvTab.ADAPT_MAX_WIDTH)          # record 3: valore lunghissimo -> massimo
        self.assertLessEqual(max(out["fit"]), A.CsvTab.ADAPT_MAX_WIDTH)
        self.assertEqual(out["manual"], 333)
        self.assertEqual(out["refit"], out["fit"])
        self.assertEqual(set(out["reset"]), {A.RecordDialog.STANDARD_WIDTH})

    def test_row_heights_follow_the_width_with_wrapped_text(self):
        tab = self.tab_with()
        self.select_rows(tab, [3, 4])
        out = {}

        def run(d):
            row = d.columns.index("nota") if hasattr(d, "columns") else 2
            out["narrow"] = d.table.rowHeight(row)
            d.table.setColumnWidth(0, 700)
            pump(lambda: d.table.rowHeight(row) != out["narrow"], timeout=3)
            out["wide"] = d.table.rowHeight(row)
        self.open_detail(tab, run)
        self.assertLess(out["wide"], out["narrow"])

    def test_main_table_widths_are_untouched(self):
        tab = self.tab_with()
        before = [tab.table.columnWidth(c) for c in range(3)]
        self.select_rows(tab, [1, 2])
        self.open_detail(tab, lambda d: d.fit_widths())
        self.assertEqual([tab.table.columnWidth(c) for c in range(3)], before)


class TestDetailWindow(P7):
    def test_standard_width_is_double_of_main_and_window_is_wider(self):
        self.assertEqual(A.RecordDialog.STANDARD_WIDTH, 2 * A.CsvTab.STANDARD_WIDTH)
        tab = self.tab_with()
        self.select_rows(tab, [1])
        out = {}
        self.open_detail(tab, lambda d: out.update(w=d.width(), min=A.RecordDialog.MIN_WIDTH))
        self.assertGreaterEqual(out["w"], out["min"])
        self.assertGreater(out["min"], 480)                      # prima 480


class TestGridLines(P7):
    def test_grid_lines_visible_in_main_table_and_detail(self):
        tab = self.tab_with()
        self.select_rows(tab, [1])
        out = {}
        self.open_detail(tab, lambda d: out.update(detail=d.table.showGrid()))
        self.assertTrue(tab.table.showGrid())
        self.assertTrue(out["detail"])


class TestDetailSpace(P7):
    def press(self, widget, key, text=" "):
        from PySide6.QtCore import QEvent, Qt
        from PySide6.QtGui import QKeyEvent
        ev = QKeyEvent(QEvent.KeyPress, key, Qt.NoModifier, text)
        return A.QApplication.instance().notify(widget, ev)

    def test_space_is_swallowed_by_the_filter_only_outside_editing(self):
        from PySide6.QtCore import QEvent, Qt
        from PySide6.QtGui import QKeyEvent
        tab = self.tab_with()
        self.select_cells(tab, [(2, 1)])
        out = {}

        def run(d):
            out["focusable_button"] = d.columns_button.focusPolicy() != Qt.NoFocus
            d.activeWindow = None
            orig = A.QApplication.activeWindow
            A.QApplication.activeWindow = staticmethod(lambda: d)         # offscreen: nessuna finestra è «attiva»
            try:
                ev = QKeyEvent(QEvent.KeyPress, Qt.Key_Space, Qt.NoModifier, " ")
                out["swallowed"] = d.eventFilter(d.table, ev)
                out["text_same"] = d.table.item(1, 0).text()
                out["editing_before"] = d.table.state()
                d.table.editItem(d.table.item(1, 0))                      # editor aperto: lo spazio passa
                out["swallowed_editing"] = d.eventFilter(d.table, QKeyEvent(QEvent.KeyPress, Qt.Key_Space, Qt.NoModifier, " "))
                d.table.commit_open_editor()
                out["ctrl_space"] = d.eventFilter(d.table, QKeyEvent(QEvent.KeyPress, Qt.Key_Space, Qt.ControlModifier, " "))
            finally:
                A.QApplication.activeWindow = orig
        self.open_detail(tab, run)
        self.assertFalse(out["focusable_button"])           # il pulsante «Colonne» non prende il focus: niente menu
        self.assertTrue(out["swallowed"])
        self.assertFalse(out["swallowed_editing"])
        self.assertFalse(out["ctrl_space"])


class TestDetailSelection(P7):
    def selected_records(self, d):
        return sorted({i.column() for i in d.table.selectedIndexes()})

    def selected_cells(self, d):
        return sorted((i.row(), i.column()) for i in d.table.selectedIndexes())

    def main_cells(self, tab):
        return sorted((i.row(), i.column()) for i in tab.table.selectedIndexes())

    def test_single_cell_is_the_only_one_selected(self):
        tab = self.tab_with()
        self.select_cells(tab, [(4, 2)])
        out = {}
        self.open_detail(tab, lambda d: out.update(sel=self.selected_cells(d), cur=(d.table.currentIndex().row(),
                                                                                   d.table.currentIndex().column())))
        self.assertEqual(out["sel"], [(2, 0)])                   # campo «nota», unico record mostrato
        self.assertEqual(out["cur"], (2, 0))

    def test_multiple_and_non_contiguous_cells(self):
        tab = self.tab_with()
        cells = [(2, 0), (2, 2), (7, 1), (9, 2)]
        self.select_cells(tab, cells, current=(7, 1))
        out = {}

        def run(d):
            out["sel"] = self.selected_cells(d)
            out["cur"] = (d.table.currentIndex().row(), d.table.currentIndex().column())
        self.open_detail(tab, run)
        # record mostrati: righe 2, 7, 9 -> colonne 0, 1, 2; campo = colonna principale
        self.assertEqual(out["sel"], sorted([(0, 0), (2, 0), (1, 1), (2, 2)]))
        self.assertEqual(out["cur"], (1, 1))

    def test_selection_after_filter_and_sort(self):
        tab = self.tab_with(rows=12)
        tab.sort = ("id", True)                                      # ordine decrescente per id
        tab.column_filters["id"] = {"op": "gt", "value": "3"}      # id > 3
        tab.filters_changed()
        settle(self.win)
        ids = [tab.table.item(r, 0).text() for r in range(tab.table.rowCount())]
        self.assertEqual(ids, sorted(ids, reverse=True))             # ordinamento (testuale: tutto è testo) e filtro applicati
        self.assertNotEqual(ids, sorted(ids, key=int))
        self.assertLess(len(ids), 12)
        self.select_cells(tab, [(1, 1), (3, 2)], current=(3, 2))
        want = {(tab.table.item(r, 0).text(), tab.columns[c]) for r, c in self.main_cells(tab)}
        out = {}

        def run(d):
            got = set()
            for i in d.table.selectedIndexes():
                rid = d.table.horizontalHeaderItem(i.column()).text()          # «Riga N»: N = id + 1
                got.add((str(int(rid.split()[1]) - 1), d.tab.columns[i.row()]))
            out["got"] = got
        self.open_detail(tab, run)
        self.assertEqual(out["got"], want)
        self.assertEqual(len(want), 2)

    def test_selected_cell_is_scrolled_into_view(self):
        tab = self.tab_with()
        self.select_cells(tab, [(5, 2)])
        out = {}

        def run(d):
            d.resize(d.width(), 120)
            settle(self.win)
            d.table.scrollToTop()
            d._select_records((5, 2))
            idx = d.table.currentIndex()
            out["visible"] = d.table.viewport().rect().intersects(d.table.visualRect(idx))
        self.open_detail(tab, run)
        self.assertTrue(out["visible"])

    def test_opening_does_not_change_the_main_selection_and_closing_keeps_it(self):
        tab = self.tab_with()
        self.select_cells(tab, [(2, 0), (7, 1), (9, 2)], current=(7, 1))
        before = self.main_cells(tab)
        self.open_detail(tab, lambda d: None)
        self.assertEqual(self.main_cells(tab), before)
        self.assertEqual((tab.table.currentRow(), tab.table.currentColumn()), (7, 1))
        self.assertFalse(tab.has_unsaved())

    def test_selection_made_in_the_detail_reaches_the_same_cells_in_the_main_table(self):
        tab = self.tab_with()
        self.select_cells(tab, [(2, 0), (7, 1), (9, 2)], current=(7, 1))

        def run(d):
            d.table.clearSelection()
            d.table.selectionModel().select(d.table.model().index(1, 2), QItemSelectionModel.Select)   # nome, «Riga 10»
        self.open_detail(tab, run)
        self.assertEqual(self.main_cells(tab), [(9, 1)])

    def test_cells_beyond_the_detail_limit_are_not_lost(self):
        tab = self.tab_with(rows=60)
        A.CsvTab.MAX_DETAIL_RECORDS, orig = 3, A.CsvTab.MAX_DETAIL_RECORDS
        self.addCleanup(setattr, A.CsvTab, "MAX_DETAIL_RECORDS", orig)
        self.select_cells(tab, [(0, 0), (1, 1), (2, 2), (3, 0), (4, 1)], current=(0, 0))
        orig_info = A.QMessageBox.information
        A.QMessageBox.information = staticmethod(lambda *a, **k: None)
        self.addCleanup(setattr, A.QMessageBox, "information", orig_info)

        def run(d):
            d.table.clearSelection()
            d.table.selectionModel().select(d.table.model().index(1, 1), QItemSelectionModel.Select)
        self.open_detail(tab, run)
        self.assertEqual(self.main_cells(tab), [(1, 1), (3, 0), (4, 1)])

    def test_edit_in_the_detail_still_writes_to_the_dataset(self):
        tab = self.tab_with()
        self.select_cells(tab, [(2, 1), (5, 1)], current=(2, 1))
        self.open_detail(tab, lambda d: d.table.item(1, 1).setText("MOD"))
        settle(self.win)
        self.assertEqual(tab.engine.cell_text(5, "nome"), "MOD")
        self.assertTrue(tab.has_unsaved())


if __name__ == "__main__":
    unittest.main()
