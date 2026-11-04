"""Fase 3 - salvataggio, avvisi sulle modifiche non salvate e query SQL automatica (interfaccia, offscreen).
Esecuzione:  QT_QPA_PLATFORM=offscreen python -m unittest tests.test_phase3_ui -v"""
import csv
import os
import shutil
import time
import unittest

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QMessageBox

from csv_reader.core.engine import ImportProblem
from csv_reader.desktop import app as A
from tests.test_phase2_ui import P2Ui
from tests.test_ui import APP, pump, settle, sha


def read_rows(path, delimiter=",", encoding="utf-8"):
    with open(path, newline="", encoding=encoding) as fh:
        return list(csv.reader(fh, delimiter=delimiter))


class P3Ui(P2Ui):
    def make_xlsx(self, name="t.xlsx", rows=30):
        import openpyxl
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "Dati"
        ws.append(["id", "codice", "nome"])
        for i in range(rows):
            ws.append([i, f"{i:05d}", f"nome {i}"])
        wb.create_sheet("Altro").append(["k", "v"])
        path = self.p(name)
        wb.save(path)
        return path

    def open_excel(self, path):
        tab = A.CsvTab()
        self.win.docs.addTab(tab, "x")
        tab.table_name = "t1"
        tab.open(path, sheet="Dati", header=True)
        self.assertTrue(pump(lambda: tab.columns and tab.table.rowCount() > 0 and not tab.is_busy()))
        settle(self.win)
        return tab

    def edit(self, tab, row=1, col=1, text="MOD"):
        tab.table.item(row, col).setText(text)
        settle(self.win)

    def save_and_wait(self, tab, fn):
        fn()
        settle(self.win)
        APP.processEvents()


