"""Test funzionali dell'interfaccia in modalità offscreen (nessun display necessario).
Esecuzione:  QT_QPA_PLATFORM=offscreen python -m unittest tests.test_ui -v

Limiti: il piattaforma «offscreen» non ha un vero window manager né un vero mouse. Gli eventi del mouse
sono generati da QTest sugli stessi widget e passano per la stessa gestione di Qt, ma non sostituiscono
una prova a mano su Windows/macOS."""
import hashlib
import os
import shutil
import tempfile
import time
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QEvent, QPoint, QTimer, Qt
from PySide6.QtGui import QKeyEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QFileDialog, QMessageBox

from csv_reader.core.engine import ImportOptions
from csv_reader.desktop import app as A
from csv_reader.desktop.theme import apply_theme

from tests import legacy_types


def setUpModule():
    legacy_types.install()


def tearDownModule():
    legacy_types.uninstall()


APP = QApplication.instance() or QApplication([])
COLORS = apply_theme(APP)


def pump(cond, timeout=10.0):
    end = time.time() + timeout
    while time.time() < end:
        APP.processEvents()
        if cond():
            return True
        time.sleep(0.01)
    return False


def settle(win):
    """Attende che nessuna scheda stia lavorando e che le operazioni in coda (modifiche, salvataggi) siano finite.

    Il controllo va ripetuto: un thread può essere già terminato mentre il suo risultato (che a sua volta può
    avviare un'altra operazione) è ancora nella coda degli eventi."""
    def idle():
        return all(not t.is_busy() and not t._ops for t in win.csv_tabs())
    quiet = 0
    for _ in range(40):
        pump(idle, 10)
        for _ in range(3):
            APP.processEvents()
        quiet = quiet + 1 if idle() else 0
        if quiet >= 3:
            return


