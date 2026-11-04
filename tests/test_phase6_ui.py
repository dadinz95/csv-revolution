"""v0.17.0 - interfaccia: tutto testo all'apertura, larghezza standard e adattamento (menu «Colonne»), menu «Tipi» (offscreen).
Esecuzione:  QT_QPA_PLATFORM=offscreen python -m unittest tests.test_phase6_ui -v"""
import os
import unittest

from PySide6.QtWidgets import QDialog, QMessageBox, QToolButton

from csv_reader.desktop import app as A
from csv_reader.desktop.tools_dialogs import TypesDialog
from tests.test_phase4_ui import P4
from tests.test_ui import APP, pump, settle


class P6(P4):
    def csv(self, text, name="t.csv"):
        path = self.p(name)
        with open(path, "w", encoding="utf-8", newline="") as fh:
            fh.write(text)
        return path

    def widths(self, tab):
        return [tab.table.columnWidth(c) for c in range(tab.table.columnCount())]

    def reply_box(self, button):
        """Sostituisce QMessageBox.exec: registra testo e pulsanti e risponde (Yes/No)."""
        seen = []
        orig = A.QMessageBox.exec

        def fake(b):
            seen.append((b.text(), b.informativeText()))
            return button
        A.QMessageBox.exec = fake
        self.addCleanup(setattr, A.QMessageBox, "exec", orig)
        return seen


class TestOpenAsText(P6):
    def test_values_open_verbatim_and_status_shows_text_type(self):
        tab = self.open(self.csv("cod,imp,data\n00123,1.50,2024-01-05\n00045,2,05/01/2024\n"))
        self.assertEqual([tab.table.item(0, c).text() for c in range(3)], ["00123", "1.50", "2024-01-05"])
        self.assertEqual([tab.table.item(1, c).text() for c in range(3)], ["00045", "2", "05/01/2024"])
        self.assertEqual(set(tab.engine.dtypes.values()), {"VARCHAR"})
        self.assertFalse(tab.has_unsaved())                           # aprire non è una modifica
        self.assertIn("Testo", tab.table.horizontalHeaderItem(0).toolTip())

    def test_save_without_conversion_keeps_everything(self):
        path = self.csv("cod;imp\n00123;1.50\n00045;2,5\n")
        tab = self.open(path)
        tab.engine.set_cell(0, "imp", "1.50")                         # nessuna modifica reale
        out = self.p("o.csv")
        tab.engine.save_csv(out, ";", "utf-8", ".")
        self.assertEqual(open(out).read(), "cod;imp\n00123;1.50\n00045;2,5\n")


class TestColumnWidths(P6):
    LONG = "x" * 500

    def make(self):
        return self.open(self.csv(f"a,b,c\n1,{self.LONG},3\n2,y,{self.LONG}\n"))

    def test_standard_uniform_width_even_with_very_long_values(self):
        tab = self.make()
        self.assertEqual(set(self.widths(tab)), {A.CsvTab.STANDARD_WIDTH})

    def test_width_actions_live_only_in_the_columns_menu(self):
        tab = self.make()
        self.assertTrue(hasattr(tab.columns_button, "fit_button") and hasattr(tab.columns_button, "reset_button"))
        shown = [w.text() for kind in (A.QPushButton, QToolButton) for w in tab.findChildren(kind) if w.isVisibleTo(tab)]
        self.assertFalse([t for t in shown if "Adatta" in t or "Larghezza" in t])     # nessun pulsante in più nella barra
        self.assertEqual(tab.columns_button.fit_button.text(), "Adatta larghezza al contenuto")

    def test_manual_resize_is_free_and_survives_page_change(self):
        tab = self.open(self.csv("a,b\n" + "".join(f"{i},v{i}\n" for i in range(500))))
        tab.page_size = 100
        tab.reset_and_load(); settle(self.win)
        tab.table.setColumnWidth(0, 300)
        tab.table.setColumnWidth(1, 70)
        tab.next_page(); settle(self.win)
        self.assertEqual(self.widths(tab), [300, 70])

    def test_fit_uses_header_and_values_with_a_maximum_and_repeats_after_manual_changes(self):
        tab = self.make()
        tab.fit_column_widths()
        w = self.widths(tab)
        self.assertLessEqual(max(w), A.CsvTab.ADAPT_MAX_WIDTH)
        self.assertEqual(w[1], A.CsvTab.ADAPT_MAX_WIDTH)              # valore lunghissimo: limite
        self.assertLess(w[0], A.CsvTab.STANDARD_WIDTH)               # colonna corta: più stretta dello standard
        tab.table.setColumnWidth(0, 400)                              # modifica manuale
        tab.fit_column_widths()                                       # nuovo adattamento dopo la modifica manuale
        self.assertEqual(self.widths(tab)[0], w[0])
        self.assertIn("adattata", tab.status.text())

    def test_reset_restores_the_uniform_standard_width(self):
        tab = self.make()
        tab.fit_column_widths()
        tab.table.setColumnWidth(2, 33)
        tab.reset_column_widths()
        self.assertEqual(set(self.widths(tab)), {A.CsvTab.STANDARD_WIDTH})
        tab.refresh_view(); settle(self.win)                          # e anche dopo un ricaricamento
        self.assertEqual(set(self.widths(tab)), {A.CsvTab.STANDARD_WIDTH})

    def test_width_is_kept_across_hide_show_and_follows_rename(self):
        tab = self.make()
        tab.table.setColumnWidth(0, 250)
        tab.columns_button.hidden.add("a"); tab.apply_hidden_columns(); settle(self.win)
        tab.columns_button.hidden.discard("a"); tab.apply_hidden_columns(); settle(self.win)
        self.assertEqual(tab.table.columnWidth(0), 250)
        tab.engine.rename_column("a", "z")
        tab._renamed("a", "z", tab.total_rows); settle(self.win)
        self.assertEqual(tab.table.columnWidth(0), 250)

    def test_menu_buttons_trigger_the_commands(self):
        tab = self.make()
        tab.columns_button.fit_button.click()
        self.assertEqual(max(self.widths(tab)), A.CsvTab.ADAPT_MAX_WIDTH)
        tab.columns_button.reset_button.click()
        self.assertEqual(set(self.widths(tab)), {A.CsvTab.STANDARD_WIDTH})


