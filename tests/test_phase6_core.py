"""v0.17.0 - motore: apertura tutto-testo, riconoscimento e conversione espliciti dei tipi, salvataggio senza alterazioni.
Nessun file di questo modulo usa la lettura con tipi dedotti (tests/legacy_types.py): è il comportamento reale.
Esecuzione:  python -m unittest tests.test_phase6_core -v"""
import datetime as dt
import hashlib
import os
import tempfile
import unittest

from csv_reader.core import coltypes
from csv_reader.core.engine import CsvEngine, ImportOptions, ImportProblem, Scope
from csv_reader.core.xlformat import display_text
from tests.test_phase5_core import failing


def sha(path):
    return hashlib.sha256(open(path, "rb").read()).hexdigest()


def make(text, name="a.csv", options=None):
    d = tempfile.mkdtemp(prefix="p6core_")
    p = os.path.join(d, name)
    with open(p, "w", encoding="utf-8", newline="") as fh:
        fh.write(text)
    e = CsvEngine()
    e.open_csv(p, options)
    return e, d, p


def rows(e):
    return e.preview(0, 100)[1]


class TestOpenAsText(unittest.TestCase):
    TEXT = ("cod;n;imp;data;ora;ok;cf;iuv;sp\n"
            "00123;1;1.50;2024-01-05;10:30;true;RSSMRA80A01H501U;01234567890123456789; x \n"
            "00045;002;2,5;05/01/2024;7:05:09;FALSE;;99999999999999999999;y\n"
            "7;3;1E3;31/02/2024;x;si;AB12;0;\n")

    def test_every_column_is_text_and_values_are_verbatim(self):
        e, _, _ = make(self.TEXT)
        self.assertEqual(set(e.dtypes.values()), {"VARCHAR"})
        r = rows(e)
        self.assertEqual(r[0], ("00123", "1", "1.50", "2024-01-05", "10:30", "true", "RSSMRA80A01H501U",
                                "01234567890123456789", " x "))
        self.assertEqual(r[1][:3], ("00045", "002", "2,5"))
        self.assertEqual(r[2][2:4], ("1E3", "31/02/2024"))          # date non valide comprese: nessuna interpretazione

    def test_all_delimiters_and_decimal_option_do_not_convert(self):
        for delim in (",", ";", "\t", "|"):
            text = "a{0}b\n00123{0}1.50\n".format(delim)
            e, _, _ = make(text)
            self.assertEqual(rows(e), [("00123", "1.50")], delim)
        e, _, p = make("a;b\n00123;12,50\n")
        e.open_csv(p, ImportOptions(decimal=","))
        self.assertEqual(rows(e), [("00123", "12,50")])

    def test_reopening_with_other_options_stays_text(self):
        e, _, p = make("a,b\n001,2.50\n")
        e.open_csv(p, ImportOptions(delimiter=",", header=False))
        self.assertEqual(set(e.dtypes.values()), {"VARCHAR"})
        self.assertEqual(rows(e)[1], ("001", "2.50"))

    def test_saving_without_edits_and_after_other_edits_leaves_values_unchanged(self):
        e, d, p = make(self.TEXT)
        out = os.path.join(d, "out.csv")
        e.save_csv(out, ";", "utf-8", ".")
        e2 = CsvEngine(); e2.open_csv(out)
        self.assertEqual(rows(e2), rows(e))
        e.set_cell(0, "sp", "nuovo")                                 # un'altra cella: le altre non cambiano
        e.save_csv(out, ";", "utf-8", ".")
        e3 = CsvEngine(); e3.open_csv(out)
        self.assertEqual([r[:8] for r in rows(e3)], [r[:8] for r in rows(e)])
        self.assertEqual(rows(e3)[0][8], "nuovo")

    def test_sort_and_filter_on_text_are_textual_until_a_type_is_assigned(self):
        e, _, _ = make("n\n10\n9\n100\n")
        order = [r[0] for r in e.preview(0, 10, order=("n", False))[1]]
        self.assertEqual(order, ["10", "100", "9"])                  # testo
        e.convert_column("n", "int")
        order = [r[0] for r in e.preview(0, 10, order=("n", False))[1]]
        self.assertEqual(order, [9, 10, 100])


