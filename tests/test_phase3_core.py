"""Fase 3 - esportazione e salvataggio nel motore. Esecuzione:  python -m unittest tests.test_phase3_core -v"""
import datetime as dt
import os
import stat
import unittest

from csv_reader.core.engine import CsvEngine, ImportOptions, ImportProblem, Scope
from tests.test_core import Base, sha

from tests import legacy_types


def setUpModule():
    legacy_types.install()


def tearDownModule():
    legacy_types.uninstall()


SRC = ("id;cod;importo;data;ts;flag;nota\n"
       '1;00123;1.50;01/02/2024;2024-01-02 03:04:05;true;"a;b"\n'
       '2;00456;2,00;15/03/2024;2024-02-03 00:00:00;false;"riga\ndue ""q"""\n'
       "3;;;;;;\n"
       "4;7;1e3;31/12/2024;2024-12-31 23:59:59;true;=SUM(A1)\n")


def rows_of(engine, n=1000):
    return engine.preview(0, n)[1]


class Phase3(Base):
    def csv(self, name="src.csv", text=SRC, encoding="utf-8"):
        path = self.p(name)
        with open(path, "w", encoding=encoding, newline="") as fh:
            fh.write(text)
        return path

    def open(self, path=None):
        e = self.engine()
        e.open_csv(path or self.csv())
        return e

    def reopen(self, path, **options):
        e = self.engine()
        e.open_csv(path, ImportOptions(**options) if options else None)
        return e