class TestSaveCommands(P3Ui):
    def test_three_distinct_commands_exist_and_count_changes(self):
        tab = self.open(self.make_csv(rows=20, cols=3))
        self.assertEqual([a.text().split("\t")[0] for a in tab.save_menu.actions()], ["Salva", "Salva con nome…"])
        self.assertFalse(hasattr(tab, "copy_button"))
        self.assertFalse(hasattr(tab, "save_copy"))
        self.edit(tab)
        self.assertEqual(tab.save_button.text(), "Salva (1)")

    def test_save_preselects_original_format_and_keeps_csv_dialect(self):
        path = self.p("it.csv")
        with open(path, "w", encoding="cp1252", newline="") as fh:
            fh.write("id;città;nota\r\n1;Forlì;\"a;b\"\r\n2;Perugia;caffè\r\n")
        tab = self.open(path)
        self.edit(tab, 0, 2, "più")
        seen = self.drive_save()
        tab.save(); settle(self.win)
        self.assertEqual((seen["format"], seen["locked"]), ("csv", True))
        rows = read_rows(path, ";", "cp1252")                      # stesso delimitatore e stessa codifica dell'importazione
        self.assertEqual(rows[1], ["1", "Forlì", "più"])
        self.assertFalse(tab.has_unsaved())

    def test_save_with_other_format_creates_copy_and_asks_where_to_continue(self):
        path = self.make_csv("o.csv", 20, 3)
        before = sha(path)
        tab = self.open(path)
        self.edit(tab)
        xlsx = self.p("o.xlsx")
        seen = self.drive_save(fmt="xlsx")
        self.assertTrue(seen["dialogs"] == 0)
        self.box_click = "Continua sul file corrente"
        tab.save(); settle(self.win)
        self.assertEqual(seen["format"], "csv")
        self.assertTrue(seen["path_after_format"].endswith(".xlsx"))      # estensione coerente con il formato
        self.assertTrue(os.path.exists(xlsx))
        self.assertEqual(sha(path), before)                               # il CSV non è stato toccato
        self.assertEqual(tab.path, path)                                  # percorso invariato
        self.assertTrue(tab.has_unsaved())                                # la copia non vale come salvataggio del file corrente
        self.assertTrue(any("Su quale file" in b[1] for b in self.boxes))
        import openpyxl
        self.assertEqual(openpyxl.load_workbook(xlsx)["Dati"]["B3"].value, "MOD")

    def test_continue_on_copy_updates_path_title_and_following_saves_go_to_the_copy(self):
        path = self.make_csv("o.csv", 20, 3)
        before = sha(path)
        tab = self.open(path)
        self.edit(tab)
        xlsx = self.p("nuovo.xlsx")
        self.drive_save(path=xlsx, fmt="xlsx")
        self.box_click = "Continua sulla copia"
        tab.save(); settle(self.win)
        self.assertEqual(tab.path, xlsx)
        self.assertFalse(tab.has_unsaved())
        self.assertIn("nuovo.xlsx", self.win.docs.tabText(self.win.docs.indexOf(tab)))
        self.assertEqual(sha(path), before)
        self.edit(tab, 2, 1, "SECONDA")
        seen = self.drive_save()
        tab.save(); settle(self.win)
        self.assertEqual((seen["format"], seen["locked"]), ("xlsx", True))   # ora il file di lavoro è la copia xlsx
        import openpyxl
        self.assertEqual(openpyxl.load_workbook(xlsx)["Dati"]["B4"].value, "SECONDA")
        self.assertEqual(sha(path), before)
        self.assertFalse(tab.has_unsaved())

    def test_save_as_chooses_folder_name_delimiter_and_encoding(self):
        tab = self.open(self.make_csv("o.csv", 20, 3))
        self.edit(tab, 0, 1, "àèù")
        os.mkdir(self.p("sub"))
        dest = self.p("sub/nuovo")                                         # senza estensione: viene aggiunta
        seen = self.drive_save(path=dest, delimiter="|", encoding="cp1252", decimal=",")
        tab.save_as(); settle(self.win)
        out = self.p("sub/nuovo.csv")
        self.assertTrue(os.path.exists(out))
        self.assertEqual(read_rows(out, "|", "cp1252")[1][1], "àèù")
        self.assertEqual(seen["request"]["encoding"], "cp1252")
        self.assertTrue(tab.has_unsaved())                                 # si è scelto di restare sul file corrente

    def test_extension_always_matches_the_format(self):
        tab = self.open(self.make_csv("o.csv", 10, 3))
        seen = self.drive_save(path=self.p("sbagliata.xlsx"), fmt="csv")
        tab.save_as(); settle(self.win)
        self.assertTrue(os.path.exists(self.p("sbagliata.csv")))
        self.assertFalse(os.path.exists(self.p("sbagliata.xlsx")))
        self.drive_save(path=self.p("altra.csv"), fmt="xlsx")
        tab.save_as(); settle(self.win)
        self.assertTrue(os.path.exists(self.p("altra.xlsx")))
        self.assertFalse(os.path.exists(self.p("altra.csv")))

    def test_existing_destination_needs_confirmation(self):
        tab = self.open(self.make_csv("o.csv", 10, 3))
        other = self.p("esiste.csv")
        with open(other, "w") as fh:
            fh.write("vecchio\n")
        self.drive_save(path=other)
        self.answer = QMessageBox.No
        tab.save_as(); settle(self.win)
        self.assertEqual(self.messages[-1][0], "question")
        self.assertEqual(open(other).read(), "vecchio\n")                  # rifiutato: intatto
        self.answer = QMessageBox.Yes
        tab.save_as(); settle(self.win)
        self.assertNotEqual(open(other).read(), "vecchio\n")