class TestTypesMenu(P6):
    TEXT = "cod,n,imp,data,ok\n00123,1,1.50,05/01/2024,vero\n00045,2,2.5,2024-02-01,falso\n,3,x,31/12/2023,si\n"

    def test_menu_has_the_actions_and_marks_the_current_type(self):
        tab = self.open(self.csv(self.TEXT))
        self.assertTrue(tab.types_button.isEnabled())
        tab.cell_ = None
        tab.table.setCurrentCell(0, 1)
        tab._update_types_menu()
        self.assertIn("Numero intero" if False else "Testo", tab.type_current_action.text())
        labels = [a.text() for a in tab.type_set_menu.actions()]
        self.assertEqual(labels, ["Testo", "Numero intero", "Numero decimale", "Data", "Data e ora", "Booleano"])
        self.assertTrue(tab.type_set_menu.actions()[0].isChecked())

    def test_set_type_from_menu_converts_and_undo_restores(self):
        tab = self.open(self.csv(self.TEXT))
        tab.table.setCurrentCell(0, 1)
        tab._update_types_menu()
        tab.set_column_type("int"); settle(self.win)
        self.assertEqual(tab.engine.dtypes["n"], "BIGINT")
        self.assertTrue(tab.has_unsaved())
        self.assertIn("Tipo assegnato", tab.status.text())
        tab.undo(); settle(self.win)
        self.assertEqual(tab.engine.dtypes["n"], "VARCHAR")
        self.assertFalse(tab.has_unsaved())
        self.assertEqual(tab.table.item(0, 1).text(), "1")

    def test_non_convertible_values_ask_before_converting_and_cancel_changes_nothing(self):
        tab = self.open(self.csv(self.TEXT))
        tab.table.setCurrentCell(0, 2)
        tab._update_types_menu()
        seen = self.reply_box(QMessageBox.No)
        tab.set_column_type("decimal"); settle(self.win)
        self.assertEqual(len(seen), 1)
        self.assertIn("«x»", seen[0][1])
        self.assertIn("diventeranno vuoti", seen[0][1])
        self.assertEqual(tab.engine.dtypes["imp"], "VARCHAR")
        self.assertFalse(tab.has_unsaved())

    def test_confirmed_conversion_blanks_only_the_invalid_values(self):
        tab = self.open(self.csv(self.TEXT))
        tab.table.setCurrentCell(0, 2)
        tab._update_types_menu()
        self.reply_box(QMessageBox.Yes)
        tab.set_column_type("decimal"); settle(self.win)
        self.assertEqual([tab.table.item(r, 2).text() for r in range(3)], ["1.50", "2.50", ""])
        self.assertIn("non convertibili", tab.status.text())
        tab.undo(); settle(self.win)
        self.assertEqual([tab.table.item(r, 2).text() for r in range(3)], ["1.50", "2.5", "x"])

    def test_leading_zero_warning(self):
        tab = self.open(self.csv("cod\n00123\n00045\n"))
        tab.table.setCurrentCell(0, 0)
        tab._update_types_menu()
        seen = self.reply_box(QMessageBox.No)
        tab.set_column_type("int"); settle(self.win)
        self.assertIn("zeri", seen[0][1])
        self.assertEqual(tab.engine.dtypes["cod"], "VARCHAR")

    def test_filters_on_converted_column_are_dropped_with_chips(self):
        tab = self.open(self.csv("n\n1\n2\n3\n"))
        tab.column_filters["n"] = {"op": "contains", "value": "1"}
        tab.table.setCurrentCell(0, 0)
        tab._update_types_menu()
        tab.set_column_type("int"); settle(self.win)
        self.assertNotIn("n", tab.column_filters)
        self.assertEqual(tab.table.rowCount(), 3)

    def test_sorted_numerically_only_after_conversion(self):
        tab = self.open(self.csv("n\n10\n9\n100\n"))
        tab.sort = ("n", False); tab.reset_and_load(); settle(self.win)
        self.assertEqual([tab.table.item(r, 0).text() for r in range(3)], ["10", "100", "9"])
        tab.table.setCurrentCell(0, 0); tab._update_types_menu()
        tab.set_column_type("int"); settle(self.win)
        self.assertEqual([tab.table.item(r, 0).text() for r in range(3)], ["9", "10", "100"])