class TestExcelAsText(unittest.TestCase):
    def make_xlsx(self):
        import openpyxl
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "Dati"
        ws.append(["Cod", "Id", "Imp", "Data", "DataOra", "Perc", "Ok", "Testo", "Gen", "Vuoto"])
        ws.append([123, 1234567890123, 1.5, dt.datetime(2026, 1, 5), dt.datetime(2026, 1, 5, 14, 30, 9), 0.256, True,
                   "00123", 3.0, None])
        ws.append([45, 5, 2, dt.datetime(2026, 12, 31), dt.datetime(2026, 12, 31, 8, 5), 1, False, "a", 0.1 + 0.2, None])
        for cell, fmt in (("A2", "00000"), ("A3", "00000"), ("C2", "0.00"), ("C3", "0.00"), ("D2", "dd/mm/yyyy"),
                          ("D3", "dd/mm/yyyy"), ("E2", "yyyy-mm-dd hh:mm:ss"), ("E3", "yyyy-mm-dd hh:mm"),
                          ("F2", "0.0%"), ("F3", "0%")):
            ws[cell].number_format = fmt
        wb.create_sheet("Altro").append(["k"])
        path = os.path.join(tempfile.mkdtemp(prefix="p6x_"), "t.xlsx")
        wb.save(path)
        return path

    def test_cells_are_text_as_excel_shows_them(self):
        e = CsvEngine()
        e.open_excel(self.make_xlsx(), "Dati", True)
        self.assertEqual(set(e.dtypes.values()), {"VARCHAR"})
        r = rows(e)
        self.assertEqual(r[0][:9], ("00123", "1234567890123", "1.50", "05/01/2026", "2026-01-05 14:30:09", "25.6%",
                                    "TRUE", "00123", "3"))
        self.assertEqual(r[1][:9], ("00045", "5", "2.00", "31/12/2026", "2026-12-31 08:05", "100%", "FALSE", "a", "0.3"))
        self.assertIsNone(r[0][9])

    def test_original_never_changes_and_untouched_cells_keep_their_excel_type(self):
        import openpyxl
        path = self.make_xlsx()
        before = sha(path)
        e = CsvEngine()
        e.open_excel(path, "Dati", True)
        e.set_cell(0, "Testo", "cambiato")
        out = path.replace("t.xlsx", "copia.xlsx")
        e.save_excel_copy(out)
        self.assertEqual(sha(path), before)
        ws = openpyxl.load_workbook(out)["Dati"]
        self.assertEqual(ws["A2"].value, 123)                  # numero con formato 00000: resta numero
        self.assertEqual(ws["A2"].number_format, "00000")
        self.assertEqual(ws["C2"].value, 1.5)
        self.assertEqual(ws["D2"].value, dt.datetime(2026, 1, 5))
        self.assertEqual(ws["G2"].value, True)
        self.assertEqual(ws["H2"].value, "cambiato")
        self.assertEqual(ws["H3"].value, "a")

    def test_converted_column_is_saved_as_numbers(self):
        import openpyxl
        path = self.make_xlsx()
        e = CsvEngine()
        e.open_excel(path, "Dati", True)
        e.convert_column("Gen", "decimal")
        out = path.replace("t.xlsx", "copia.xlsx")
        e.save_excel_copy(out)
        ws = openpyxl.load_workbook(out)["Dati"]
        self.assertEqual((ws["I2"].value, ws["I3"].value), (3, 0.3))

    def test_format_helper_cases(self):
        self.assertEqual(display_text(5, "0000"), "0005")
        self.assertEqual(display_text(1234567.891, "#,##0.00"), "1,234,567.89")
        self.assertEqual(display_text(-3.5, "0.00;(0.00)"), "(3.50)")
        self.assertEqual(display_text(dt.datetime(2024, 3, 5, 14, 7, 9), "d mmm yy"), "5 mar 24")
        self.assertEqual(display_text(dt.datetime(2024, 3, 5, 14, 7, 9), "mm:ss"), "07:09")      # m dopo h/prima di s = minuti
        self.assertEqual(display_text(0.1 + 0.2, None), "0.3")
        self.assertEqual(display_text(True, None), "TRUE")


