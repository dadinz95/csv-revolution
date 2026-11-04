"""Test funzionali del motore (nessuna interfaccia). Esecuzione:  python -m unittest tests.test_core -v"""
import datetime as dt
import hashlib
import os
import shutil
import tempfile
import unittest

from csv_reader.core.engine import CsvEngine, ImportOptions, ImportProblem, detect_encoding

from tests import legacy_types


def setUpModule():
    legacy_types.install()


def tearDownModule():
    legacy_types.uninstall()


ROWS = [["id", "città", "importo", "nota"], ["1", "Perugia", "12,50", "caffè"], ["2", "Forlì", "3,10", "più"]]


def write(path, delim, encoding, bom=False, rows=ROWS, newline="\r\n"):
    data = newline.join(delim.join(r) for r in rows) + newline
    raw = data.encode(encoding)
    if bom:
        raw = b"\xef\xbb\xbf" + raw
    with open(path, "wb") as fh:
        fh.write(raw)


def sha(path):
    with open(path, "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()


class Base(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="csvtest_")
        self.engines = []

    def tearDown(self):
        for e in self.engines:
            e.close()
        shutil.rmtree(self.dir, ignore_errors=True)

    def engine(self):
        e = CsvEngine()
        self.engines.append(e)
        return e

    def p(self, name):
        return os.path.join(self.dir, name)


class TestCsvImport(Base):
    def check_italian(self, e, path, options=None):
        cols, n = e.open_csv(path, options)
        self.assertEqual(cols, ["id", "città", "importo", "nota"])
        self.assertEqual(n, 2)
        _, rows, _ = e.preview(0, 10)
        self.assertEqual(rows[0][1], "Perugia")
        self.assertEqual(rows[0][3], "caffè")
        self.assertEqual(rows[1][1], "Forlì")
        return rows

    def test_auto_delimiters(self):
        for name, delim in (("semi", ";"), ("pipe", "|"), ("tab", "\t")):
            with self.subTest(delim=delim):
                path = self.p(name + ".csv")
                write(path, delim, "utf-8")
                e = self.engine()
                rows = self.check_italian(e, path)
                self.assertEqual(e.info["delimiter"], delim)
                self.assertEqual(rows[0][2], "12,50")  # il testo resta quello del file: nessuna conversione automatica

    def test_auto_encodings(self):
        cases = [("utf8.csv", "utf-8", False, "utf-8"), ("bom.csv", "utf-8", True, "utf-8-sig"),
                 ("cp.csv", "cp1252", False, "cp1252"), ("u16.csv", "utf-16", False, "utf-16"),
                 ("u16le.csv", "utf-16-le", False, "utf-16-le"), ("u16be.csv", "utf-16-be", False, "utf-16-be")]
        for name, enc, bom, expected in cases:
            with self.subTest(file=name):
                path = self.p(name)
                write(path, ";", enc, bom)
                self.assertEqual(detect_encoding(path)[0], expected)
                self.check_italian(self.engine(), path)

    def test_manual_encoding_is_really_applied(self):
        path = self.p("latin.csv")
        write(path, ";", "latin-1")
        e = self.engine()
        self.check_italian(e, path, ImportOptions(encoding="latin-1"))
        self.assertEqual(e.info["encoding"], "latin-1")
        # codifica sbagliata imposta a mano: i caratteri cambiano davvero (prova che l'opzione arriva al parser)
        write(self.p("utf8.csv"), ";", "utf-8")
        _, rows, _ = e.preview(0, 1) if e.open_csv(self.p("utf8.csv"), ImportOptions(encoding="latin-1")) else (0, 0, 0)
        self.assertEqual(rows[0][3], "caffÃ¨")

    def test_invalid_utf8_gives_friendly_error(self):
        path = self.p("cp.csv")
        write(path, ";", "cp1252")
        with self.assertRaises(ImportProblem) as ctx:
            self.engine().open_csv(path, ImportOptions(encoding="utf-8"))
        self.assertIn("codifica", ctx.exception.user_message.lower())
        self.assertTrue(ctx.exception.fixable)

    def test_manual_delimiter_and_custom(self):
        path = self.p("hash.csv")
        write(path, "#", "utf-8")
        e = self.engine()
        self.check_italian(e, path, ImportOptions(delimiter="#"))
        cols, _ = e.open_csv(path, ImportOptions(delimiter=";"))   # delimitatore sbagliato: una sola colonna
        self.assertEqual(len(cols), 1)

    def test_header_option(self):
        path = self.p("h.csv")
        write(path, ";", "utf-8")
        e = self.engine()
        cols, n = e.open_csv(path, ImportOptions(header=False))
        self.assertEqual(n, 3)                        # la prima riga diventa un dato
        self.assertEqual(cols[0], "column0")
        cols, n = e.open_csv(path, ImportOptions(header=True))
        self.assertEqual((cols[0], n), ("id", 2))

    def test_decimal_option(self):
        path = self.p("d.csv")
        write(path, ";", "utf-8")
        e = self.engine()
        e.open_csv(path, ImportOptions(decimal="."))
        self.assertEqual(e.preview(0, 1)[1][0][2], "12,50")      # tutto testo: il valore non cambia con l'opzione
        e.open_csv(path, ImportOptions(decimal=","))
        self.assertEqual(e.preview(0, 1)[1][0][2], "12,50")
        self.assertEqual(e.info["decimal"], ",")                  # l'opzione resta come separatore predefinito dell'esportazione

    def test_incompatible_configuration(self):
        path = self.p("x.csv")
        write(path, ";", "utf-8")
        with self.assertRaises(ImportProblem) as ctx:
            self.engine().open_csv(path, ImportOptions(delimiter=",", decimal=","))
        self.assertIn("incompatibile", ctx.exception.user_message)

    def test_file_errors(self):
        e = self.engine()
        with self.assertRaises(ImportProblem):
            e.open_csv(self.p("manca.csv"))
        open(self.p("vuoto.csv"), "w").close()
        with self.assertRaises(ImportProblem) as ctx:
            e.open_csv(self.p("vuoto.csv"))
        self.assertIn("vuoto", ctx.exception.user_message)

    def test_failed_reload_keeps_previous_dataset(self):
        good = self.p("ok.csv")
        write(good, ";", "utf-8")
        e = self.engine()
        e.open_csv(good)
        before = e.preview(0, 5)[1]
        with self.assertRaises(ImportProblem):
            e.open_csv(good, ImportOptions(delimiter=",", decimal=","))
        self.assertEqual(e.preview(0, 5)[1], before)
        self.assertEqual(e.columns, ["id", "città", "importo", "nota"])

    def test_transcoding_is_streamed_and_cleaned(self):
        big = self.p("big.csv")
        line = "1;Perugia;12,50;caffè e più\n"
        with open(big, "wb") as fh:
            fh.write("id;citta;importo;nota\n".encode("cp1252"))
            for _ in range(250_000):          # ~7 MB: più di un blocco di prova con blocchi piccoli sotto
                fh.write(line.encode("cp1252"))
        from csv_reader.core import engine as eng
        orig = eng._transcode.__defaults__
        eng._transcode.__defaults__ = (64 * 1024,)   # blocchi da 64 KB: verifica la lettura a blocchi
        try:
            e = self.engine()
            cols, n = e.open_csv(big)
        finally:
            eng._transcode.__defaults__ = orig
        self.assertEqual(n, 250_000)
        self.assertEqual(e.preview(0, 1)[1][0][3], "caffè e più")
        tmp = e._tmpdir
        self.assertTrue(os.path.isdir(tmp))
        e.close()
        self.assertFalse(os.path.exists(tmp))

    def test_csv_original_untouched_by_a_copy_and_overwritten_only_on_explicit_save(self):
        path = self.p("o.csv")
        write(path, ";", "utf-8")
        before = sha(path)
        e = self.engine()
        e.open_csv(path)
        e.set_cell(0, "nota", "modificata")
        e.save_csv(self.p("copia.csv"))
        self.assertEqual(sha(path), before)                     # salvare una copia non tocca l'originale
        self.assertIn("modificata", open(self.p("copia.csv"), encoding="utf-8").read())
        e.save_csv(path)                                        # Fase 3: «Salva» può sostituire il CSV corrente (atomico)
        self.assertNotEqual(sha(path), before)
        self.assertIn("modificata", open(path, encoding="utf-8").read())


class TestSort(Base):
    def test_sort_numeric_text_nulls_and_stable_row_ids(self):
        path = self.p("s.csv")
        rows = [["id", "n", "t"], ["1", "10", "b"], ["2", "9", "a"], ["3", "", "c"], ["4", "100", "a"], ["5", "9", "d"]]
        write(path, ";", "utf-8", rows=rows)
        e = self.engine()
        e.open_csv(path)
        _, r, ids = e.preview(0, 10, order=("n", False))
        self.assertEqual([x[0] for x in r], [2, 5, 1, 4, 3])        # numerico: 9 < 10 < 100, vuoto in fondo, parità in ordine file
        self.assertEqual(ids, [1, 4, 0, 3, 2])                       # gli id di riga restano quelli del file (le modifiche restano agganciate)
        _, r, _ = e.preview(0, 10, order=("n", True))
        self.assertEqual([x[0] for x in r], [4, 1, 2, 5, 3])        # decrescente: vuoto ancora in fondo
        _, r, _ = e.preview(0, 2, order=("t", False))
        self.assertEqual([x[2] for x in r], ["a", "a"])
        _, r2, _ = e.preview(2, 2, order=("t", False))              # paginazione coerente con l'ordine
        self.assertEqual([x[2] for x in r + r2], ["a", "a", "b", "c"])
        _, r, _ = e.preview(0, 10, search="a", order=("id", True))  # ordine insieme ai filtri
        self.assertEqual([x[0] for x in r], [4, 2])
        self.assertEqual(e.preview(0, 1, order=("inesistente", False))[1][0][0], 1)   # colonna sconosciuta: ignorata


class TestExcel(Base):
    def make_xlsx(self):
        import openpyxl
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "Dati"
        ws.append(["ID", "Codice", "Importo", "Data", "Attivo", "ID", None])
        ws.append([1, "00123", 12.5, dt.datetime(2026, 1, 5), True, "a", "x"])
        ws.append([2, "00456", 3, dt.datetime(2026, 2, 6, 10, 30), False, "b", None])
        ws.append([None] * 7)
        ws.append([3, "n/a", None, dt.datetime(2026, 3, 7), True, "c", "y"])
        ws["F7"] = "=A2*2"
        wb.create_sheet("Altro").append(["k", "v"])
        path = self.p("t.xlsx")
        wb.save(path)
        return path

    def test_import_types_and_names(self):
        path = self.make_xlsx()
        e = self.engine()
        cols, n = e.open_excel(path, "Dati", True)
        self.assertEqual(cols, ["ID", "Codice", "Importo", "Data", "Attivo", "ID_2", "colonna_7"])
        self.assertEqual(n, 4)                                     # riga vuota interna conservata
        types = dict((r[0], r[1]) for r in e.con.execute("DESCRIBE SELECT * FROM csv_data").fetchall())
        self.assertEqual(set(types.values()), {"VARCHAR"})        # tutto testo: nessun tipo dedotto
        rows = e.preview(0, 10)[1]
        self.assertEqual(rows[0][:5], ("1", "00123", "12.5", "2026-01-05 0:00:00", "TRUE"))   # come lo mostra Excel col formato della cella
        self.assertEqual(rows[1][2:4], ("3", "2026-02-06 10:30:00"))
        self.assertIsNone(rows[2][0])                              # riga vuota
        self.assertTrue(e.info["warnings"])                        # nomi rinominati

    def test_filter_search_edit_on_excel(self):
        e = self.engine()
        e.open_excel(self.make_xlsx(), "Dati", True)
        self.assertEqual(e.count_filtered("456", {}), 1)
        self.assertEqual(e.count_filtered("", {"Attivo": {"text": "", "values": ["TRUE"]}}), 2)
        self.assertEqual(e.distinct_values("Codice")[0][0][0], None)

    def test_without_header(self):
        e = self.engine()
        cols, n = e.open_excel(self.make_xlsx(), "Dati", False)
        self.assertEqual(n, 5)
        self.assertEqual(cols[0], "colonna_1")

    def test_save_copy_never_touches_original(self):
        import openpyxl
        path = self.make_xlsx()
        before = sha(path)
        e = self.engine()
        e.open_excel(path, "Dati", True)
        e.convert_column("Importo", "decimal")                       # tipi solo su richiesta
        e.convert_column("ID", "int")
        e.set_cell(0, "ID", "9")
        with self.assertRaises(ImportProblem):
            e.save_excel_copy(path)
        copy = self.p("copia.xlsx")
        e.set_cell(0, "ID", "1")                                    # riportata al valore originale: non è più una modifica
        for rid, col, text in ((0, "Importo", "99,5"), (1, "Codice", "ZZ"), (2, "ID", "abc")):
            e.set_cell(rid, col, text)
        report = e.save_excel_copy(copy)
        self.assertEqual(sha(path), before)
        wb = openpyxl.load_workbook(copy)
        self.assertEqual(wb.sheetnames, ["Dati", "Altro"])         # fogli non importati conservati
        ws = wb["Dati"]
        self.assertEqual(ws["C2"].value, 99.5)
        self.assertEqual(ws["B3"].value, "ZZ")
        self.assertEqual(ws["F7"].value, "=A2*2")                  # formula non toccata conservata
        self.assertEqual(ws["A4"].value, "abc")                    # «abc» in colonna numerica: la colonna diventa testo

    def test_xls(self):
        try:
            import xlwt
        except ImportError:
            self.skipTest("xlwt non installato (serve solo per creare il file di prova)")
        import openpyxl
        book = xlwt.Workbook()
        s = book.add_sheet("Dati")
        for j, h in enumerate(["ID", "Nome", "Data"]):
            s.write(0, j, h)
        st = xlwt.XFStyle()
        st.num_format_str = "YYYY-MM-DD"
        s.write(1, 0, 1); s.write(1, 1, "Mario"); s.write(1, 2, dt.datetime(2026, 5, 1), st)
        book.add_sheet("Foglio2").write(0, 0, "z")
        path = self.p("t.xls")
        book.save(path)
        before = sha(path)
        e = self.engine()
        cols, n = e.open_excel(path, "Dati", True)
        self.assertEqual((cols, n), (["ID", "Nome", "Data"], 1))
        e.set_cell(0, "Nome", "Luigi")
        report = e.save_excel_copy(self.p("copia.xlsx"))
        self.assertTrue(report["converted_from_xls"])
        self.assertEqual(sha(path), before)
        wb = openpyxl.load_workbook(self.p("copia.xlsx"))
        self.assertEqual(wb["Dati"]["B2"].value, "Luigi")
        self.assertEqual(wb.sheetnames, ["Dati", "Foglio2"])


if __name__ == "__main__":
    unittest.main()
