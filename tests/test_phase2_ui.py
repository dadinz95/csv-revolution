"""Test funzionali della Fase 2 sull'interfaccia (modalità offscreen): filtri avanzati, formattazione condizionale,
trova/sostituisci, aggiunta/rinomina/eliminazione di colonne e righe, coerenza tra le funzioni.

Come in test_ui, gli eventi del mouse sono generati da QTest sugli stessi widget; i dialoghi modali sono pilotati
dal test (impostano i campi e premono «Applica»); non sostituiscono una prova a mano su Windows/macOS.
Esecuzione:  QT_QPA_PLATFORM=offscreen python -m unittest tests.test_phase2_ui -v"""
import csv
import os
import unittest

from PySide6.QtCore import QItemSelection, QItemSelectionModel, QPoint, Qt
from PySide6.QtGui import QColor
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QDialog, QFileDialog, QMessageBox

from csv_reader.desktop import app as A
from csv_reader.desktop import tools_dialogs as TD
from tests.test_ui import APP, UiCase, pump, settle

from tests import legacy_types


def setUpModule():
    legacy_types.install()


def tearDownModule():
    legacy_types.uninstall()



def read_csv(path):
    with open(path, encoding="utf-8", newline="") as fh:
        return list(csv.reader(fh))


class P2Ui(UiCase):
    def setUp(self):
        super().setUp()
        self.boxes = []                                  # (testo, testo informativo) dei QMessageBox(...).exec()
        self.box_answer = QMessageBox.Yes
        self._orig_box_exec = A.QMessageBox.exec
        A.QMessageBox.exec = lambda b: self.boxes.append((b.text(), b.informativeText())) or (
            self.box_answer(b) if callable(self.box_answer) else self.box_answer)
        self._orig_filter_exec = A.FilterDialog.exec
        self._orig_name_exec = A.NameDialog.exec
        self._orig_cf_exec = A.CfDialog.exec
        self._orig_menu_exec = A.QMenu.exec

    def tearDown(self):
        A.QMessageBox.exec = self._orig_box_exec
        A.FilterDialog.exec = self._orig_filter_exec
        A.NameDialog.exec = self._orig_name_exec
        A.CfDialog.exec = self._orig_cf_exec
        A.QMenu.exec = self._orig_menu_exec
        super().tearDown()

    # ---- pilotaggio dei dialoghi
    def drive_filter(self, tab, column, op=None, value="", case=False, remove=False, values=None):
        seen = {}

        def fake(d):
            seen["default_op"] = d.condition.currentData()
            if op:
                d.condition.setCurrentIndex(d.condition.findData(op))
            d.value_edit.setText(value)
            d.case_box.setChecked(case)
            if values:
                for i in range(d.list.count()):
                    if d._values[i] in values:
                        d.list.item(i).setCheckState(Qt.Checked)
            if remove:
                d._remove()
            else:
                d._apply()
            seen["error"] = d.error.text() if d.error.isVisible() or d.error.text() else ""
            return d.result() == QDialog.Accepted and QDialog.Accepted or QDialog.Rejected

        A.FilterDialog.exec = fake
        tab.filter_by_header_click(tab.columns.index(column))
        settle(self.win)
        return seen

    def drive_name(self, name):
        seen = {"attempts": []}

        def fake(d):
            for candidate in (name if isinstance(name, list) else [name]):
                d.edit.setText(candidate)
                d._accept()
                seen["attempts"].append(d.error.text())
                if d.result_name:
                    return QDialog.Accepted
            return QDialog.Rejected

        A.NameDialog.exec = fake
        return seen

    def drive_cf(self, rules_to_add=None, disable=None, delete=None):
        def fake(d):
            for rule in rules_to_add or []:
                d.rules.append(rule)
            for i in (disable or []):
                d.rules[i]["enabled"] = False
            for i in sorted(delete or [], reverse=True):
                del d.rules[i]
            d._apply()
            return QDialog.Accepted

        A.CfDialog.exec = fake

    def rule(self, column, op, value, color, **kw):
        return {"id": f"{column}{op}{value}", "enabled": True, "column": column, "op": op, "value": value, "case": False,
                "color": color, **kw}

    def click_header(self, tab, col, mod=Qt.NoModifier):
        h = tab.table.horizontalHeader()
        QTest.mouseClick(h.viewport(), Qt.LeftButton, mod, QPoint(h.sectionViewportPosition(col) + h.sectionSize(col) // 3, h.height() // 2))
        APP.processEvents()

    def click_row_header(self, tab, row, mod=Qt.NoModifier):
        v = tab.table.verticalHeader()
        QTest.mouseClick(v.viewport(), Qt.LeftButton, mod, QPoint(v.width() // 2, v.sectionViewportPosition(row) + v.sectionSize(row) // 2))
        APP.processEvents()

    def select_range(self, tab, top, left, bottom, right):
        m = tab.table.model()
        tab.table.sel_kind = None
        tab.table.selectionModel().select(QItemSelection(m.index(top, left), m.index(bottom, right)),
                                          QItemSelectionModel.ClearAndSelect)
        tab.table.selectionModel().setCurrentIndex(m.index(top, left), QItemSelectionModel.NoUpdate)
        APP.processEvents()

    def bg(self, tab, row, col):
        brush = tab.table.item(row, col).background()
        return None if brush.style() == Qt.NoBrush else brush.color().name()

    def texts(self, tab, col):
        return [tab.table.item(r, col).text() for r in range(tab.table.rowCount())]

    def find_setup(self, tab, find, repl="", case=False, exact=False, filtered=False):
        tab.open_find()
        d = tab.find_dialog
        d.find_edit.setText(find)
        d.replace_edit.setText(repl)
        d.case_box.setChecked(case)
        d.exact_box.setChecked(exact)
        d.filtered_box.setChecked(filtered)
        return d


class TestAdvancedFilters(P2Ui):
    def test_default_condition_is_contains_and_dialog_has_all_controls(self):
        tab = self.open(self.make_csv(rows=40, cols=3))
        d = A.FilterDialog(tab, "col1", None, tab.engine.distinct_values("col1")[0], False, None, "VARCHAR")
        self.assertEqual(d.condition.currentData(), "contains")
        self.assertEqual([d.condition.itemData(i) for i in range(d.condition.count())],
                         ["contains", "eq", "ne", "not_contains", "starts", "ends", "gt", "lt", "empty", "not_empty"])
        self.assertTrue(d.value_edit.isEnabled())
        d.condition.setCurrentIndex(d.condition.findData("empty"))
        self.assertFalse(d.value_edit.isEnabled())                       # «Vuoto» non ha bisogno di un valore
        self.assertTrue(d.apply_button.text() == "Applica")

    def test_filter_updates_table_count_sql_dot_and_removal_refreshes_everything(self):
        tab = self.open(self.make_csv(rows=300, cols=3))
        seen = self.drive_filter(tab, "col0", "gt", "250")
        self.assertEqual(seen["default_op"], "contains")
        self.assertEqual(tab.table.rowCount(), 49)
        self.assertEqual(tab._filtered_count, 49)
        self.assertIn("49", tab.status.text())
        self.assertNotIn("●", tab.table.horizontalHeaderItem(0).text())
        self.assertIn(0, tab.table.horizontalHeader().active_columns)
        self.assertIn("> 250", tab.sql_input.toPlainText())
        self.assertTrue(tab.chip_container.isVisible())
        self.assertEqual(tab.chip_row.itemAt(0).widget().text(), "col0: 1 filtro  ✕")
        # filtro mantenuto cambiando ordinamento
        tab.sort = ("col0", True)
        tab.filters_changed(); settle(self.win)
        self.assertEqual(tab.table.item(0, 0).text(), "299")
        self.assertEqual(tab.table.rowCount(), 49)
        # rimozione dalla finestra
        self.drive_filter(tab, "col0", remove=True)
        self.assertEqual(tab.table.rowCount(), 200)
        self.assertEqual(tab._filtered_count, 300)
        self.assertNotIn("col0", tab.column_filters)
        self.assertEqual(tab.sort, ("col0", True))                                  # l'ordinamento resta: l'imbuto resta acceso
        self.assertIn(0, tab.table.horizontalHeader().active_columns)
        self.assertNotIn("WHERE", tab.sql_input.toPlainText())
        self.assertFalse(tab.chip_container.isVisible() and tab.chip_row.count() > 1 and tab.chip_row.itemAt(0).widget().text().startswith("col0"))

    def test_text_conditions_case_option_and_and_between_columns(self):
        tab = self.open(self.make_csv(rows=120, cols=3))
        self.drive_filter(tab, "col1", "starts", "VALORE 1")
        self.assertEqual(tab._filtered_count, 31)                         # «valore 1», «valore 10-19», «valore 100-119»
        self.drive_filter(tab, "col1", "starts", "VALORE 1", case=True)
        self.assertEqual(tab._filtered_count, 0)                          # distinzione maiuscole attiva
        self.drive_filter(tab, "col1", "starts", "valore 1")
        self.drive_filter(tab, "col0", "lt", "20")
        self.assertEqual(tab._filtered_count, 11)                         # AND: col0 < 20 e col1 inizia con «valore 1»
        self.assertEqual(set(tab.column_filters), {"col1", "col0"})
        self.assertIn("2 filtri" if False else "col1: 1 filtro", tab.chip_row.itemAt(0).widget().text() + tab.chip_row.itemAt(1).widget().text())

    def test_invalid_value_for_numeric_column_is_rejected_in_dialog(self):
        tab = self.open(self.make_csv(rows=50, cols=3))
        seen = self.drive_filter(tab, "col0", "gt", "abc")
        self.assertIn("non è un numero", seen["error"])
        self.assertNotIn("col0", tab.column_filters)
        self.assertEqual(tab.table.rowCount(), 50)

    def test_empty_and_notempty_conditions(self):
        path = self.p("e.csv")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("a,b\n1,x\n2,\n3,  \n4,y\n")
        tab = self.open(path)
        self.drive_filter(tab, "b", "empty")
        self.assertEqual(tab._filtered_count, 2)
        self.drive_filter(tab, "b", "not_empty")
        self.assertEqual(tab._filtered_count, 2)

    def test_filters_survive_page_change_and_tab_switch(self):
        a = self.open(self.make_csv("a.csv", 500, 3))
        self.drive_filter(a, "col0", "gt", "99")
        self.assertEqual(a._filtered_count, 400)
        a.next_page(); settle(self.win)
        self.assertEqual(a.offset, 200)
        self.assertEqual(a.table.item(0, 0).text(), "300")
        b = self.open(self.make_csv("b.csv", 30, 3))
        self.win.step_file(-1)
        self.assertIn("col0", a.column_filters)
        self.assertEqual(a._filtered_count, 400)
        self.assertEqual(a.table.item(0, 0).text(), "300")
        self.assertEqual(b.table.rowCount(), 30)                           # l'altra scheda non è toccata

    def test_row_numbers_stay_the_file_record_numbers_after_filter_and_sort(self):
        tab = self.open(self.make_csv(rows=60, cols=3))
        self.drive_filter(tab, "col0", "gt", "54")
        tab.sort = ("col0", True)
        tab.filters_changed(); settle(self.win)
        labels = [tab.table.verticalHeaderItem(r).text() for r in range(tab.table.rowCount())]
        self.assertEqual(labels, ["60", "59", "58", "57", "56"])           # record 60 = riga 60 del file, anche ordinato
        self.assertEqual(tab.table.item(0, 0).text(), "59")                # col0 parte da 0: record 60 ha col0 = 59

    def test_sql_follows_visible_columns_filters_and_sort(self):
        tab = self.open(self.make_csv(rows=40, cols=4))
        tab.hidden_columns.add("col2")
        tab.apply_hidden_columns()
        sql = tab.sql_input.toPlainText()
        self.assertIn('"col0"', sql)
        self.assertNotIn('"col2"', sql)
        self.assertNotIn("SELECT *", sql)
        self.drive_filter(tab, "col1", "contains", "valore 3")
        tab.sort = ("col0", False)
        tab.filters_changed(); settle(self.win)
        sql = tab.sql_input.toPlainText()
        self.assertIn("valore 3", sql)
        self.assertIn('ORDER BY "col0" ASC', sql)
        tab.run_sql_button.click(); settle(self.win)                       # la query generata è eseguibile
        self.assertEqual(tab.sql_result.rowCount(), 11)
        tab.hidden_columns.clear(); tab.apply_hidden_columns()
        sql = tab.sql_input.toPlainText()                                  # tutte visibili: elenco esplicito, stesso ordine della tabella
        self.assertNotIn("SELECT *", sql)
        self.assertTrue(sql.index('"col0"') < sql.index('"col1"') < sql.index('"col2"'))


class TestConditionalFormattingUi(P2Ui):
    def test_rules_colour_only_background_with_precedence_pages_hidden_columns_and_filters(self):
        tab = self.open(self.make_csv(rows=300, cols=4))
        self.drive_cf([self.rule("col0", "gt", "250", "#ff0000"), self.rule("col0", "gt", "100", "#00ff00"),
                       self.rule("col1", "contains", "valore 7-", "#0000ff")])
        tab.open_cf(); settle(self.win)
        self.assertEqual(tab.cf_button.text(), "Formattazione condizionale… (3)")
        self.assertIsNone(self.bg(tab, 50, 0))                              # col0 = 50: nessuna regola
        self.assertEqual(self.bg(tab, 150, 0), "#00ff00")
        self.assertEqual(self.bg(tab, 7, 1), "#0000ff")                     # «valore 7-1»
        self.assertEqual(tab.table.item(150, 0).text(), "150")              # valori invariati
        self.assertEqual(tab.table.item(150, 0).font().bold(), False)       # il carattere non cambia
        tab.next_page(); settle(self.win)                                   # pagina 2: righe 200–299
        self.assertEqual(self.bg(tab, 0, 0), "#00ff00")                     # 200
        self.assertEqual(self.bg(tab, 60, 0), "#ff0000")                    # 260: la prima regola vince sulla seconda
        # colonna nascosta: i colori restano calcolati sulle righe
        tab.hidden_columns.add("col0"); tab.apply_hidden_columns()
        self.assertTrue(tab.table.isColumnHidden(0))
        self.assertEqual(self.bg(tab, 60, 0), "#ff0000")
        # con filtri e ordinamento
        tab.hidden_columns.clear(); tab.apply_hidden_columns()
        self.drive_filter(tab, "col0", "gt", "270")
        tab.sort = ("col0", True)
        tab.filters_changed(); settle(self.win)
        self.assertEqual(tab.table.item(0, 0).text(), "299")
        self.assertTrue(all(self.bg(tab, r, 0) == "#ff0000" for r in range(tab.table.rowCount())))

    def test_disable_delete_and_no_interference_with_data_search_sql_save(self):
        tab = self.open(self.make_csv(rows=60, cols=3))
        self.drive_cf([self.rule("col0", "lt", "10", "#ff8800"), self.rule("col1", "empty", "", "#00ffff")])
        tab.open_cf(); settle(self.win)
        self.assertEqual(self.bg(tab, 3, 0), "#ff8800")
        # disattivata: nessun colore ma la regola resta nell'elenco
        self.drive_cf(disable=[0])
        tab.open_cf(); settle(self.win)
        self.assertIsNone(self.bg(tab, 3, 0))
        self.assertEqual(len(tab.cf_rules), 2)
        self.assertEqual(tab.cf_button.text(), "Formattazione condizionale… (1)")
        # eliminata
        self.drive_cf(delete=[0])
        tab.open_cf(); settle(self.win)
        self.assertEqual([r["column"] for r in tab.cf_rules], ["col1"])
        # le regole non toccano dati, ricerca, SQL e file salvato
        self.drive_cf([self.rule("col0", "lt", "10", "#ff8800")])
        tab.open_cf(); settle(self.win)
        tab.search_input.setText("valore 5-"); tab.reset_and_load(); settle(self.win)
        self.assertEqual(tab._filtered_count, 1)                            # solo il record 5
        self.assertEqual(self.bg(tab, 0, 0), "#ff8800")                     # il record 5 è colorato anche dopo la ricerca
        self.assertFalse(tab.engine.has_changes())                          # nessuna modifica ai dati
        tab.sql_input.setPlainText("SELECT COUNT(*) FROM csv_data"); tab.run_sql_button.click(); settle(self.win)
        self.assertEqual(tab.sql_result.item(0, 0).text(), "60")
        copy = self.p("o.csv")
        tab.engine.save_csv(copy)
        self.assertEqual(read_csv(copy)[1], ["0", "valore 0-1", "valore 0-2"])

    def test_background_is_really_painted_despite_the_stylesheet(self):
        tab = self.open(self.make_csv(rows=20, cols=3))
        self.drive_cf([self.rule("col0", "lt", "5", "#ff8800")])
        tab.open_cf(); settle(self.win)
        tab.table.clearSelection()
        tab.table.setCurrentCell(-1, -1)
        tab.show(); APP.processEvents()
        rect = tab.table.visualRect(tab.table.model().index(2, 0))
        image = tab.table.viewport().grab().toImage()
        self.assertEqual(image.pixelColor(rect.left() + 4, rect.center().y()).name(), "#ff8800")
        rect = tab.table.visualRect(tab.table.model().index(8, 0))
        self.assertNotEqual(image.pixelColor(rect.left() + 4, rect.center().y()).name(), "#ff8800")

    def test_rule_dialog_validates_values_for_the_column_type(self):
        tab = self.open(self.make_csv(rows=20, cols=3))
        d = TD.CfRuleDialog(tab, list(tab.columns), dict(tab.engine.dtypes))
        d.column.setCurrentIndex(0)
        d.condition.setCurrentIndex(d.condition.findData("gt"))
        d.value.setText("abc")
        d._save()
        self.assertIsNone(d.result_rule)
        self.assertIn("non è un numero", d.error.text())
        d.value.setText("5")
        d._save()
        self.assertEqual(d.result_rule["op"], "gt")
        self.assertTrue(d.result_rule["color"].startswith("#"))

    def test_rules_follow_rename_and_are_removed_on_column_delete(self):
        tab = self.open(self.make_csv(rows=30, cols=4))
        self.drive_cf([self.rule("col0", "lt", "5", "#ff8800"), self.rule("col2", "contains", "3", "#00ffff")])
        tab.open_cf(); settle(self.win)
        self.drive_name("ID")
        tab.rename_column_prompt("col0"); settle(self.win)
        self.assertEqual([r["column"] for r in tab.cf_rules], ["ID", "col2"])
        self.assertEqual(self.bg(tab, 2, 0), "#ff8800")
        tab.delete_column_prompt("col2"); settle(self.win)
        self.assertEqual([r["column"] for r in tab.cf_rules], ["ID"])
        self.assertEqual(tab.cf_button.text(), "Formattazione condizionale… (1)")
        self.assertEqual(self.bg(tab, 2, 0), "#ff8800")


class TestFindReplaceUi(P2Ui):
    def test_find_next_changes_page_selects_and_scrolls_to_cell(self):
        tab = self.open(self.make_csv(rows=450, cols=3))
        d = self.find_setup(tab, "valore 333-1")
        tab.find_next(); settle(self.win)
        self.assertEqual(tab.offset, 200)                                   # record 334 = pagina 2
        self.assertEqual((tab.table.currentRow(), tab.table.currentColumn()), (133, 1))
        self.assertEqual(tab.table.item(133, 1).text(), "valore 333-1")
        self.assertTrue(tab.table.item(133, 1).isSelected())
        self.assertIn("Trovato", d.status.text())
        # una seconda ricerca senza altre occorrenze ricomincia dall'inizio e ritrova la stessa cella
        tab.find_next(); settle(self.win)
        self.assertIn("Ricominciato", d.status.text())
        # nessun risultato
        d.find_edit.setText("non-esiste")
        tab.find_next(); settle(self.win)
        self.assertIn("Nessuna occorrenza", d.status.text())

    def test_find_next_walks_matches_in_order_across_columns(self):
        tab = self.open(self.make_csv(rows=30, cols=3))
        self.find_setup(tab, "valore 2")
        seen = []
        for _ in range(4):
            tab.find_next(); settle(self.win)
            seen.append((tab.table.currentRow(), tab.table.currentColumn()))
        self.assertEqual(seen, [(2, 1), (2, 2), (20, 1), (20, 2)])        # riga 2 e riga 20, colonne in ordine

    def test_replace_one_then_continue_and_dataset_is_updated(self):
        tab = self.open(self.make_csv(rows=30, cols=3))
        d = self.find_setup(tab, "valore 5-1", "CINQUE")
        tab.find_next(); settle(self.win)
        self.assertEqual(tab.table.currentRow(), 5)
        tab.replace_current(); settle(self.win)
        self.assertEqual(tab.engine.cell_text(5, "col1"), "CINQUE")        # scritto nel dataset, non solo mostrato
        self.assertEqual(tab.table.item(5, 1).text(), "CINQUE")
        self.assertEqual(tab.engine.cell_text(4, "col1"), "valore 4-1")
        self.assertTrue(tab.has_unsaved())
        self.assertIn("Sostituito", d.status.text())
        # il dataset modificato è visibile a filtri e SQL
        self.drive_filter(tab, "col1", "eq", "cinque")
        self.assertEqual(tab._filtered_count, 1)

    def test_replace_all_asks_confirmation_and_reports_count(self):
        tab = self.open(self.make_csv(rows=40, cols=3))
        d = self.find_setup(tab, "valore", "v")
        self.box_answer = QMessageBox.No
        tab.replace_all(); settle(self.win)
        self.assertEqual(tab.engine.cell_text(0, "col1"), "valore 0-1")   # rifiutato: nulla è cambiato
        self.assertFalse(tab.has_unsaved())
        self.assertIn("annullata", d.status.text())
        self.box_answer = QMessageBox.Yes
        tab.replace_all(); settle(self.win)
        self.assertIn("80 celle", self.boxes[-1][0])                        # 40 righe × 2 colonne di testo
        self.assertIn("80 celle modificate", d.status.text())
        self.assertTrue(any("Sostituzione completata" in str(m) or "80 celle modificate" in str(m) for m in self.messages))
        self.assertEqual(tab.table.item(0, 1).text(), "v 0-1")
        self.assertEqual(tab.engine.cell_text(39, "col2"), "v 39-2")
        self.assertEqual(tab.engine.cell_text(39, "col0"), "39")            # colonna numerica senza «valore»: intatta
        self.assertIn("(80)", tab.save_button.text())

    def test_scope_selected_columns_includes_other_pages_and_ignores_other_columns(self):
        tab = self.open(self.make_csv(rows=450, cols=3))
        self.click_header(tab, 1)
        d = self.find_setup(tab, "valore", "X")
        self.assertIn("1 colonne selezionate", d.scope_label.text())
        self.assertIn("anche fuori pagina", d.scope_label.text())
        tab.replace_all(); settle(self.win)
        self.assertEqual(tab.engine.cell_text(0, "col1"), "X 0-1")
        self.assertEqual(tab.engine.cell_text(449, "col1"), "X 449-1")       # pagina non visualizzata
        self.assertEqual(tab.engine.cell_text(0, "col2"), "valore 0-2")      # altra colonna intatta

    def test_scope_selected_rows_includes_hidden_columns(self):
        tab = self.open(self.make_csv(rows=30, cols=4))
        tab.hidden_columns.add("col2"); tab.apply_hidden_columns()
        self.click_row_header(tab, 3)
        self.click_row_header(tab, 5, Qt.ControlModifier)
        d = self.find_setup(tab, "valore", "R")
        self.assertIn("2 righe selezionate", d.scope_label.text())
        self.assertIn("nascoste", d.scope_label.text())
        tab.replace_all(); settle(self.win)
        self.assertEqual(tab.engine.cell_text(3, "col2"), "R 3-2")            # la colonna nascosta è compresa
        self.assertEqual(tab.engine.cell_text(5, "col3"), "R 5-3")
        self.assertEqual(tab.engine.cell_text(4, "col2"), "valore 4-2")       # riga non selezionata intatta

    def test_scope_range_and_cells_only_touch_selection(self):
        tab = self.open(self.make_csv(rows=30, cols=4))
        self.select_range(tab, 2, 1, 4, 2)                                   # righe 2–4, colonne col1–col2
        d = self.find_setup(tab, "valore", "Q")
        self.assertIn("intervallo selezionato (6 celle)", d.scope_label.text())
        tab.replace_all(); settle(self.win)
        self.assertEqual(tab.engine.cell_text(2, "col1"), "Q 2-1")
        self.assertEqual(tab.engine.cell_text(4, "col2"), "Q 4-2")
        self.assertEqual(tab.engine.cell_text(4, "col3"), "valore 4-3")       # fuori dall'intervallo
        self.assertEqual(tab.engine.cell_text(5, "col1"), "valore 5-1")
        self.assertEqual(tab.engine.cell_text(1, "col1"), "valore 1-1")

    def test_find_next_stays_inside_selection_even_after_it_moves(self):
        tab = self.open(self.make_csv(rows=30, cols=3))
        self.select_range(tab, 4, 1, 6, 1)                                   # solo col1 delle righe 4–6
        self.find_setup(tab, "valore")
        hits = []
        for _ in range(5):
            tab.find_next(); settle(self.win)
            hits.append((tab.table.currentRow(), tab.table.currentColumn()))
        self.assertEqual(hits, [(5, 1), (6, 1), (4, 1), (5, 1), (6, 1)])     # parte dopo la cella corrente, resta nell'ambito congelato e ricomincia

    def test_precedence_columns_over_rows_over_cells(self):
        tab = self.open(self.make_csv(rows=30, cols=4))
        self.click_row_header(tab, 2)
        self.click_header(tab, 1, Qt.ControlModifier)                        # colonna intera + una riga intera
        d = self.find_setup(tab, "valore", "P")
        self.assertIn("colonne selezionate", d.scope_label.text())
        self.assertIn("vale solo", d.scope_label.text())                     # lo spiega all'utente
        self.assertIn("Precedenza", d.scope_label.text())

    def test_only_filtered_records_option(self):
        tab = self.open(self.make_csv(rows=60, cols=3))
        self.drive_filter(tab, "col0", "gt", "49")
        d = self.find_setup(tab, "valore", "F", filtered=True)
        self.assertIn("Limitato ai record", d.scope_label.text())
        tab.replace_all(); settle(self.win)
        self.assertEqual(tab.engine.cell_text(55, "col1"), "F 55-1")
        self.assertEqual(tab.engine.cell_text(10, "col1"), "valore 10-1")     # fuori filtro: intatto
        # senza l'opzione: intero dataset
        d.filtered_box.setChecked(False)
        d.find_edit.setText("valore")
        tab.replace_all(); settle(self.win)
        self.assertEqual(tab.engine.cell_text(10, "col1"), "F 10-1")

    def test_special_characters_case_exact_and_empty_cells(self):
        path = self.p("s.csv")
        with open(path, "w", encoding="utf-8", newline="") as fh:
            fh.write('t,u\n"a.b (x) 100%",""\nAXB,Mario\nmario,\n')
        tab = self.open(path)
        self.find_setup(tab, ".", "-")
        tab.replace_all(); settle(self.win)
        self.assertEqual(tab.engine.cell_text(0, "t"), "a-b (x) 100%")        # il punto non è un jolly
        self.assertEqual(tab.engine.cell_text(1, "t"), "AXB")
        d = self.find_setup(tab, "(x)", "[y]")
        tab.replace_all(); settle(self.win)
        self.assertEqual(tab.engine.cell_text(0, "t"), "a-b [y] 100%")
        d.find_edit.setText("MARIO"); d.replace_edit.setText("Z"); d.case_box.setChecked(True)
        tab.replace_all(); settle(self.win)
        self.assertEqual(tab.engine.cell_text(1, "u"), "Mario")                 # maiuscole/minuscole rispettate
        d.case_box.setChecked(False); d.exact_box.setChecked(True)
        tab.replace_all(); settle(self.win)
        self.assertEqual(tab.engine.cell_text(1, "u"), "Z")                     # corrispondenza esatta (cella intera)
        self.assertEqual(tab.engine.cell_text(2, "t"), "Z")
        d.find_edit.setText(""); d.replace_edit.setText("vuoto")
        tab.replace_all(); settle(self.win)                                     # «Trova» vuoto + esatta = celle vuote
        self.assertEqual(tab.engine.cell_text(0, "u"), "vuoto")
        self.assertEqual(tab.engine.cell_text(2, "u"), "vuoto")
        d.exact_box.setChecked(False)
        tab.replace_all(); settle(self.win)
        self.assertIn("Scrivi il testo", d.status.text())                       # vuoto senza «esatta»: rifiutato

    def test_replace_keeps_identity_with_sort_and_filter(self):
        tab = self.open(self.make_csv(rows=40, cols=3))
        self.drive_filter(tab, "col0", "gt", "30")
        tab.sort = ("col0", True)
        tab.filters_changed(); settle(self.win)
        self.assertEqual(tab.table.item(0, 0).text(), "39")
        d = self.find_setup(tab, "valore 39-1", "ULTIMO")
        tab.find_next(); settle(self.win)
        self.assertEqual(tab.table.currentRow(), 0)
        tab.replace_current(); settle(self.win)
        self.assertEqual(tab.engine.cell_text(39, "col1"), "ULTIMO")
        self.assertEqual(tab.engine.cell_text(38, "col1"), "valore 38-1")


class TestStructureUi(P2Ui):
    def test_add_column_rejects_empty_and_duplicates_then_is_available_everywhere(self):
        tab = self.open(self.make_csv(rows=40, cols=3))
        seen = self.drive_name(["   ", "COL1", "Note"])
        tab.add_column_prompt(); settle(self.win)
        self.assertIn("vuoto", seen["attempts"][0])
        self.assertIn("Esiste già", seen["attempts"][1])
        self.assertEqual(tab.columns[-1], "Note")
        self.assertEqual(tab.table.columnCount(), 4)
        self.assertEqual(tab.table.rowCount(), 40)                               # il numero di record non cambia
        self.assertEqual(self.texts(tab, 3), [""] * 40)
        self.assertEqual(tab.total_rows, 40)
        # modificabile, filtrabile, ordinabile, ricercabile, colorabile
        tab.table.item(7, 3).setText("urgente"); settle(self.win)
        self.drive_filter(tab, "Note", "eq", "URGENTE")
        self.assertEqual(tab.table.rowCount(), 1)
        self.drive_filter(tab, "Note", "empty")
        self.assertEqual(tab.table.rowCount(), 39)
        self.drive_filter(tab, "Note", remove=True)
        tab.sort = ("Note", True)
        tab.filters_changed(); settle(self.win)
        self.assertEqual(tab.table.item(0, 3).text(), "urgente")
        self.drive_cf([self.rule("Note", "not_empty", "", "#ff0000")])
        tab.open_cf(); settle(self.win)
        self.assertEqual(self.bg(tab, 0, 3), "#ff0000")
        d = self.find_setup(tab, "urgente", "ok")
        tab.replace_all(); settle(self.win)
        self.assertEqual(tab.engine.cell_text(7, "Note"), "ok")
        self.assertIn("40", tab.file_meta.text())                                # meta aggiornati: 4 colonne
        self.assertIn("4 colonne", tab.file_meta.text())

    def test_rename_column_rejects_duplicates_and_updates_filters_sort_sql_and_warns_custom_sql(self):
        tab = self.open(self.make_csv(rows=40, cols=3))
        self.drive_filter(tab, "col1", "contains", "valore 3")
        tab.sort = ("col1", False)
        tab.filters_changed(); settle(self.win)
        before = self.texts(tab, 1)
        tab.sql_input.setPlainText('SELECT col1 FROM csv_data')                   # query personalizzata che usa il vecchio nome
        self.assertTrue(tab.sql_dirty)
        seen = self.drive_name(["col2", "", "Descrizione"])
        self.box_answer = QMessageBox.No                                           # «sostituire la query?» -> No
        tab.rename_column_prompt("col1"); settle(self.win)
        self.assertTrue(any("Vuoi sostituirla" in b[1] for b in self.boxes))        # lo propone esplicitamente
        self.assertIn("Esiste già", seen["attempts"][0])
        self.assertIn("vuoto", seen["attempts"][1])
        self.assertEqual(tab.columns, ["col0", "Descrizione", "col2"])
        self.assertEqual(self.texts(tab, 1), before)                              # valori e identità invariati
        self.assertIn("Descrizione", tab.column_filters)
        self.assertEqual(tab.sort[0], "Descrizione")
        self.assertTrue(tab.table.horizontalHeaderItem(1).text().startswith("Descrizione"))
        self.assertEqual(tab.sql_input.toPlainText(), "SELECT col1 FROM csv_data")  # non modificata in silenzio
        self.assertTrue(tab.sql_notice.isVisible() or not tab.sql_notice.isHidden())
        self.assertIn("potrebbe essere necessario aggiornarla", tab.sql_notice.text())
        self.assertFalse(tab.sql_notice_button.isHidden())
        self.assertIn("personalizzata", tab.sql_mode_label.text())
        tab.restore_sql()
        self.assertIn("automatica", tab.sql_mode_label.text())                                                          # la query generata usa il nuovo nome
        self.assertIn('"Descrizione"', tab.sql_input.toPlainText())
        tab.run_sql_button.click(); settle(self.win)
        self.assertEqual(tab.sql_result.rowCount(), 11)

    def test_delete_column_asks_confirmation_and_cleans_dependent_state(self):
        tab = self.open(self.make_csv(rows=40, cols=4))
        self.drive_filter(tab, "col2", "contains", "valore")
        tab.sort = ("col2", True)
        tab.filters_changed(); settle(self.win)
        self.drive_cf([self.rule("col2", "contains", "1", "#ff0000"), self.rule("col1", "contains", "1", "#00ff00")])
        tab.open_cf(); settle(self.win)
        tab.hidden_columns.add("col2"); tab.apply_hidden_columns()
        # rifiuto
        self.box_answer = QMessageBox.No
        tab.delete_column_prompt("col2"); settle(self.win)
        self.assertIn("col2", tab.columns)
        self.assertIn("arriva nel file solo quando salvi", self.boxes[-1][1])
        self.assertNotIn("non viene mai modificato", self.boxes[-1][1])           # per un CSV «Salva» sostituisce il file
        self.assertIn("rimossi anche", self.boxes[-1][1])
        # conferma
        self.box_answer = QMessageBox.Yes
        tab.delete_column_prompt("col2"); settle(self.win)
        self.assertEqual(tab.columns, ["col0", "col1", "col3"])
        self.assertEqual(tab.table.columnCount(), 3)
        self.assertEqual(tab.column_filters, {})
        self.assertIsNone(tab.sort)
        self.assertEqual([r["column"] for r in tab.cf_rules], ["col1"])
        self.assertNotIn("col2", tab.hidden_columns)
        self.assertNotIn("col2", tab.sql_input.toPlainText())
        self.assertTrue(tab.has_unsaved())
        self.assertEqual(tab.table.rowCount(), 40)
        tab.sql_input.setPlainText("SELECT * FROM csv_data LIMIT 1"); tab.run_sql_button.click(); settle(self.win)
        self.assertEqual(tab.sql_result.columnCount(), 3)

    def test_delete_rows_confirms_and_renumbers_and_add_row(self):
        tab = self.open(self.make_csv(rows=30, cols=3))
        self.click_row_header(tab, 2)
        self.click_row_header(tab, 4, Qt.ControlModifier)
        self.box_answer = QMessageBox.No
        tab.delete_rows_prompt(); settle(self.win)
        self.assertEqual(tab.total_rows, 30)
        self.assertIn("arriva nel file solo quando salvi", self.boxes[-1][1])
        self.assertNotIn("non viene mai modificato", self.boxes[-1][1])           # per un CSV «Salva» sostituisce il file
        self.box_answer = QMessageBox.Yes
        tab.delete_rows_prompt(); settle(self.win)
        self.assertEqual(tab.total_rows, 28)
        self.assertEqual(tab.table.rowCount(), 28)
        self.assertEqual(tab.table.item(2, 0).text(), "3")                       # il record «2» non c'è più
        self.assertEqual([tab.table.verticalHeaderItem(r).text() for r in range(5)], ["1", "2", "3", "4", "5"])
        self.assertIn("28 righe", tab.file_meta.text())
        # nuova riga in fondo, selezionata e modificabile
        tab.add_row_prompt(); settle(self.win)
        self.assertEqual(tab.total_rows, 29)
        self.assertEqual(tab.table.currentRow(), 28)
        self.assertEqual(self.texts(tab, 1)[-1], "")
        tab.table.item(28, 1).setText("nuova"); settle(self.win)
        copy = self.p("o.csv")
        tab.engine.save_csv(copy)
        rows = read_csv(copy)
        self.assertEqual(len(rows), 30)
        self.assertEqual(rows[-1][1], "nuova")
        self.assertEqual(rows[3][0], "3")

    def test_delete_rows_without_selection_asks_to_select(self):
        tab = self.open(self.make_csv(rows=10, cols=3))
        tab.table.clearSelection()
        tab.delete_rows_prompt()
        self.assertTrue(self.messages and "Seleziona le righe" in str(self.messages[-1]))

    def test_changes_persist_after_save_and_reopen(self):
        path = self.make_csv("orig.csv", 40, 4)
        tab = self.open(path)
        self.drive_name("Nome")
        tab.rename_column_prompt("col1"); settle(self.win)
        self.drive_name("Extra")
        tab.add_column_prompt(); settle(self.win)
        tab.delete_column_prompt("col3"); settle(self.win)
        d = self.find_setup(tab, "valore", "V")
        tab.replace_all(); settle(self.win)
        tab.table.item(0, 3).setText("e0"); settle(self.win)
        self.click_row_header(tab, 9)
        tab.delete_rows_prompt(); settle(self.win)
        before = open(path, "rb").read()
        copy = self.p("salvato.csv")
        self.drive_save(path=copy)
        self.box_click = "Continua sulla copia"
        tab.save(); settle(self.win)
        self.assertEqual(open(path, "rb").read(), before)                          # originale intatto
        self.assertFalse(tab.has_unsaved())                                        # si prosegue sulla copia: nulla in sospeso
        self.assertEqual(tab.path, copy)
        self.assertEqual(tab.save_button.text(), "Salva")
        rows = read_csv(copy)
        self.assertEqual(rows[0], ["col0", "Nome", "col2", "Extra"])
        self.assertEqual(len(rows), 40)                                            # 39 record + intestazione
        self.assertEqual(rows[1], ["0", "V 0-1", "V 0-2", "e0"])
        self.assertNotIn("9", [r[0] for r in rows[1:]])
        reopened = self.open(copy)
        self.assertEqual(reopened.columns, ["col0", "Nome", "col2", "Extra"])
        self.assertEqual(reopened.total_rows, 39)

    def test_unsaved_structure_changes_are_protected_on_close_and_reload(self):
        tab = self.open(self.make_csv(rows=20, cols=3))
        self.drive_name("Nuova")
        tab.add_column_prompt(); settle(self.win)
        self.assertTrue(tab.has_unsaved())
        self.unsaved_choice = "cancel"
        self.win.close_widget(tab)
        self.assertIn(tab, self.win.csv_tabs())
        self.assertEqual(len(self.unsaved_asked), 1)
        self.assertFalse(tab.reload(tab.import_options, None, True))
        self.assertEqual(len(self.unsaved_asked), 2)

    def test_context_menus_offer_the_column_and_row_commands(self):
        tab = self.open(self.make_csv(rows=20, cols=3))
        seen = []
        real_menu = A.QMenu

        class RecordingMenu(real_menu):
            def exec(self, *a, **k):
                seen.append([x.text() for x in self.actions() if x.text()])

        A.QMenu = RecordingMenu
        self.addCleanup(setattr, A, "QMenu", real_menu)
        tab._header_menu(1, QPoint(5, 5))
        header_items = seen[-1]
        for expected in ("Filtra e ordina…", "Rinomina colonna…", "Aggiungi colonna…", "Elimina colonna…", "Nascondi colonna"):
            self.assertIn(expected, header_items)
        self.assertEqual(tab.table.selected_columns(), [1])                         # il menu seleziona la colonna
        tab._rows_menu(QPoint(5, 5), 3)
        row_items = seen[-1]
        for expected in ("Aggiungi riga dopo la selezione", "Elimina righe selezionate…", "Trova e sostituisci…", "Copia"):
            self.assertIn(expected, row_items)
        self.assertEqual(tab.table.selected_rows(), [3])
        menu = [a.text() for a in tab.edit_menu.actions()]
        for expected in ("Elimina colonna…", "Trova e sostituisci…", "Formattazione condizionale…"):
            self.assertIn(expected, menu)
        self.assertEqual(tab.edit_button.text(), "Menu")

    def test_global_sql_sees_modified_data(self):
        a = self.open(self.make_csv("a.csv", 20, 3))
        b = self.open(self.make_csv("b.csv", 20, 3))
        a.table.item(3, 1).setText("CAMBIATO"); settle(self.win)
        g = self.win.global_tab
        g.sql_input.setPlainText("SELECT t1.col1 FROM t1 JOIN t2 ON t1.col0 = t2.col0 WHERE t1.col1 = 'CAMBIATO'")
        g.run_sql(); pump(lambda: not g.is_busy()); APP.processEvents()
        self.assertEqual(g.result.rowCount(), 1)
        self.assertEqual(g.result.item(0, 0).text(), "CAMBIATO")
        self.drive_name("Nuova")
        a.add_column_prompt(); settle(self.win)
        g.sql_input.setPlainText("SELECT Nuova FROM t1 LIMIT 1")
        g.run_sql(); pump(lambda: not g.is_busy()); APP.processEvents()
        self.assertEqual(g.result.columnCount(), 1)

    def test_edits_in_detail_dialog_reach_filters(self):
        tab = self.open(self.make_csv(rows=20, cols=3))
        tab.apply_edit(4, "col1", "DAL-DETTAGLIO", 4, 1)
        settle(self.win)
        self.assertEqual(tab.engine.cell_text(4, "col1"), "DAL-DETTAGLIO")
        self.drive_filter(tab, "col1", "starts", "dal-")
        self.assertEqual(tab._filtered_count, 1)


if __name__ == "__main__":
    unittest.main()


class TestPlacement(P2Ui):
    def test_new_column_goes_after_the_selected_column_and_row_after_the_selected_row(self):
        tab = self.open(self.make_csv(rows=20, cols=4))
        self.click_header(tab, 1)
        self.drive_name("Nuova")
        tab.add_column_prompt(); settle(self.win)
        self.assertEqual(tab.columns, ["col0", "col1", "Nuova", "col2", "col3"])
        self.click_row_header(tab, 3)
        tab.add_row_prompt(); settle(self.win)
        self.assertEqual(tab.total_rows, 21)
        self.assertIsNone(tab.engine.cell_text(4, "col1"))
        self.assertEqual(tab.engine.cell_text(5, "col0"), "4")           # i record seguenti slittano di uno

    def test_global_sql_tab_is_first_and_pinned(self):
        a = self.open(self.make_csv("a.csv", 10, 3))
        b = self.open(self.make_csv("b.csv", 10, 3))
        docs = self.win.docs
        self.assertIs(docs.widget(0), self.win.global_tab)
        c = self.open(self.make_csv("c.csv", 10, 3))
        self.assertIs(docs.widget(0), self.win.global_tab)
        docs.tabBar().moveTab(0, 2)
        APP.processEvents()
        self.assertIs(docs.widget(0), self.win.global_tab)