class TestRecognition(unittest.TestCase):
    def kind(self, values, **kw):
        e, _, _ = make("c\n" + "\n".join(values) + "\n", options=ImportOptions(delimiter=";"))
        a = e.analyze_column("c")
        return a["kind"], a

    def test_integers_decimals_dates_booleans(self):
        self.assertEqual(self.kind(["1", "-20", "+3", "400"])[0], "int")
        self.assertEqual(self.kind(["1.50", "2,5", "3"])[0], "decimal")
        self.assertEqual(self.kind(["1.234,56", "7,1"])[0], "decimal")
        self.assertEqual(self.kind(["2024-01-05", "05/01/2024", "5.1.2024"])[0], "date")
        self.assertEqual(self.kind(["2024-01-05 10:30", "05/01/2024"])[0], "datetime")
        self.assertEqual(self.kind(["true", "FALSE", "Sì", "no"])[0], "bool")
        self.assertEqual(self.kind(["a", "b"])[0], "text")

    def test_identifiers_are_never_proposed_as_numbers(self):
        k, a = self.kind(["00123", "00045"])
        self.assertEqual(k, "text")
        self.assertIn("identificatori", a["reason"])
        self.assertEqual(self.kind(["12345678901234567", "1"])[0], "text")          # più di 15 cifre

    def test_mixed_and_invalid_values(self):
        k, a = self.kind(["1", "2", "x"])
        self.assertEqual(k, "text")                                                    # 67%: troppo poco
        k, a = self.kind([str(i) for i in range(19)] + ["abc"])
        self.assertEqual((k, a["partial"], a["invalid"], a["examples"]), ("int", True, 1, ["abc"]))
        k, a = self.kind(["31/02/2024", "2024-13-01"])
        self.assertEqual(k, "text")
        k, a = self.kind([""])
        self.assertEqual(k, "text")

    def test_analysis_changes_nothing(self):
        e, _, _ = make("c\n1\n2\n")
        e.analyze_column("c")
        self.assertFalse(e.mutated)
        self.assertFalse(e.has_changes())
        self.assertEqual(e.dtypes["c"], "VARCHAR")