class TestExtraCopy(P3Ui):
    def test_copy_does_not_change_path_or_saved_state(self):
        path = self.make_csv("o.csv", 20, 3)
        before = sha(path)
        tab = self.open(path)
        self.edit(tab)
        copy = self.p("copia.csv")
        seen = self.drive_save(path=copy, delimiter=";")
        self.box_click = 'Continua sul file corrente'; tab.save_as(); settle(self.win)
        self.assertEqual(seen["format"], "csv")                            # formato originale preselezionato
        self.assertTrue(os.path.exists(copy))
        self.assertEqual(tab.path, path)
        self.assertEqual(sha(path), before)                                # il principale non è stato scritto
        self.assertTrue(tab.has_unsaved())                                 # stato del documento mantenuto
        self.assertEqual(read_rows(copy, ";")[2][1], "MOD")

    def test_copy_contains_everything_ignoring_filters_hidden_columns_and_pagination(self):
        tab = self.open(self.make_csv("o.csv", 500, 4))
        self.drive_filter(tab, "col0", "lt", "10")
        tab.hidden_columns.add("col2"); tab.apply_hidden_columns()
        tab.page_size = 50
        tab.sort = ("col0", True); tab.filters_changed(); settle(self.win)
        self.assertEqual(tab._filtered_count, 10)
        copy = self.p("tutto.csv")
        self.drive_save(path=copy)
        self.box_click = 'Continua sul file corrente'; tab.save_as(); settle(self.win)
        rows = read_rows(copy)
        self.assertEqual(len(rows), 501)                                   # tutte le righe
        self.assertEqual(rows[0], ["col0", "col1", "col2", "col3"])        # anche la colonna nascosta, stesso ordine
        self.assertEqual([r[0] for r in rows[1:4]], ["0", "1", "2"])       # ordine del file, non quello della vista

    def test_failed_save_keeps_changes_and_state(self):
        tab = self.open(self.make_csv("o.csv", 20, 3))
        self.edit(tab)
        crit = []
        orig = QMessageBox.critical
        QMessageBox.critical = staticmethod(lambda *a, **k: crit.append(a[1:3]) or QMessageBox.Ok)
        real = tab.engine.save_csv
        tab.engine.save_csv = lambda *a, **k: (_ for _ in ()).throw(ImportProblem("Permessi insufficienti.", fixable=False))
        try:
            self.drive_save()
            tab.save(); settle(self.win)
        finally:
            QMessageBox.critical = orig
            tab.engine.save_csv = real
        self.assertTrue(tab.has_unsaved())
        self.assertEqual(tab.save_button.text(), "Salva (1)")
        self.assertIn("Permessi insufficienti", crit[0][1])
        self.assertIn("nulla è andato perso", crit[0][1])
        self.assertEqual(tab.engine.cell_text(1, "col1"), "MOD")


class TestExcelProtection(P3Ui):
    def test_xlsx_save_never_touches_original_and_default_format_is_xlsx(self):
        path = self.make_xlsx()
        before = sha(path)
        tab = self.open_excel(path)
        self.edit(tab, 1, 2, "MOD")
        seen = self.drive_save(path=path)                                  # tentativo di sovrascrivere l'originale
        tab.save(); settle(self.win)
        self.assertEqual(seen["format"], "xlsx")
        self.assertFalse(seen["locked"])                                   # per un Excel «Salva» chiede sempre una copia
        self.assertIn("non viene mai sovrascritto", seen["error"])
        self.assertEqual(sha(path), before)
        copy = self.p("copia.xlsx")
        self.drive_save(path=copy)
        tab.save(); settle(self.win)
        self.assertEqual(sha(path), before)
        self.assertTrue(tab.has_unsaved())
        import openpyxl
        wb = openpyxl.load_workbook(copy)
        self.assertEqual(wb["Dati"]["C3"].value, "MOD")
        self.assertIn("Altro", wb.sheetnames)                              # foglio non importato conservato

    def test_continue_on_xlsx_copy_keeps_original_separate_and_protected(self):
        path = self.make_xlsx()
        before = sha(path)
        tab = self.open_excel(path)
        self.edit(tab, 1, 2, "UNO")
        copy = self.p("lavoro.xlsx")
        self.drive_save(path=copy)
        self.box_click = "Continua sulla copia"
        tab.save(); settle(self.win)
        self.assertEqual((tab.path, tab.original_path), (copy, path))
        self.assertFalse(tab.has_unsaved())
        self.edit(tab, 2, 2, "DUE")
        seen = self.drive_save()
        tab.save(); settle(self.win)
        self.assertTrue(seen["locked"])                                    # la copia si può risalvare
        self.assertEqual(sha(path), before)
        import openpyxl
        ws = openpyxl.load_workbook(copy)["Dati"]
        self.assertEqual((ws["C3"].value, ws["C4"].value), ("UNO", "DUE"))
        self.drive_save(path=path)                                         # l'originale resta protetto anche ora
        self.edit(tab, 3, 2, "TRE")
        seen = self.drive_save(path=path, fmt="xlsx")
        tab.save_as(); settle(self.win)
        self.assertIn("non viene mai sovrascritto", seen["error"])
        self.assertEqual(sha(path), before)

    def test_xlsx_exported_as_csv_with_options(self):
        path = self.make_xlsx()
        tab = self.open_excel(path)
        out = self.p("dalxlsx.csv")
        seen = self.drive_save(path=out, fmt="csv", delimiter=";", encoding="utf-8-sig")
        tab.save_as(); settle(self.win)
        self.assertEqual(seen["format"], "xlsx")
        rows = read_rows(out, ";", "utf-8-sig")
        self.assertEqual(rows[0], ["id", "codice", "nome"])
        self.assertEqual(rows[2][1], "00001")                              # zeri iniziali conservati
        self.assertEqual(len(rows), 31)

    def test_xls_defaults_to_xlsx(self):
        try:
            import xlwt
        except ImportError:
            self.skipTest("xlwt non installato")
        book = xlwt.Workbook(); s = book.add_sheet("Dati")
        for j, h in enumerate(["id", "nome"]):
            s.write(0, j, h)
        for i in range(1, 6):
            s.write(i, 0, i); s.write(i, 1, f"n{i}")
        path = self.p("t.xls"); book.save(path)
        before = sha(path)
        tab = self.open_excel(path)
        self.edit(tab, 0, 1, "MOD")
        seen = self.drive_save(path=self.p("t_mod.xlsx"))
        tab.save(); settle(self.win)
        self.assertEqual(seen["format"], "xlsx")                           # .xls -> si salva come .xlsx
        self.assertTrue(seen["path"].endswith(".xlsx"))
        self.assertEqual(sha(path), before)
        import openpyxl
        self.assertEqual(openpyxl.load_workbook(self.p("t_mod.xlsx"))["Dati"]["B2"].value, "MOD")