class TestTypesDialog(P6):
    TEXT = "cod,n,imp,data,ok\n00123,1,1.50,05/01/2024,vero\n00045,2,2.5,2024-02-01,falso\n,3,4,31/12/2023,si\n"

    def run_dialog(self, tab, edit):
        seen = {}
        orig = A.TypesDialog.exec

        def fake(d):
            d.detect()                                                   # come showEvent con auto_detect
            self.assertTrue(pump(lambda: bool(d.analysis), timeout=15))
            seen["analysis"] = dict(d.analysis)
            seen["cells"] = [[d.table.item(r, c).text() for c in range(4)] for r in range(d.table.rowCount())]
            edit(d)
            d._apply()
            return QDialog.Accepted if d.changes else QDialog.Rejected
        A.TypesDialog.exec = fake
        try:
            tab.open_types(detect=True)
            settle(self.win)
        finally:
            A.TypesDialog.exec = orig
        return seen

    def test_detect_shows_recognised_types_and_reasons_without_changing_anything(self):
        tab = self.open(self.csv(self.TEXT))
        before = [tab.table.item(r, c).text() for r in range(3) for c in range(5)]
        seen = self.run_dialog(tab, lambda d: None)
        by = {row[0]: row for row in seen["cells"]}
        self.assertEqual(by["cod"][2], "Testo")
        self.assertIn("identificatori", by["cod"][3])
        self.assertEqual(by["n"][2], "Numero intero")
        self.assertEqual(by["imp"][2], "Numero decimale")
        self.assertEqual(by["data"][2], "Data")
        self.assertEqual(by["ok"][2], "Booleano")
        self.assertEqual(set(tab.engine.dtypes.values()), {"VARCHAR"})
        self.assertEqual([tab.table.item(r, c).text() for r in range(3) for c in range(5)], before)
        self.assertFalse(tab.has_unsaved())

    def test_use_recognised_then_apply_converts_several_columns_in_one_go(self):
        tab = self.open(self.csv(self.TEXT))

        def choose(d):
            d.use_detected()
            self.assertEqual(d.combos[0].currentData(), None)         # «cod»: identificatori, non toccata
        self.run_dialog(tab, choose)
        self.assertEqual({c: tab.engine.dtypes[c] for c in tab.columns},
                         {"cod": "VARCHAR", "n": "BIGINT", "imp": "DECIMAL(18,2)", "data": "DATE", "ok": "BOOLEAN"})
        for _ in range(4):
            tab.undo(); settle(self.win)
        self.assertEqual(set(tab.engine.dtypes.values()), {"VARCHAR"})
        self.assertFalse(tab.has_unsaved())

    def test_manual_choice_and_decimals(self):
        tab = self.open(self.csv(self.TEXT))

        def choose(d):
            row = d.columns.index("imp")
            d.combos[row].setCurrentIndex(d.combos[row].findData("decimal"))
            self.assertFalse(d.spins[row].isHidden())
            self.assertEqual(d.spins[row].minimum(), 2)               # «1.50» ha 2 decimali: non si scende sotto (nessun arrotondamento)
            d.spins[row].setValue(3)
        self.run_dialog(tab, choose)
        self.assertEqual(tab.engine.dtypes["imp"], "DECIMAL(18,3)")
        self.assertEqual([tab.table.item(r, 2).text() for r in range(3)], ["1.500", "2.500", "4.000"])

    def test_dialog_without_choices_reports_and_closes_nothing(self):
        tab = self.open(self.csv(self.TEXT))
        d = TypesDialog(tab, tab.columns, {c: "text" for c in tab.columns}, lambda cols: [], tab.run_op)
        d._apply()
        self.assertEqual(d.changes, [])
        self.assertIn("Nessuna modifica", d.state.text())


if __name__ == "__main__":
    unittest.main()