class TestConversion(unittest.TestCase):
    def test_each_type_and_exact_undo(self):
        e, _, _ = make("i;d;dt;ts;b;t\n00007;1.50;05/01/2024;2024-01-05 10:30;vero;x\n12;2,5;2024-02-01;1/2/2024;0;y\n;;;;;\n")
        before = rows(e)
        e.convert_column("i", "int"); e.convert_column("d", "decimal"); e.convert_column("dt", "date")
        e.convert_column("ts", "datetime"); e.convert_column("b", "bool")
        r = rows(e)
        self.assertEqual(r[0][0], 7)                                   # lo zero iniziale si perde SOLO se lo si chiede
        self.assertEqual(str(r[0][1]), "1.50")
        self.assertEqual(str(r[1][1]), "2.50")
        self.assertEqual(r[0][2], dt.date(2024, 1, 5))
        self.assertEqual(r[0][3], dt.datetime(2024, 1, 5, 10, 30))
        self.assertEqual((r[0][4], r[1][4], r[2][4]), (True, False, None))
        self.assertEqual(r[0][5], "x")
        self.assertIsNone(r[2][0])
        while e.can_undo():
            e.undo()
        self.assertEqual(rows(e), before)                               # testo originale identico
        self.assertEqual(set(e.dtypes.values()), {"VARCHAR"})
        self.assertFalse(e.has_changes())

    def test_non_convertible_values_do_not_block_and_are_restored_by_undo(self):
        e, _, _ = make("n\n1\nabc\n3\n\n")
        plan = e.preview_conversion("n", "int")
        self.assertEqual((plan["invalid"], plan["examples"]), (1, ["abc"]))
        res = e.convert_column("n", "int")
        self.assertEqual((res["converted"], res["invalid"]), (2, 1))
        self.assertEqual([r[0] for r in rows(e)], [1, None, 3, None])
        e.undo()
        self.assertEqual([r[0] for r in rows(e)], ["1", "abc", "3", None])

    def test_decimal_scale_never_rounds_and_is_uniform(self):
        e, _, _ = make("d\n1.5\n2.125\n3\n")
        res = e.convert_column("d", "decimal")
        self.assertEqual(res["scale"], 3)                               # almeno le cifre dei dati
        self.assertEqual([str(r[0]) for r in rows(e)], ["1.500", "2.125", "3.000"])
        e2, _, _ = make("d\n1.5\n2\n")
        e2.convert_column("d", "decimal", 4)
        self.assertEqual([str(r[0]) for r in rows(e2)], ["1.5000", "2.0000"])
        e3, _, _ = make("d\n1.5\n2.125\n")
        e3.convert_column("d", "decimal", 0)                            # un numero di decimali troppo basso viene alzato
        self.assertEqual([str(r[0]) for r in rows(e3)], ["1.500", "2.125"])

    def test_thousands_separators_and_ambiguous_forms(self):
        e, _, _ = make("d\n1.234,56\n1,234.56\n1.234\n1,234\n1.234.567\n")
        e.convert_column("d", "decimal")
        self.assertEqual([float(r[0]) for r in rows(e)], [1234.56, 1234.56, 1.234, 1.234, 1234567.0])

    def test_back_to_text_keeps_the_displayed_value(self):
        e, _, _ = make("d\n1.50\n2.5\n")
        e.convert_column("d", "decimal")
        e.convert_column("d", "text")
        self.assertEqual([r[0] for r in rows(e)], ["1.50", "2.50"])
        self.assertEqual(e.dtypes["d"], "VARCHAR")

    def test_same_type_is_a_no_op(self):
        e, _, _ = make("n\n1\n")
        e.convert_column("n", "int")
        v = e.version
        self.assertFalse(e.convert_column("n", "int")["changed"])
        self.assertEqual(e.version, v)
        self.assertFalse(e.convert_column("n", "text")["dtype"] != "VARCHAR")

    def test_failure_in_the_middle_restores_everything(self):
        e, _, _ = make("n;m\n1;a\n2;b\n")
        e.convert_column("m", "text")                                   # già testo: nessuna modifica
        before = (rows(e), list(e.columns), dict(e.dtypes), e.version)
        with failing(e, "ALTER TABLE wk.work ALTER COLUMN"):
            with self.assertRaises(RuntimeError):
                e.convert_column("n", "int")
        self.assertEqual((rows(e), list(e.columns), dict(e.dtypes), e.version), before)
        self.assertFalse(e.can_undo())
        self.assertEqual(e.convert_column("n", "int")["converted"], 2)  # e poi funziona

    def test_unknown_type_is_rejected(self):
        e, _, _ = make("n\n1\n")
        with self.assertRaises(ImportProblem):
            e.convert_column("n", "xyz")
        with self.assertRaises(ImportProblem):
            e.convert_column("nope", "int")

    def test_filters_sort_sql_and_edits_follow_the_new_type(self):
        e, _, _ = make("n;d\n10;2024-03-01\n9;2023-01-01\n100;2025-05-05\nx;\n")
        e.convert_column("n", "int"); e.convert_column("d", "date")
        self.assertEqual(e.count_filtered("", {"n": {"op": "gt", "value": "9"}}), 2)
        self.assertEqual(e.count_filtered("", {"d": {"op": "gt", "value": "01/01/2024"}}), 2)
        self.assertEqual([r[1] for r in e.preview(0, 10, order=("d", False))[1]][:3],
                         [dt.date(2023, 1, 1), dt.date(2024, 3, 1), dt.date(2025, 5, 5)])
        data, cols = e.run_query("SELECT sum(n) AS s, max(d) AS m FROM csv_data")
        self.assertEqual(data[0], (119, dt.date(2025, 5, 5)))
        e.set_cell(0, "n", "11")
        self.assertEqual(e.dtypes["n"], "BIGINT")
        self.assertEqual(rows(e)[0][0], 11)
        e.set_cell(1, "n", "abc")                                       # valore incompatibile: la colonna torna testo
        self.assertEqual(e.dtypes["n"], "VARCHAR")

    def test_boolean_column_sorts_and_filters(self):
        e, _, _ = make("b\ntrue\nfalse\ntrue\n\n")
        e.convert_column("b", "bool")
        self.assertEqual([r[0] for r in e.preview(0, 10, order=("b", False))[1]], [False, True, True, None])
        self.assertEqual(e.count_filtered("", {"b": {"op": "eq", "value": "true"}}), 2)

    def test_csv_export_of_converted_columns(self):
        e, d, _ = make("i;d;dt;b\n7;1.5;05/01/2024;vero\n")
        e.convert_column("d", "decimal"); e.convert_column("dt", "date"); e.convert_column("b", "bool")
        out = os.path.join(d, "o.csv")
        e.save_csv(out, ";", "utf-8", ",")
        self.assertEqual(open(out, encoding="utf-8").read().splitlines(), ["i;d;dt;b", "7;1,50;05/01/2024;true"])   # la data esce nel formato del file

    def test_converted_columns_survive_structure_changes_and_undo_chain(self):
        e, _, _ = make("a;b\n1;x\n2;y\n")
        e.convert_column("a", "int")
        e.add_column("c")
        e.rename_column("a", "aa")
        e.delete_rows([0])
        self.assertEqual(e.dtypes["aa"], "BIGINT")
        for _ in range(4):
            e.undo()
        self.assertEqual(rows(e), [("1", "x"), ("2", "y")])
        self.assertEqual(e.dtypes["a"], "VARCHAR")