class TestUnsavedPrompts(P3Ui):
    def test_no_prompt_without_changes(self):
        tab = self.open(self.make_csv("o.csv", 10, 3))
        self.win.close_widget(tab)
        self.assertEqual(self.unsaved_asked, [])
        self.assertNotIn(tab, self.win.csv_tabs())

    def test_close_tab_cancel_discard_and_save(self):
        path = self.make_csv("o.csv", 10, 3)
        before = sha(path)
        tab = self.open(path)
        self.edit(tab)
        self.unsaved_choice = "cancel"
        self.win.close_widget(tab)
        self.assertIn(tab, self.win.csv_tabs())
        self.assertTrue(tab.has_unsaved())
        self.unsaved_choice = "discard"
        self.win.close_widget(tab)
        self.assertNotIn(tab, self.win.csv_tabs())
        self.assertEqual(sha(path), before)                                # «non salvare»: nessun file toccato
        tab = self.open(path)
        self.edit(tab)
        self.unsaved_choice = "save"
        self.drive_save()
        self.win.close_widget(tab)
        pump(lambda: tab not in self.win.csv_tabs()); settle(self.win)
        self.assertNotIn(tab, self.win.csv_tabs())                         # chiuso solo dopo il salvataggio
        self.assertIn("MOD", open(path, encoding="utf-8").read())

    def test_save_chosen_but_dialog_cancelled_keeps_tab_open(self):
        tab = self.open(self.make_csv("o.csv", 10, 3))
        self.edit(tab)
        self.unsaved_choice = "save"
        self.drive_save(accept=False)
        self.win.close_widget(tab)
        settle(self.win)
        self.assertIn(tab, self.win.csv_tabs())
        self.assertTrue(tab.has_unsaved())

    def test_failed_save_does_not_close_or_reload(self):
        tab = self.open(self.make_csv("o.csv", 10, 3))
        self.edit(tab)
        self.unsaved_choice = "save"
        tab.engine.save_csv = lambda *a, **k: (_ for _ in ()).throw(ImportProblem("Disco pieno.", fixable=False))
        orig = QMessageBox.critical
        QMessageBox.critical = staticmethod(lambda *a, **k: QMessageBox.Ok)
        try:
            self.drive_save()
            self.win.close_widget(tab)
            settle(self.win)
        finally:
            QMessageBox.critical = orig
        self.assertIn(tab, self.win.csv_tabs())
        self.assertEqual(tab.engine.cell_text(1, "col1"), "MOD")

    def test_reload_prompts_and_save_then_reloads(self):
        path = self.make_csv("o.csv", 10, 3)
        tab = self.open(path)
        self.edit(tab)
        self.unsaved_choice = "cancel"
        self.assertFalse(tab.reload(tab.import_options, None, True))
        self.unsaved_choice = "save"
        self.drive_save()
        self.assertTrue(tab.reload(tab.import_options, None, True))
        pump(lambda: not tab.is_busy()); settle(self.win)
        self.assertEqual(tab.table.item(1, 1).text(), "MOD")               # salvato prima, poi ricaricato dal file
        self.assertFalse(tab.has_unsaved())

    def test_each_file_tracks_its_own_changes_and_app_close_asks_for_each(self):
        a = self.open(self.make_csv("a.csv", 10, 3))
        b = self.open(self.make_csv("b.csv", 10, 3))
        c = self.open(self.make_csv("c.csv", 10, 3))
        self.edit(a); self.edit(c)
        self.assertEqual((a.has_unsaved(), b.has_unsaved(), c.has_unsaved()), (True, False, True))
        self.win.docs.setCurrentWidget(b)                                  # cambiare scheda non perde nulla: nessun avviso
        self.assertEqual(self.unsaved_asked, [])
        self.unsaved_choice = "cancel"
        self.win.close()
        self.assertTrue(self.win.isVisible())                              # «Annulla»: l'applicazione resta aperta
        self.assertEqual(len(self.unsaved_asked), 1)
        self.unsaved_choice = "discard"
        self.unsaved_asked.clear()
        self.win.close()
        pump(lambda: not self.win.isVisible(), 5)
        self.assertEqual(len(self.unsaved_asked), 2)                       # uno per ciascun file modificato (a, c)
        self.assertFalse(self.win.isVisible())

    def test_structure_changes_count_as_unsaved(self):
        tab = self.open(self.make_csv("o.csv", 10, 3))
        self.assertFalse(tab.has_unsaved())
        self.drive_name("Nuova")
        tab.add_column_prompt(); settle(self.win)
        self.assertTrue(tab.has_unsaved())
        self.drive_save()
        tab.save(); settle(self.win)
        self.assertFalse(tab.has_unsaved())
        self.drive_name("Rinominata")
        tab.rename_column_prompt("col1"); settle(self.win)
        self.assertTrue(tab.has_unsaved())
        self.assertEqual(read_rows(tab.path)[0], ["col0", "col1", "col2", "Nuova"])    # il file cambia solo salvando
        self.drive_save()
        tab.save(); settle(self.win)
        self.assertEqual(read_rows(tab.path)[0], ["col0", "Rinominata", "col2", "Nuova"])