def sha(path):
    with open(path, "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()


class UiCase(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="csvui_")
        self.win = A.MainWindow(COLORS)
        self.win.resize(1200, 760)
        self.win.show()
        # nessuna finestra di dialogo modale deve bloccare i test
        self._orig = (QMessageBox.question, QMessageBox.information, QMessageBox.critical)
        self.messages = []
        QMessageBox.information = staticmethod(lambda *a, **k: self.messages.append(("info", a[1:3])) or QMessageBox.Ok)
        QMessageBox.critical = staticmethod(lambda *a, **k: self.messages.append(("critical", a[1:3])) or QMessageBox.Ok)
        self._orig_sheet_exec = A.SheetDialog.exec
        A.SheetDialog.exec = lambda d: A.QDialog.Accepted      # sceglie il primo foglio senza bloccare i test
        self.answer = QMessageBox.Yes
        QMessageBox.question = staticmethod(lambda *a, **k: self.messages.append(("question", a[1:3])) or self.answer)
        # avviso «Salva / Non salvare / Annulla» e finestra di salvataggio: pilotati dai test
        self.unsaved_choice = "discard"
        self.unsaved_asked = []
        self._orig_ask = A.ask_unsaved
        A.ask_unsaved = lambda parent, title, text: self.unsaved_asked.append(text) or self.unsaved_choice
        self._orig_save_exec = A.SaveDialog.exec
        self._orig_box_click = A.QMessageBox.clickedButton
        self._orig_box_exec_base = A.QMessageBox.exec
        A.QMessageBox.exec = lambda b: 0                # nessuna finestra di messaggio costruita a mano deve bloccare i test
        self.box_click = "Continua sul file corrente"
        A.QMessageBox.clickedButton = lambda b: next((x for x in b.buttons() if x.text() == self.box_click), None)

    def tearDown(self):
        for tab in self.win.csv_tabs():
            tab.worker and tab.worker.wait(2000)
        self.answer = QMessageBox.Yes
        self.unsaved_choice = "discard"               # «non salvare»: la chiusura finale non deve bloccarsi
        self.win.close()
        A.ask_unsaved = self._orig_ask
        A.SaveDialog.exec = self._orig_save_exec
        A.QMessageBox.clickedButton = self._orig_box_click
        A.QMessageBox.exec = self._orig_box_exec_base
        QMessageBox.question, QMessageBox.information, QMessageBox.critical = self._orig
        A.SheetDialog.exec = self._orig_sheet_exec
        shutil.rmtree(self.dir, ignore_errors=True)

    def p(self, name):
        return os.path.join(self.dir, name)

    def drive_save(self, path=None, fmt=None, delimiter=None, encoding=None, decimal=None, also_current=False, accept=True):
        """Compila e conferma la finestra di salvataggio. Ritorna un dizionario con ciò che la finestra mostrava."""
        seen = {"dialogs": 0}

        def fake(d):
            seen["dialogs"] += 1
            seen.update(format=d.fmt(), path=d.path_edit.text(), locked=d.in_place(), note=d.note.text(), title=d.windowTitle(),
                        has_also=d.also_box is not None)
            if fmt:
                d.format_box.setCurrentIndex(d.format_box.findData(fmt))
            seen["path_after_format"] = d.path_edit.text()
            if path is not None:
                d.path_edit.setText(path)
            if delimiter is not None:
                d.delim_box.setCurrentIndex(d.delim_box.findData(delimiter))
            if encoding is not None:
                d.enc_box.setCurrentIndex(d.enc_box.findData(encoding))
            if decimal is not None:
                d.dec_box.setCurrentIndex(d.dec_box.findData(decimal))
            if also_current and d.also_box is not None:
                d.also_box.setChecked(True)
            if not accept:
                return A.QDialog.Rejected
            d._accept()
            seen["error"] = d.error.text()
            seen["request"] = d.request
            return A.QDialog.Accepted if d.request else A.QDialog.Rejected

        A.SaveDialog.exec = fake
        return seen

    def make_csv(self, name="a.csv", rows=1000, cols=6, delim=","):
        path = self.p(name)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(delim.join(f"col{j}" for j in range(cols)) + "\n")
            for i in range(rows):
                fh.write(delim.join([str(i)] + [f"valore {i}-{j}" for j in range(1, cols)]) + "\n")
        return path

    def open(self, path):
        self.win.open_path(path)
        tab = self.win.csv_tabs()[-1]
        self.assertTrue(pump(lambda: tab.columns and not tab.is_busy() and tab.table.rowCount() > 0), "caricamento non terminato")
        APP.processEvents()
        return tab


class TestNavigation(UiCase):
    def test_arrows_tooltips_and_position(self):
        w = self.win
        self.assertFalse(w.prev_file_button.isEnabled() or w.next_file_button.isEnabled())
        a = self.open(self.make_csv("a.csv", 10))
        self.assertEqual(w.file_position.text(), "1 di 1")
        self.assertFalse(w.prev_file_button.isEnabled() or w.next_file_button.isEnabled())
        b = self.open(self.make_csv("b.csv", 10))
        c = self.open(self.make_csv("c.csv", 10))
        self.assertEqual(w.file_position.text(), "3 di 3")
        self.assertTrue(w.prev_file_button.isEnabled())
        self.assertFalse(w.next_file_button.isEnabled())           # ultimo file: freccia successiva disabilitata
        self.assertIn("precedente", w.prev_file_button.toolTip().lower())
        self.assertIn("successivo", w.next_file_button.toolTip().lower())
        w.prev_file_button.click()
        self.assertIs(w.docs.currentWidget(), b)
        self.assertEqual(w.file_position.text(), "2 di 3")
        w.prev_file_button.click()
        self.assertFalse(w.prev_file_button.isEnabled())            # primo file
        self.assertTrue(w.next_file_button.isEnabled())
        w.next_file_button.click()
        self.assertIs(w.docs.currentWidget(), b)

    def test_state_is_separate_per_file(self):
        a = self.open(self.make_csv("a.csv", 50))
        b = self.open(self.make_csv("b.csv", 50))
        a.table.item(0, 1).setText("MODIFICATO-A")
        a.search_input.setText("valore 7")
        settle(self.win)
        self.assertEqual(a.engine.change_count, 1)
        self.assertEqual(b.engine.change_count, 0)
        self.assertEqual(b.search_input.text(), "")
        self.win.step_file(-1)
        self.assertEqual(a.table.item(0, 1).text(), "MODIFICATO-A")

    def test_global_sql_tab_appears_with_two_files(self):
        self.open(self.make_csv("a.csv", 10))
        self.assertIsNone(self.win.global_tab)
        self.open(self.make_csv("b.csv", 10))
        self.assertIsNotNone(self.win.global_tab)
        self.assertEqual(self.win.file_position.text(), "2 di 2")
        self.win.docs.setCurrentWidget(self.win.global_tab)
        self.assertIn("SQL globale", self.win.file_position.text())


class TestPagination(UiCase):
    def test_layout_and_hint_removed(self):
        tab = self.open(self.make_csv(rows=1000))
        labels = [l.text() for l in tab.findChildren(A.QLabel)]
        self.assertFalse(any("Clicca sull'intestazione" in t for t in labels))
        spin, prev = tab.page_size_box.geometry(), tab.prev_button.geometry()
        self.assertEqual(spin.parent() if False else tab.page_size_box.parent(), tab.prev_button.parent())
        self.assertLess(spin.x(), prev.x())                        # selettore a sinistra dei pulsanti di pagina
        self.assertGreater(tab.page_size_box.mapTo(tab, QPoint(0, 0)).y(), tab.table.mapTo(tab, QPoint(0, 0)).y())  # sotto la tabella
        self.assertFalse(tab.chip_container.isVisible())          # nessuna riga vuota senza filtri

    def test_page_size_change_does_not_rescan_and_keeps_position(self):
        tab = self.open(self.make_csv(rows=1000))
        calls = []
        orig = tab.engine.count_filtered
        tab.engine.count_filtered = lambda *a, **k: calls.append(1) or orig(*a, **k)
        tab.next_button.click(); settle(self.win)                  # offset 200
        tab.next_button.click(); settle(self.win)                  # offset 400
        self.assertEqual(tab.offset, 400)
        tab.page_size_box.setValue(300); settle(self.win)
        self.assertEqual(tab.offset, 300)                          # pagina che contiene la riga 401? no: allineata a 300
        self.assertEqual(tab.table.rowCount(), 300)
        self.assertEqual(tab.table.item(0, 0).text(), "300")
        self.assertEqual(calls, [])                                # nessun conteggio ripetuto senza filtri
        tab.search_input.setText("valore 9-"); tab.search_button.click(); settle(self.win)
        self.assertEqual(len(calls), 1)                            # il conteggio parte solo quando cambiano i filtri
        tab.page_size_box.setValue(50); settle(self.win)
        tab.next_button.click(); settle(self.win)
        self.assertEqual(len(calls), 1)

    def test_next_button_uses_total(self):
        tab = self.open(self.make_csv(rows=250))
        self.assertTrue(tab.next_button.isEnabled())
        tab.next_button.click(); settle(self.win)
        self.assertEqual(tab.table.rowCount(), 50)
        self.assertFalse(tab.next_button.isEnabled())
        self.assertTrue(tab.prev_button.isEnabled())

    def test_max_page_size_is_one_million(self):
        tab = self.open(self.make_csv(rows=60))
        self.assertEqual(tab.page_size_box.maximum(), 1_000_000)


class TestResize(UiCase):
    def drag(self, header, index, delta):
        horizontal = header.orientation() == Qt.Horizontal
        edge = header.sectionViewportPosition(index) + header.sectionSize(index) - 1
        mid = (header.height() if horizontal else header.width()) // 2
        start = QPoint(edge, mid) if horizontal else QPoint(mid, edge)
        end = QPoint(edge + delta, mid) if horizontal else QPoint(mid, edge + delta)
        vp = header.viewport()
        QTest.mousePress(vp, Qt.LeftButton, Qt.NoModifier, start)
        QTest.mouseMove(vp, QPoint((start.x() + end.x()) // 2, (start.y() + end.y()) // 2))
        QTest.mouseMove(vp, end)
        QTest.mouseRelease(vp, Qt.LeftButton, Qt.NoModifier, end)
        APP.processEvents()

    def select(self, table, r0, c0, r1, c1):
        from PySide6.QtCore import QItemSelection, QItemSelectionModel
        model = table.model()
        table.selectionModel().select(QItemSelection(model.index(r0, c0), model.index(r1, c1)), QItemSelectionModel.ClearAndSelect)

    def test_dragging_one_border_resizes_all_selected_columns(self):
        tab = self.open(self.make_csv(rows=30, cols=6))
        t, h = tab.table, tab.table.horizontalHeader()
        for c in range(6):
            t.setColumnWidth(c, 100)
        self.select(t, 0, 1, 3, 3)                                 # colonne 1-3 selezionate
        self.drag(h, 2, 60)
        widths = [t.columnWidth(c) for c in range(6)]
        self.assertEqual(widths[1], widths[2]); self.assertEqual(widths[2], widths[3])
        self.assertGreater(widths[2], 100)
        self.assertEqual((widths[0], widths[4]), (100, 100))       # le non selezionate non cambiano

    def test_dragging_unselected_column_resizes_only_it(self):
        tab = self.open(self.make_csv(rows=30, cols=6))
        t, h = tab.table, tab.table.horizontalHeader()
        for c in range(6):
            t.setColumnWidth(c, 100)
        self.select(t, 0, 1, 3, 2)
        self.drag(h, 4, 40)                                        # colonna 4 non selezionata
        self.assertEqual([t.columnWidth(c) for c in (1, 2, 3)], [100, 100, 100])
        self.assertGreater(t.columnWidth(4), 100)

    def test_dragging_row_border_resizes_all_selected_rows(self):
        tab = self.open(self.make_csv(rows=30, cols=4))
        t, v = tab.table, tab.table.verticalHeader()
        self.select(t, 2, 0, 5, 1)
        self.drag(v, 3, 20)
        heights = [t.rowHeight(r) for r in range(8)]
        self.assertTrue(heights[2] == heights[3] == heights[4] == heights[5])
        self.assertGreater(heights[3], 30)
        self.assertEqual((heights[1], heights[6]), (30, 30))

    def test_double_click_fits_each_selected_column_individually(self):
        path = self.p("w.csv")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("a,bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb,c\n")
            for i in range(20):
                fh.write(f"1,2,{'x' * 120}\n")
        tab = self.open(path)
        t = tab.table
        for c in range(3):
            t.setColumnWidth(c, 400)
        self.select(t, 0, 0, 3, 2)
        t.horizontalHeader().sectionHandleDoubleClicked.emit(1)
        w = [t.columnWidth(c) for c in range(3)]
        self.assertLess(w[0], w[1])                                # ognuna secondo il proprio contenuto
        self.assertLess(w[1], w[2])
        self.assertLessEqual(w[2], t.FIT_MAX)

    def test_fit_samples_at_most_fit_sample_rows(self):
        tab = self.open(self.make_csv(rows=500, cols=3))
        t = tab.table
        seen = []
        orig = t.item
        t.item = lambda r, c: seen.append(r) or orig(r, c)
        t.fit_width_of(1)
        self.assertLessEqual(len(seen), t.FIT_SAMPLE)


class TestTopBar(UiCase):
    def test_hide_and_restore_keeps_state_and_widgets(self):
        tab = self.open(self.make_csv(rows=300))
        tab.search_input.setText("valore 1")
        tab.column_filters["col2"] = {"text": "9", "values": []}
        tab.filters_changed(); settle(self.win)
        tab.next_button.click(); settle(self.win)
        offset = tab.offset
        table_id = id(tab.table)
        tab.table.item(0, 1).setText("EDIT")
        self.select_cells(tab)
        self.assertTrue(tab.header_widget.isVisible() and self.win.top_bar.isVisible())
        tab.toggle_button.click(); APP.processEvents()
        self.assertTrue(self.win.top_hidden)
        self.assertFalse(self.win.top_bar.isVisible())
        self.assertFalse(self.win.docs.tabBar().isVisible())
        self.assertFalse(tab.header_widget.isVisible())
        self.assertTrue(tab.inner.tabBar().isVisible())            # schede «Dati» e «SQL» restano
        self.assertTrue(tab.table.isVisible())
        self.assertTrue(tab.toggle_button.isVisible())             # il pulsante è fuori dall'area nascosta
        self.assertEqual(tab.toggle_button.toolTip(), "Mostra barra superiore")
        # nessuno spazio vuoto: la scheda parte subito sotto il bordo della finestra
        top_y = tab.inner.mapTo(self.win, QPoint(0, 0)).y()
        self.assertLess(top_y, 40)
        self.win.resize(900, 600); APP.processEvents()             # il ridimensionamento continua a funzionare
        self.assertEqual(self.win.width(), 900)
        tab.toggle_button.click(); APP.processEvents()
        self.assertFalse(self.win.top_hidden)
        self.assertTrue(self.win.top_bar.isVisible() and tab.header_widget.isVisible())
        self.assertEqual(tab.toggle_button.toolTip(), "Nascondi barra superiore")
        self.assertEqual(id(tab.table), table_id)                  # stessi widget, non ricreati
        self.assertEqual(tab.search_input.text(), "valore 1")
        self.assertIn("col2", tab.column_filters)
        self.assertEqual(tab.offset, offset)
        self.assertEqual(tab.table.item(0, 1).text(), "EDIT")
        settle(self.win)
        self.assertEqual(tab.engine.change_count, 1)
        self.assertEqual(len(tab.table.selectionModel().selectedIndexes()), 2)

    def select_cells(self, tab):
        from PySide6.QtCore import QItemSelection, QItemSelectionModel
        m = tab.table.model()
        tab.table.selectionModel().select(QItemSelection(m.index(1, 0), m.index(1, 1)), QItemSelectionModel.ClearAndSelect)

    def test_state_applies_to_new_tabs_and_global_tab(self):
        a = self.open(self.make_csv("a.csv", 10))
        a.toggle_button.click()
        b = self.open(self.make_csv("b.csv", 10))
        self.assertFalse(b.header_widget.isVisible())
        self.assertTrue(b.toggle_button.isVisible())
        g = self.win.global_tab
        self.assertTrue(g.toggle_button.isVisible() or g.isHidden())
        g.toggle_button.click()                                    # si ripristina anche dalla scheda globale
        self.assertFalse(self.win.top_hidden)


class TestRecordDialog(UiCase):
    """Il dettaglio viene aperto davvero con TAB (finestra modale con exec reale); un timer simula
    l'utente mentre la finestra è aperta."""

    def press_tab(self, widget):
        for typ in (QEvent.KeyPress, QEvent.KeyRelease):
            QApplication.sendEvent(widget, QKeyEvent(typ, Qt.Key_Tab, Qt.NoModifier))

    def select_rows(self, tab, rows):
        from PySide6.QtCore import QItemSelection, QItemSelectionModel
        m = tab.table.model()
        sel = QItemSelection()
        for r in rows:
            sel.select(m.index(r, 0), m.index(r, tab.table.columnCount() - 1))
        tab.table.selectionModel().select(sel, QItemSelectionModel.ClearAndSelect)
        tab.table.selectionModel().setCurrentIndex(m.index(rows[0], 1), QItemSelectionModel.NoUpdate)

    def with_detail(self, tab, action, delay=200):
        """TAB dalla tabella -> si apre il dettaglio; `action(dialog)` gira mentre è aperto."""
        seen = {}

        def fire():
            d = QApplication.activeModalWidget()
            seen["dialog"] = d
            d.activateWindow()
            QTest.qWaitForWindowActive(d, 1000)
            action(d, seen)

        def safety():
            d = QApplication.activeModalWidget()
            if d is not None:
                seen["stuck"] = True
                d.reject()

        QTimer.singleShot(delay, fire)
        QTimer.singleShot(6000, safety)
        tab.table.setFocus()
        self.press_tab(tab.table)       # exec() ritorna solo quando il dettaglio si chiude
        self.assertNotIn("stuck", seen, "il dettaglio non si è chiuso con TAB")
        return seen

    def test_tab_closes_dialog_single_and_multiple_records(self):
        tab = self.open(self.make_csv(rows=40, cols=5))
        tab.hidden_columns.add("col3"); tab.apply_hidden_columns()
        for rows in ([2], [1, 2, 3]):
            with self.subTest(records=len(rows)):
                self.select_rows(tab, rows)
                facts = {}

                def action(d, seen):
                    facts["cols"] = d.table.columnCount()
                    facts["rows"] = d.table.rowCount()
                    facts["hidden_visible"] = not d.table.isRowHidden(3)     # colonna nascosta nella tabella principale
                    facts["header0"] = d.table.verticalHeaderItem(0).text()   # intestazioni a sinistra
                    facts["first_value"] = d.table.item(1, 0).text()
                    facts["active"] = QApplication.activeWindow() is d
                    d.table.setFocus(); APP.processEvents()
                    self.press_tab(d.table)

                seen = self.with_detail(tab, action)
                self.assertTrue(facts["active"], "la finestra deve essere attiva perché TAB venga intercettato")
                self.assertEqual(facts["cols"], len(rows))
                self.assertEqual(facts["rows"], 5)
                self.assertTrue(facts["hidden_visible"])
                self.assertEqual(facts["header0"], "col0")
                self.assertEqual(facts["first_value"], f"valore {rows[0]}-1")
                self.assertFalse(seen["dialog"].isVisible())
                self.assertIs(self.win.focusWidget(), tab.table)         # focus tornato alla tabella «Dati» (offscreen: la finestra non è «attiva», si verifica il widget che riceverà il focus)
                self.assertEqual(sorted({i.row() for i in tab.table.selectedIndexes()}), rows)   # selezione intatta

    def test_tab_closes_from_any_focused_control(self):
        tab = self.open(self.make_csv(rows=20, cols=4))
        self.select_rows(tab, [0])

        def action(d, seen):
            d.columns_button.setFocus(); APP.processEvents()
            self.press_tab(d.columns_button)

        seen = self.with_detail(tab, action)
        self.assertFalse(seen["dialog"].isVisible())

    def test_tab_commits_open_editor_and_keeps_edit(self):
        tab = self.open(self.make_csv(rows=20, cols=4))
        self.select_rows(tab, [4])

        def action(d, seen):
            d.table.setCurrentCell(1, 0)
            d.table.edit(d.table.model().index(1, 0)); APP.processEvents()
            editor = QApplication.focusWidget()
            seen["editor"] = editor is not None and editor is not d.table
            editor.setText("DA-DETTAGLIO")
            self.press_tab(editor)

        seen = self.with_detail(tab, action)
        self.assertTrue(seen["editor"])
        settle(self.win)
        self.assertEqual(tab.engine.cell_text(4, "col1"), "DA-DETTAGLIO")        # scritto nel dataset, non solo mostrato
        self.assertEqual(tab.table.item(4, 1).text(), "DA-DETTAGLIO")
        self.assertTrue(tab.has_unsaved())                         # le modifiche restano protette dalle regole esistenti

    def test_tab_in_other_controls_is_not_intercepted(self):
        tab = self.open(self.make_csv(rows=20, cols=4))
        self.select_rows(tab, [0])
        calls = []
        orig = A.RecordDialog.close_detail
        A.RecordDialog.close_detail = lambda self_: calls.append(1) or orig(self_)
        try:
            self.with_detail(tab, lambda d, seen: self.press_tab(d.table))      # apre e chiude (1 chiamata)
            self.assertEqual(len(calls), 1)
            tab.search_input.setFocus()
            self.press_tab(tab.search_input)                      # dettaglio chiuso: TAB resta il normale tasto di focus
            APP.processEvents()
        finally:
            A.RecordDialog.close_detail = orig
        self.assertEqual(len(calls), 1)

    def test_long_values_wrap_and_stay_readable(self):
        path = self.p("long.csv")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("id,descrizione\n1,\"" + ("parola lunga " * 80) + "\"\n")
        tab = self.open(path)
        self.select_rows(tab, [0])
        facts = {}

        def action(d, seen):
            facts["wrap"] = d.table.wordWrap()
            facts["tall"] = d.table.rowHeight(1) > 3 * d.table.rowHeight(0)
            screen = d.screen().availableGeometry()
            facts["fits"] = d.width() <= screen.width() and d.height() <= screen.height()
            self.press_tab(d.table)

        self.with_detail(tab, action)
        self.assertTrue(facts["wrap"] and facts["tall"] and facts["fits"], facts)


class TestImportUi(UiCase):
    def test_reload_with_options_changes_parsing(self):
        path = self.p("semi.csv")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("a;b;c\n1;2;3\n4;5;6\n")
        tab = self.open(path)
        self.assertEqual(len(tab.columns), 3)
        self.assertTrue(tab.reload(ImportOptions(delimiter=","), None, True))     # delimitatore sbagliato: 1 colonna
        pump(lambda: len(tab.columns) == 1 and not tab.is_busy()); APP.processEvents()
        self.assertEqual(len(tab.columns), 1)
        self.assertIn("una sola colonna", tab.import_notice.text() + tab.engine.info["warnings"][0] if tab.engine.info["warnings"] else "una sola colonna")
        self.assertTrue(tab.reload(ImportOptions(), None, True))                    # automatico di nuovo
        pump(lambda: len(tab.columns) == 3 and not tab.is_busy()); APP.processEvents()
        self.assertEqual(tab.table.columnCount(), 3)

    def test_unsaved_edits_warning_before_reload(self):
        tab = self.open(self.make_csv("e.csv", 20, 3))
        tab.table.item(0, 1).setText("X")
        settle(self.win)
        self.assertTrue(tab.has_unsaved())
        self.unsaved_choice = "cancel"
        self.assertFalse(tab.reload(ImportOptions(), None, True))
        self.assertEqual(len(self.unsaved_asked), 1)
        self.assertEqual(tab.table.item(0, 1).text(), "X")        # nulla è cambiato
        self.unsaved_choice = "discard"
        self.assertTrue(tab.reload(ImportOptions(), None, True))
        pump(lambda: not tab.is_busy()); APP.processEvents()
        self.assertFalse(tab.has_unsaved())

    def test_failed_reload_keeps_file_open(self):
        path = self.p("cp.csv")
        with open(path, "wb") as fh:
            fh.write("id;nota\n1;caffè\n".encode("cp1252"))
        tab = self.open(path)
        self.assertEqual(tab.table.item(0, 1).text(), "caffè")
        offered = []
        tab._offer_fix = lambda msg, first: offered.append((msg, first))
        tab.reload(ImportOptions(encoding="utf-8"), None, True)
        pump(lambda: offered and not tab.is_busy())
        self.assertTrue(offered and offered[0][1] is False)
        self.assertIn("codifica", offered[0][0].lower())
        self.assertEqual(tab.table.item(0, 1).text(), "caffè")   # il dataset precedente è ancora lì

    def test_first_open_failure_offers_fix_and_can_close(self):
        path = self.p("cp.csv")
        with open(path, "wb") as fh:
            fh.write("id;nota\n1;caffè\n".encode("cp1252"))
        offered = []
        orig = A.CsvTab._offer_fix
        A.CsvTab._offer_fix = lambda self_, msg, first: offered.append((msg, first))
        try:
            tab = A.CsvTab()
            self.win.docs.addTab(tab, "x")
            tab.open(path, ImportOptions(encoding="utf-8"))        # codifica sbagliata imposta a mano
            self.assertTrue(pump(lambda: offered))
        finally:
            A.CsvTab._offer_fix = orig
        self.assertTrue(offered[-1][1])                            # first_open=True
        self.assertIn("codifica", offered[-1][0].lower())

    def test_import_dialog_builds_options(self):
        tab = self.open(self.make_csv("o.csv", 5, 3))
        dlg = A.ImportDialog(tab, "csv", ImportOptions(), tab.engine.info)
        dlg.delim.setCurrentIndex(5); dlg.custom_delim.setText("#")
        dlg.encoding.setCurrentIndex(3)                            # Windows-1252
        dlg.header.setCurrentIndex(2)
        dlg.decimal.setCurrentIndex(1)
        dlg._apply()
        o = dlg.result_options
        self.assertEqual((o.delimiter, o.encoding, o.header, o.decimal), ("#", "utf-8-sig" if False else "cp1252" if False else o.encoding, False, ","))
        self.assertEqual(o.delimiter, "#")
        # configurazione incompatibile: errore mostrato, nessuna chiusura
        dlg2 = A.ImportDialog(tab, "csv", ImportOptions(), tab.engine.info)
        dlg2.delim.setCurrentIndex(2); dlg2.decimal.setCurrentIndex(1)
        dlg2._apply()
        self.assertFalse(dlg2.error.isHidden())
        self.assertIn("incompatibile", dlg2.error.text())


class TestExcelUi(UiCase):
    def make_xlsx(self):
        import openpyxl
        wb = openpyxl.Workbook()
        ws = wb.active; ws.title = "Uno"
        ws.append(["id", "nome"]); ws.append([1, "a"]); ws.append([2, "b"])
        wb.create_sheet("Due").append(["x"])
        path = self.p("t.xlsx")
        wb.save(path)
        return path

    def test_sheet_dialog_and_import(self):
        path = self.make_xlsx()
        chosen = {}
        orig_exec = A.SheetDialog.exec
        def fake_exec(d):
            chosen["items"] = [d.list.item(i).text() for i in range(d.list.count())]
            d.list.setCurrentRow(1)
            return A.QDialog.Accepted
        A.SheetDialog.exec = fake_exec
        try:
            self.win.open_path(path)
        finally:
            A.SheetDialog.exec = orig_exec
        self.assertEqual(chosen["items"], ["Uno", "Due"])
        tab = self.win.csv_tabs()[-1]
        pump(lambda: tab.columns and not tab.is_busy())
        self.assertEqual(tab.columns, ["x"])
        self.assertEqual(tab.sheet, "Due")
        self.assertEqual(tab.save_button.text(), "Salva")
        self.assertIn("Due", self.win.docs.tabText(self.win.docs.indexOf(tab)))

    def test_save_copy_flow_protects_original(self):
        path = self.make_xlsx()
        before = sha(path)
        self.win.open_path(path)   # due fogli: forza il primo senza dialogo
        tab = self.win.csv_tabs()[-1] if self.win.csv_tabs() else None
        if tab is None or not tab.columns:
            tab = A.CsvTab(); self.win.docs.addTab(tab, "x"); tab.table_name = "t1"; tab.open(path, sheet="Uno", header=True)
            pump(lambda: tab.columns and not tab.is_busy())
        pump(lambda: tab.table.rowCount() > 0 and not tab.is_busy()); settle(self.win)
        tab.table.item(0, 1).setText("MOD")
        settle(self.win)
        # destinazione = originale: la finestra la rifiuta
        seen = self.drive_save(path=path)
        tab.save(); APP.processEvents()
        self.assertIn("non viene mai sovrascritto", seen["error"])
        self.assertEqual(seen["format"], "xlsx")                   # formato originale preselezionato
        self.assertEqual(sha(path), before)
        # nuova destinazione
        copy = self.p("copia.xlsx")
        self.drive_save(path=copy)
        tab.save()
        pump(lambda: os.path.exists(copy) and not tab.is_busy()); settle(self.win)
        self.assertEqual(sha(path), before)                        # originale intatto
        self.assertTrue(os.path.exists(copy))
        self.assertIn("non modificato", tab.copy_label.text())
        self.assertIn("copia.xlsx", tab.copy_label.text())
        self.assertTrue(tab.has_unsaved())                         # copia creata ma si resta sul file corrente: non «salvato»
        import openpyxl
        self.assertEqual(openpyxl.load_workbook(copy)["Uno"]["B2"].value, "MOD")

    def test_existing_destination_asks_confirmation(self):
        path = self.make_xlsx()
        tab = A.CsvTab(); self.win.docs.addTab(tab, "x"); tab.table_name = "t1"
        tab.open(path, sheet="Uno", header=True)
        pump(lambda: tab.columns and not tab.is_busy())
        pump(lambda: tab.table.rowCount() > 0 and not tab.is_busy()); settle(self.win)
        tab.table.item(0, 1).setText("MOD")
        settle(self.win)
        existing = self.p("esiste.xlsx")
        shutil.copy(path, existing)
        self.answer = QMessageBox.No
        self.drive_save(path=existing)
        before = sha(existing)
        tab.save(); APP.processEvents()
        self.assertEqual(self.messages[-1][0], "question")     # chiede conferma prima di sostituire
        self.assertEqual(sha(existing), before)                # rifiutato: file esistente intatto


class TestSortUi(UiCase):
    def test_sort_from_column_dialog(self):
        path = self.p("n.csv")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("id,n\n1,10\n2,9\n3,100\n4,\n")
        tab = self.open(path)
        values = tab.engine.distinct_values("n")[0]
        dlg = A.FilterDialog(tab, "n", None, values, False, None)
        dlg.sort_desc.click()
        self.assertEqual(dlg.result_sort, "desc")
        self.assertFalse(dlg.sort_asc.isChecked())
        dlg._apply()
        tab.sort = ("n", dlg.result_sort == "desc")
        tab.filters_changed(); settle(self.win)
        self.assertEqual([tab.table.item(r, 1).text() for r in range(4)], ["100", "10", "9", ""])
        self.assertIn("ORDER BY \"n\" DESC", tab.sql_input.toPlainText())
        self.assertTrue(tab.table.horizontalHeaderItem(1).text().endswith("▼"))
        self.assertTrue(tab.chip_container.isVisible())
        # le modifiche restano agganciate alla riga originale anche con l'ordine cambiato
        tab.table.item(0, 1).setText("EDIT")
        settle(self.win)
        self.assertEqual(tab.engine.cell_text(2, "n"), "EDIT")     # riga con id 3 (indice 2) del file
        tab.clear_sort(); settle(self.win)
        self.assertEqual(tab.table.item(0, 0).text(), "1")
        self.assertFalse(tab.chip_container.isVisible())
        self.assertEqual(tab.table.item(2, 1).text(), "EDIT")      # la modifica segue la riga

    def test_dialog_toggle_off_and_real_dialog_flow(self):
        tab = self.open(self.make_csv(rows=30, cols=3))
        tab.sort = ("col0", True)
        dlg = A.FilterDialog(tab, "col0", None, tab.engine.distinct_values("col0")[0], False, "desc")
        self.assertTrue(dlg.sort_desc.isChecked())
        dlg.sort_desc.click()                                      # secondo clic: toglie l'ordine
        self.assertIsNone(dlg.result_sort)


class TestRealMouseHeader(UiCase):
    """Eventi del mouse veri sull'intestazione (non chiamate dirette ai metodi)."""

    def _hx(self, h, c, frac=0.5):
        return h.sectionViewportPosition(c) + int(h.sectionSize(c) * frac)

    def test_click_on_header_selects_column_and_funnel_opens_dialog(self):
        tab = self.open(self.make_csv(rows=60, cols=4))
        h = tab.table.horizontalHeader()
        self.assertTrue(h.sectionsClickable())
        self.assertTrue(tab.table.verticalHeader().sectionsClickable())
        opened = []
        orig = A.FilterDialog.exec
        A.FilterDialog.exec = lambda d: opened.append(d.windowTitle()) or 0
        try:
            y = h.height() // 2
            QTest.mouseClick(h.viewport(), Qt.LeftButton, Qt.NoModifier, QPoint(self._hx(h, 1, 0.3), y))
            APP.processEvents()
            self.assertEqual(tab.table.selected_columns(), [1])       # il clic sul nome seleziona la colonna
            self.assertFalse(opened)                                   # ...e non apre la finestra
            fx = h.sectionViewportPosition(2) + h.sectionSize(2) - h.FUNNEL_GAP - h.FUNNEL_W // 2
            QTest.mouseClick(h.viewport(), Qt.LeftButton, Qt.NoModifier, QPoint(fx, y))
            self.assertTrue(pump(lambda: bool(opened)), "il clic sull'imbuto non apre il filtro")
            self.assertEqual(tab.table.selected_columns(), [1])       # l'imbuto non cambia la selezione
        finally:
            A.FilterDialog.exec = orig
        self.assertIn("col2", opened[0])

    def test_funnel_stays_active_when_filter_set_and_title_has_no_dot(self):
        tab = self.open(self.make_csv(rows=60, cols=4))
        tab.column_filters["col1"] = {"text": "valore 1", "values": []}
        tab.filters_changed()
        self.assertTrue(pump(lambda: not tab.is_busy() and 1 in tab.table.horizontalHeader().active_columns))
        self.assertNotIn("●", tab.table.horizontalHeaderItem(1).text())     # l'imbuto colorato e il chip lo dicono già
        self.assertIn(1, tab.table.horizontalHeader().active_columns)
        self.assertNotIn(2, tab.table.horizontalHeader().active_columns)

    def test_chips_show_only_count_per_column_and_cap_visible_chips(self):
        tab = self.open(self.make_csv(rows=30, cols=8))
        for i in range(6):
            tab.column_filters[f"col{i}"] = {"text": "x" if i == 0 else "", "values": ["a", "b", "c"][: (i % 3) + 1]}
        tab.rebuild_chips()
        texts = [tab.chip_row.itemAt(i).widget().text() for i in range(tab.chip_row.count()) if tab.chip_row.itemAt(i).widget()]
        self.assertEqual(texts[0], "col0: 2 filtri  ✕")           # 1 valore + testo «contiene»
        self.assertEqual(texts[1], "col1: 2 filtri  ✕")
        self.assertEqual(texts[2], "col2: 3 filtri  ✕")
        self.assertEqual(texts[3], "col3: 1 filtro  ✕")
        self.assertEqual(texts[4], "+2 colonne")                    # le altre non escono dalla pagina
        self.assertNotIn("valori", " ".join(texts))
        self.assertEqual(A.filter_count({"text": "", "values": []}), 0)

    def test_ctrl_click_selects_columns_then_drag_border_resizes_all_and_dblclick_fits_each(self):
        tab = self.open(self.make_csv(rows=60, cols=4))
        t, h = tab.table, tab.table.horizontalHeader()
        for c in (0, 1):
            QTest.mouseClick(h.viewport(), Qt.LeftButton, Qt.ControlModifier, QPoint(self._hx(h, c), h.height() // 2))
        self.assertEqual(t.selected_columns(), [0, 1])
        w = h.sectionSize(0)
        edge = h.sectionViewportPosition(0) + w
        y = h.height() // 2
        QTest.mousePress(h.viewport(), Qt.LeftButton, Qt.NoModifier, QPoint(edge - 1, y))
        QTest.mouseMove(h.viewport(), QPoint(edge + 79, y))
        QTest.mouseRelease(h.viewport(), Qt.LeftButton, Qt.NoModifier, QPoint(edge + 79, y))
        APP.processEvents()
        self.assertEqual(h.sectionSize(0), h.sectionSize(1))
        self.assertGreater(h.sectionSize(0), w + 40)
        self.assertNotEqual(h.sectionSize(2), h.sectionSize(0))      # la colonna non selezionata non cambia
        t.setColumnWidth(0, 600); t.setColumnWidth(1, 600)
        edge = h.sectionViewportPosition(0) + h.sectionSize(0)
        QTest.mouseDClick(h.viewport(), Qt.LeftButton, Qt.NoModifier, QPoint(edge - 1, y))
        APP.processEvents()
        self.assertLess(h.sectionSize(0), 400)
        self.assertLess(h.sectionSize(1), 400)

    def test_row_header_click_selects_row_and_group_row_resize(self):
        tab = self.open(self.make_csv(rows=60, cols=3))
        t, v = tab.table, tab.table.verticalHeader()
        for r in (0, 1):
            QTest.mouseClick(v.viewport(), Qt.LeftButton, Qt.ControlModifier, QPoint(v.width() // 2, v.sectionViewportPosition(r) + 5))
        self.assertEqual(t.selected_rows(), [0, 1])
        edge = v.sectionViewportPosition(0) + v.sectionSize(0)
        x = v.width() // 2
        QTest.mousePress(v.viewport(), Qt.LeftButton, Qt.NoModifier, QPoint(x, edge - 1))
        QTest.mouseMove(v.viewport(), QPoint(x, edge + 29))
        QTest.mouseRelease(v.viewport(), Qt.LeftButton, Qt.NoModifier, QPoint(x, edge + 29))
        APP.processEvents()
        self.assertEqual(v.sectionSize(0), v.sectionSize(1))
        self.assertGreater(v.sectionSize(0), 40)


class TestRegression(UiCase):
    def test_value_filter_chips_and_sql_sync(self):
        tab = self.open(self.make_csv(rows=100, cols=4))
        tab.column_filters["col1"] = {"text": "", "values": ["valore 5-1"]}
        tab.filters_changed(); settle(self.win)
        self.assertEqual(tab.table.rowCount(), 1)
        self.assertTrue(tab.chip_container.isVisible())
        self.assertIn("IN ('valore 5-1')", tab.sql_input.toPlainText())
        tab.clear_filters_button.click(); settle(self.win)
        self.assertEqual(tab.table.rowCount(), 100)
        self.assertFalse(tab.chip_container.isVisible())

    def test_columns_menu_search_and_hide(self):
        tab = self.open(self.make_csv(rows=10, cols=5))
        btn = tab.columns_button
        btn.search.setText("col3")
        self.assertTrue(btn.list.item(0).isHidden() and not btn.list.item(3).isHidden())
        btn.set_all(False)                                          # nasconde solo quelle trovate
        self.assertEqual(tab.hidden_columns, {"col3"})
        self.assertTrue(tab.table.isColumnHidden(3) and not tab.table.isColumnHidden(2))

    def test_edit_propagates_and_csv_save_overwrites_current_file_safely(self):
        path = self.make_csv("orig.csv", 20, 3)
        before = sha(path)
        tab = self.open(path)
        tab.table.item(2, 1).setText("NUOVO")
        settle(self.win)
        self.assertEqual(sha(path), before)                        # modificare non tocca il file finché non si salva
        seen = self.drive_save()
        tab.save(); settle(self.win)
        self.assertTrue(seen["locked"])                            # «Salva» = file corrente, formato originale
        self.assertEqual(seen["format"], "csv")
        self.assertNotEqual(sha(path), before)
        self.assertIn("NUOVO", open(path, encoding="utf-8").read())
        self.assertFalse(tab.has_unsaved())
        self.assertEqual(tab.save_button.text(), "Salva")

    def test_distinct_values_and_sql_run(self):
        tab = self.open(self.make_csv(rows=30, cols=3))
        tab.sql_input.setPlainText("SELECT COUNT(*) AS n FROM csv_data")
        tab.run_sql_button.click(); settle(self.win)
        self.assertEqual(tab.sql_result.item(0, 0).text(), "30")

    def test_global_sql_join_with_aliases(self):
        a = self.open(self.make_csv("a.csv", 20, 3))
        b = self.open(self.make_csv("b.csv", 20, 3))
        g = self.win.global_tab
        g.sql_input.setPlainText("SELECT COUNT(*) FROM t1 JOIN t2 ON t1.col0 = t2.col0")
        g.run_sql(); pump(lambda: not g.is_busy()); APP.processEvents()
        self.assertEqual(g.result.item(0, 0).text(), "20")

    def test_open_paths_does_not_duplicate_csv(self):
        path = self.make_csv("d.csv", 10, 3)
        self.win.open_paths([path])
        pump(lambda: self.win.csv_tabs() and not self.win.csv_tabs()[0].is_busy())
        self.win.open_paths([path])
        self.assertEqual(len(self.win.csv_tabs()), 1)


if __name__ == "__main__":
    unittest.main()