class TestLargeAndLong(unittest.TestCase):
    def test_very_long_values_open_and_convert_safely(self):
        long_text = "x" * 200_000
        e, _, _ = make(f"a;b\n{long_text};1\n")
        self.assertEqual(len(rows(e)[0][0]), 200_000)
        self.assertEqual(e.analyze_column("a")["kind"], "text")
        self.assertEqual(e.convert_column("b", "int")["converted"], 1)

    def test_many_rows_are_analysed_and_converted(self):
        d = tempfile.mkdtemp(prefix="p6big_")
        p = os.path.join(d, "big.csv")
        e = CsvEngine()
        e.con.execute(f"COPY (SELECT i AS id, CAST(i AS VARCHAR) || '.50' AS imp FROM range(300000) t(i)) TO '{p}' (HEADER)")
        e.open_csv(p)
        self.assertEqual(e.analyze_column("id")["kind"], "int")
        self.assertEqual(e.analyze_column("imp")["kind"], "decimal")
        e.convert_column("imp", "decimal")
        self.assertEqual(e.run_query("SELECT count(*) FROM csv_data WHERE imp > 100")[0][0][0], 299900)   # 100.50 > 100


class TestTypeHelpers(unittest.TestCase):
    def test_kind_of_and_labels(self):
        self.assertEqual([coltypes.kind_of(t) for t in ("VARCHAR", "BIGINT", "DECIMAL(18,2)", "DOUBLE", "DATE", "TIMESTAMP", "BOOLEAN")],
                         ["text", "int", "decimal", "decimal", "date", "datetime", "bool"])
        self.assertEqual(coltypes.target_dtype("decimal", 2, 10), "DECIMAL(18,2)")
        self.assertEqual(coltypes.target_dtype("decimal", 15, 30), "DOUBLE")


if __name__ == "__main__":
    unittest.main()