class TestSqlSync(P3Ui):
    def test_automatic_query_follows_visible_columns_filters_and_sort(self):
        tab = self.open(self.make_csv(rows=40, cols=4))
        sql = tab.sql_input.toPlainText()
        self.assertEqual([c for c in ("col0", "col1", "col2", "col3") if f'"{c}"' in sql], ["col0", "col1", "col2", "col3"])
        self.assertNotIn("WHERE", sql)
        self.assertNotIn("ORDER BY", sql)                                  # nessun ordinamento artificiale
        self.assertNotIn("__csv_row_idx", sql)
        tab.hidden_columns.update({"col1", "col3"}); tab.apply_hidden_columns()
        sql = tab.sql_input.toPlainText()
        self.assertNotIn('"col1"', sql)
        self.assertIn('"col2"', sql)
        self.drive_filter(tab, "col0", "gt", "30")
        self.drive_filter(tab, "col2", "contains", "valore 3")
        tab.sort = ("col0", True); tab.filters_changed(); settle(self.win)
        sql = tab.sql_input.toPlainText()
        self.assertEqual(sql.count("WHERE"), 1)
        self.assertIn("AND", sql)
        self.assertIn('ORDER BY "col0" DESC NULLS LAST', sql)
        tab.run_sql_button.click(); settle(self.win)                       # eseguita dà gli stessi record della tabella
        self.assertEqual(tab.sql_result.rowCount(), tab._filtered_count)
        self.assertEqual(tab.sql_result.columnCount(), 2)
        tab.sort = ("col0", False); tab.filters_changed(); settle(self.win)
        self.assertIn('ORDER BY "col0" ASC', tab.sql_input.toPlainText())

    def test_special_names_and_no_visible_column(self):
        path = self.p("sp.csv")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write('select,"nome con spazi","a""b",ünï\n1,2,3,4\n5,6,7,8\n')
        tab = self.open(path)
        tab.run_sql_button.click(); settle(self.win)                       # parole riservate, spazi, virgolette, accenti
        self.assertEqual(tab.sql_result.rowCount(), 2)
        self.assertEqual(tab.sql_result.columnCount(), 4)
        tab.hidden_columns.update(tab.columns); tab.apply_hidden_columns()
        self.assertTrue(tab.sql_input.toPlainText().startswith("--"))
        tab.run_sql_button.click(); settle(self.win)
        self.assertTrue(any("Nessuna query" in m[1][0] for m in self.messages if m[0] == "info"))
        tab.hidden_columns.clear(); tab.apply_hidden_columns()
        self.assertIn("SELECT", tab.sql_input.toPlainText())

    def test_updates_after_add_delete_and_rename(self):
        tab = self.open(self.make_csv(rows=20, cols=3))
        self.drive_name("Nuova")
        tab.add_column_prompt(); settle(self.win)
        self.assertIn('"Nuova"', tab.sql_input.toPlainText())
        self.drive_name("Altro")
        tab.rename_column_prompt("Nuova"); settle(self.win)
        sql = tab.sql_input.toPlainText()
        self.assertIn('"Altro"', sql)
        self.assertNotIn('"Nuova"', sql)
        tab.delete_column_prompt("Altro"); settle(self.win)
        self.assertNotIn('"Altro"', tab.sql_input.toPlainText())
        tab.run_sql_button.click(); settle(self.win)
        self.assertEqual(tab.sql_result.columnCount(), 3)

    def test_edits_are_visible_to_queries(self):
        tab = self.open(self.make_csv(rows=20, cols=3))
        self.edit(tab, 4, 1, "TROVAMI")
        tab.sql_input.setPlainText("SELECT col0 FROM csv_data WHERE col1 = 'TROVAMI'")
        tab.run_sql_button.click(); settle(self.win)
        self.assertEqual(tab.sql_result.item(0, 0).text(), "4")

    def test_custom_query_is_never_overwritten_silently(self):
        tab = self.open(self.make_csv(rows=20, cols=3))
        custom = 'SELECT "col2", col1 FROM csv_data WHERE col0 > 3'
        tab.sql_input.setPlainText(custom)
        self.assertIn("personalizzata", tab.sql_mode_label.text())
        tab.hidden_columns.add("col0"); tab.apply_hidden_columns()
        self.drive_filter(tab, "col0", "gt", "1")
        self.assertEqual(tab.sql_input.toPlainText(), custom)              # filtri e colonne non la toccano
        self.box_answer = lambda b: QMessageBox.No if "Vuoi sostituirla" in b.informativeText() else QMessageBox.Yes
        self.boxes.clear()
        tab.delete_column_prompt("col2")
        settle(self.win)
        self.assertEqual(tab.sql_input.toPlainText(), custom)              # segnalata, non modificata
        self.assertFalse(tab.sql_notice_button.isHidden())
        self.assertTrue(any("Vuoi sostituirla" in b[1] for b in self.boxes))
        tab.sql_notice_button.click()                                      # proposta accettata esplicitamente
        self.assertIn("automatica", tab.sql_mode_label.text())
        self.assertNotEqual(tab.sql_input.toPlainText(), custom)
        self.assertTrue(tab.sql_notice_button.isHidden())