class TestCsvExport(Phase3):
    def test_roundtrip_keeps_text_dates_leading_zeros_newlines_and_nulls(self):
        e = self.open()
        out = self.p("out.csv")
        r = e.save_csv(out, ";", "utf-8")
        self.assertEqual(r["rows"], 4)
        again = self.reopen(out)
        self.assertEqual(rows_of(again), rows_of(e))
        text = open(out, encoding="utf-8").read()
        self.assertIn("01/02/2024", text)                      # il formato data dell'originale resta
        self.assertIn("00123", text)                           # zeri iniziali
        self.assertIn('"riga\ndue ""q"""', text)               # a capo e virgolette
        self.assertIn("=SUM(A1)", text)                        # testo, non valutato

    def test_every_delimiter_and_encoding_reopens_identically(self):
        e = self.open(self.csv(text="a,b\nàèì,ç€\n1,ü\n"))
        for delim in (",", ";", "\t", "|"):
            for enc in ("utf-8", "utf-8-sig", "utf-16", "cp1252"):
                if enc == "cp1252":
                    e2 = self.open(self.csv("l.csv", "a,b\nàèì,çù\n1,ü\n"))
                else:
                    e2 = e
                out = self.p(f"o_{ord(delim)}_{enc}.csv")
                e2.save_csv(out, delim, enc)
                raw = open(out, "rb").read()
                if enc == "utf-8-sig":
                    self.assertTrue(raw.startswith(b"\xef\xbb\xbf"))
                if enc == "utf-16":
                    self.assertTrue(raw[:2] in (b"\xff\xfe", b"\xfe\xff"))
                again = self.reopen(out, delimiter=delim, encoding=enc)
                self.assertEqual(rows_of(again), rows_of(e2), (delim, enc))

    def test_unrepresentable_character_fails_and_leaves_existing_file_untouched(self):
        e = self.open(self.csv(text="a,b\n€,x\n"))
        out = self.p("keep.csv")
        open(out, "w").write("vecchio\n")
        with self.assertRaises(ImportProblem) as ctx:
            e.save_csv(out, ",", "cp1252" if False else "latin-1")
        self.assertIn("non può essere scritto", str(ctx.exception))
        self.assertEqual(open(out).read(), "vecchio\n")
        self.assertEqual([f for f in os.listdir(self.dir) if f.endswith(".tmp")], [])

    def test_decimal_comma_and_validation(self):
        e = self.open(self.csv(text="a,b\n1,2.5\n2,3.25\n"))
        e.open_csv(self.csv("n.csv", "a;b\n1;2.5\n2;3.25\n"))
        out = self.p("d.csv")
        e.save_csv(out, ";", "utf-8", ",")
        self.assertIn("2,5", open(out).read())
        # il file riaperto è tutto testo: «2,5» resta «2,5» (nessuna conversione automatica)
        self.assertEqual(rows_of(self.reopen(out, decimal=",")), [(1, "2,5"), (2, "3,25")])
        with self.assertRaises(ImportProblem):
            e.save_csv(out, ",", "utf-8", ",")
        with self.assertRaises(ImportProblem):
            e.save_csv(out, ";;", "utf-8")

    def test_export_has_all_rows_all_columns_current_names_and_edits(self):
        n = 3000
        path = self.p("big.csv")
        with open(path, "w") as fh:
            fh.write("a,b,c,d\n" + "".join(f"{i},x{i},y{i},z{i}\n" for i in range(n)))
        e = self.open(path)
        e.set_cell(5, "b", "MOD")
        e.replace_all("y1", "Y!", True, False, Scope("columns", ["c"]))
        e.rename_column("a", "id")
        e.delete_column("d")
        e.add_column("nuova", after="id")
        e.delete_rows([0, 1])
        out = self.p("full.csv")
        e.save_csv(out)
        again = self.reopen(out)
        self.assertEqual(again.columns, ["id", "nuova", "b", "c"])
        self.assertEqual(again.count_total(), n - 2)
        rows = rows_of(again, 5000)
        self.assertEqual(rows[3][2], "MOD")
        self.assertTrue(any(str(r[3]).startswith("Y!") for r in rows))
        self.assertTrue(all(r[1] is None for r in rows))

    def test_overwrite_same_file_is_atomic_and_rereadable(self):
        path = self.csv()
        e = self.open(path)
        e.set_cell(0, "nota", "nuova")
        e.save_csv(path, ";", "utf-8")
        self.assertIn("nuova", open(path, encoding="utf-8").read())
        self.assertEqual(rows_of(self.reopen(path))[0][6], "nuova")

    def test_write_errors_are_friendly_and_keep_state(self):
        e = self.open()
        e.set_cell(0, "nota", "x")
        v = e.version
        with self.assertRaises(ImportProblem) as ctx:
            e.save_csv(self.p("nonesiste/out.csv"))
        self.assertIn("cartella", str(ctx.exception))
        with self.assertRaises(ImportProblem):
            e.save_csv(self.dir)
        if hasattr(os, "geteuid") and os.geteuid() != 0:
            ro = self.p("ro"); os.mkdir(ro); os.chmod(ro, stat.S_IREAD | stat.S_IEXEC)
            try:
                with self.assertRaises(ImportProblem) as ctx:
                    e.save_csv(os.path.join(ro, "o.csv"))
                self.assertIn("permessi", str(ctx.exception).lower())
            finally:
                os.chmod(ro, stat.S_IRWXU)
        self.assertTrue(e.has_changes())                       # nessuno stato di «salvato» registrato dal motore
        self.assertEqual(e.version, v)

    def test_save_returns_the_version_it_wrote(self):
        e = self.open()
        e.set_cell(0, "nota", "x")
        r = e.save_csv(self.p("v.csv"))
        self.assertEqual(r["version"], e.version)
        e.mark_saved(r["version"])
        self.assertFalse(e.has_changes())
        e.set_cell(1, "nota", "y")
        e.mark_saved(r["version"])                             # vecchia versione: la nuova modifica resta non salvata
        self.assertTrue(e.has_changes())