class TestResponsiveness(P3Ui):
    def test_ui_stays_responsive_while_saving_a_large_dataset(self):
        path = self.p("grande.csv")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("a,b,c,d\n" + "".join(f"{i},nome{i},{i * 1.5},testo lungo numero {i}\n" for i in range(600000)))
        tab = self.open(path)
        self.edit(tab)
        ticks = []
        timer = QTimer()
        timer.timeout.connect(lambda: ticks.append(time.time()))
        timer.start(10)
        self.drive_save(path=self.p("grande_copia.xlsx"), fmt="xlsx")
        self.box_click = 'Continua sul file corrente'; tab.save_as()
        seen_busy = pump(lambda: tab.is_busy(), 5)
        started = time.time()
        pump(lambda: not tab.is_busy() and not tab._ops, 120)
        timer.stop()
        settle(self.win)
        self.assertTrue(os.path.exists(self.p("grande_copia.xlsx")))
        during = [t for t in ticks if t >= started]
        self.assertGreater(len(during), 20, "il ciclo degli eventi si è fermato durante il salvataggio")
        import openpyxl
        ws = openpyxl.load_workbook(self.p("grande_copia.xlsx"), read_only=True)["Dati"]
        self.assertEqual(sum(1 for _ in ws.iter_rows()), 600001)


if __name__ == "__main__":
    unittest.main()