class TestXlsxExport(Phase3):
    def test_csv_to_xlsx_types_text_dates_and_formula_like_text(self):
        import openpyxl
        e = self.open()
        out = self.p("o.xlsx")
        r = e.save_xlsx(out)
        self.assertEqual(r["rows"], 4)
        wb = openpyxl.load_workbook(out)
        ws = wb["Dati"]
        self.assertEqual([c.value for c in ws[1]], ["id", "cod", "importo", "data", "ts", "flag", "nota"])
        row2 = [c.value for c in ws[2]]
        self.assertEqual(row2[0], 1)                                       # numero
        self.assertEqual(row2[1], "00123")                                 # zeri iniziali = testo
        self.assertEqual(row2[3], dt.datetime(2024, 2, 1))                 # data vera
        self.assertEqual(row2[5], True)
        self.assertEqual(ws["G5"].value, "=SUM(A1)")
        self.assertEqual(ws["G5"].data_type, "s")                          # NON è una formula
        self.assertIsNone(ws["B4"].value)                                  # vuoto = cella vuota
        self.assertEqual(ws["G3"].value, 'riga\ndue "q"')
        again = self.engine(); again.open_excel(out, "Dati", True)
        self.assertEqual(again.columns, e.columns)
        self.assertEqual(again.count_total(), 4)

    def test_limits_and_illegal_characters(self):
        import openpyxl
        e = self.open(self.csv(text="a,b\n1,ok\x07bad\n99999999999999999,x\n"))
        out = self.p("c.xlsx")
        r = e.save_xlsx(out)
        self.assertEqual(r["stripped"], 1)
        ws = openpyxl.load_workbook(out)["Dati"]
        self.assertEqual(ws["B2"].value, "okbad")
        self.assertEqual(ws["A3"].value, "99999999999999999")              # >15 cifre: testo, non arrotondato
        with self.assertRaises(ImportProblem):
            e.save_xlsx(self.p("c.csv"))                                   # estensione sbagliata

    def test_xlsx_original_never_overwritten_and_edits_in_copy(self):
        import openpyxl
        from tests.test_core import TestExcel
        path = TestExcel.make_xlsx(self)
        before = sha(path)
        e = self.engine(); e.open_excel(path, "Dati", True)
        e.convert_column("Importo", "decimal")                              # tipo assegnato dall'utente
        e.set_cell(0, "Importo", "99")
        with self.assertRaises(ImportProblem) as ctx:
            e.save_xlsx(path)
        self.assertIn("originale", str(ctx.exception))
        self.assertEqual(sha(path), before)
        out = self.p("copia.xlsx")
        e.save_xlsx(out)
        self.assertEqual(sha(path), before)
        wb = openpyxl.load_workbook(out)
        self.assertIn("Altro", wb.sheetnames)                              # altro foglio conservato
        self.assertEqual(wb["Dati"]["C2"].value, 99)
        e.save_xlsx(out)                                                   # una copia già creata si può risalvare
        self.assertEqual(sha(path), before)

    def test_xlsx_to_csv_and_xls_to_xlsx(self):
        from tests.test_core import TestExcel
        path = TestExcel.make_xlsx(self)
        e = self.engine(); e.open_excel(path, "Dati", True)
        out = self.p("da_xlsx.csv")
        e.save_csv(out, ";", "utf-8")
        again = self.reopen(out, delimiter=";")
        self.assertEqual(again.count_total(), e.count_total())
        self.assertEqual(again.columns, e.columns)
        self.assertIn("00123", open(out).read())
        with self.assertRaises(ImportProblem):
            e.save_csv(path)                                               # nemmeno come CSV sopra l'Excel originale
        try:
            import xlwt
        except ImportError:
            self.skipTest("xlwt non installato")
        book = xlwt.Workbook(); s = book.add_sheet("Dati")
        for j, h in enumerate(["ID", "Nome"]):
            s.write(0, j, h)
        s.write(1, 0, 1); s.write(1, 1, "Mario")
        xls = self.p("t.xls"); book.save(xls)
        before = sha(xls)
        x = self.engine(); x.open_excel(xls, "Dati", True)
        x.set_cell(0, "Nome", "Luigi")
        with self.assertRaises(ImportProblem):
            x.save_xlsx(self.p("t.xls"))
        x.save_xlsx(self.p("t2.xlsx"))
        self.assertEqual(sha(xls), before)
        import openpyxl
        self.assertEqual(openpyxl.load_workbook(self.p("t2.xlsx"))["Dati"]["B2"].value, "Luigi")

    def test_structure_changes_reach_the_xlsx(self):
        import openpyxl
        e = self.open()
        e.rename_column("nota", "note")
        e.delete_column("ts")
        e.add_column("extra", after="id")
        out = self.p("s.xlsx")
        e.save_xlsx(out)
        ws = openpyxl.load_workbook(out)["Dati"]
        self.assertEqual([c.value for c in ws[1]], ["id", "extra", "cod", "importo", "data", "flag", "note"])


if __name__ == "__main__":
    unittest.main()
