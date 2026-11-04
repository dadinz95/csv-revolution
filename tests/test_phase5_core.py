"""Fase 5 - motore: operazioni atomiche, export protetto, sostituzioni reali, tetto di «Annulla».
Esecuzione:  python -m unittest tests.test_phase5_core -v"""
import os
import tempfile
import unittest
from unittest import mock

from csv_reader.core.engine import CsvEngine, ImportProblem, Scope, export_query_to_csv

from tests import legacy_types


def setUpModule():
    legacy_types.install()


def tearDownModule():
    legacy_types.uninstall()



def make(text=None, rows=12):
    d = tempfile.mkdtemp(prefix="p5core_")
    p = os.path.join(d, "a.csv")
    with open(p, "w") as fh:
        fh.write(text if text is not None else
                 "id,n,t\n" + "".join(f"{i},{i * 1.5},x{i}\n" for i in range(rows)))
    e = CsvEngine()
    e.open_csv(p)
    return e, d, p


class FailOn:
    """Connessione che fallisce sulle istruzioni che iniziano con ``prefix`` (simula un errore a metà operazione)."""

    def __init__(self, real, prefix):
        self._real, self._prefix = real, prefix

    def __getattr__(self, name):
        return getattr(self._real, name)

    def execute(self, sql, *args):
        if sql.startswith(self._prefix):
            raise RuntimeError("errore simulato")
        return self._real.execute(sql, *args)


def failing(e, prefix):
    """Context manager: da qui in poi le istruzioni che iniziano con ``prefix`` falliscono."""
    class _Ctx:
        def __enter__(self_):
            self_.real = e.con
            e.con = FailOn(e.con, prefix)

        def __exit__(self_, *exc):
            e.con = self_.real
    return _Ctx()


def snapshot(e):
    return e.con.execute("SELECT * FROM csv_data").fetchall(), list(e.columns), dict(e.dtypes)


class TestAtomicOperations(unittest.TestCase):
    def test_failure_in_second_column_leaves_nothing_half_done(self):
        e, _, _ = make("a,b\nx,x\nx,x\n")
        before = snapshot(e)
        real = e._assign
        calls = []

        def flaky(*args, **kwargs):
            calls.append(args[0])
            if len(calls) == 2:
                raise RuntimeError("errore simulato alla seconda colonna")
            return real(*args, **kwargs)

        with mock.patch.object(e, "_assign", side_effect=flaky):
            with self.assertRaises(RuntimeError):
                e.replace_all("x", "y", True, False, Scope(kind="all", columns=["a", "b"]))
        self.assertEqual(snapshot(e), before)                       # la prima colonna è tornata com'era
        self.assertFalse(e.has_changes())
        self.assertFalse(e.can_undo())
        self.assertIsNone(e._backups)
        # e il motore resta utilizzabile
        self.assertEqual(e.replace_all("x", "y", True, False, Scope(kind="all", columns=["a", "b"]))["cells"], 4)

    def test_failed_type_conversion_restores_column_type(self):
        e, _, _ = make("n\n1\n2\n")
        dtype = e.dtypes["n"]
        self.assertNotEqual(dtype, "VARCHAR")
        e.set_cell(0, "n", "5")                                       # materializza (dati reali in wk.work)
        before = snapshot(e)
        n_undo = len(e._undo)
        with failing(e, "UPDATE wk.work SET"):                        # il tipo è già cambiato quando l'UPDATE fallisce
            with self.assertRaises(RuntimeError):
                e.set_cell(1, "n", "abc")
        self.assertEqual(e.dtypes["n"], dtype)
        self.assertEqual(e.con.execute("SELECT typeof(n) FROM csv_data LIMIT 1").fetchone()[0], dtype)
        self.assertEqual(snapshot(e), before)
        self.assertEqual(len(e._undo), n_undo)

    def test_failed_structure_operations_restore_columns(self):
        e, _, _ = make()
        e.set_cell(0, "t", "q")
        before = snapshot(e)
        n_undo = len(e._undo)
        with mock.patch.object(e, "_undo_table", side_effect=RuntimeError("boom")):
            with self.assertRaises(RuntimeError):
                e.delete_column("t")
            with self.assertRaises(RuntimeError):
                e.delete_rows([1, 2])
        with failing(e, "ALTER TABLE wk.work RENAME COLUMN"):
            with self.assertRaises(RuntimeError):
                e.rename_column("t", "tt")
        with failing(e, "ALTER TABLE wk.work DROP COLUMN"):
            with self.assertRaises(RuntimeError):
                e.delete_column("t")
        with failing(e, "ALTER TABLE wk.work ADD COLUMN"):
            with self.assertRaises(RuntimeError):
                e.add_column("z", "id")
        self.assertEqual(snapshot(e), before)
        self.assertEqual(len(e._undo), n_undo)
        tables = e.con.execute("SELECT count(*) FROM duckdb_tables() WHERE database_name = 'wk' "
                               "AND table_name LIKE 'undo_%'").fetchone()[0]
        self.assertEqual(tables, n_undo)                              # nessun backup orfano

    def test_failed_undo_keeps_entry_and_state(self):
        e, _, _ = make()
        e.set_cell(1, "t", "ZZ")
        e.delete_column("n")
        before = snapshot(e)
        n = len(e._undo)
        with failing(e, "UPDATE wk.work SET"):                        # dopo ADD COLUMN, mentre rimette i valori
            with self.assertRaises(RuntimeError):
                e.undo()
        self.assertEqual(len(e._undo), n)                             # la voce resta: si può riprovare
        self.assertEqual(snapshot(e), before)                         # la colonna non è riapparsa a metà
        e.undo()
        self.assertIn("n", e.columns)
        self.assertEqual(e.con.execute("SELECT n FROM csv_data WHERE id = 2").fetchone()[0], 3.0)


class TestRealReplacements(unittest.TestCase):
    def test_replacement_that_changes_nothing_is_not_a_change(self):
        e, _, _ = make("t\nabc\nABC\nxyz\n")
        report = e.replace_all("abc", "abc", False, False, Scope(kind="all", columns=["t"]))
        self.assertEqual(report["cells"], 1)                          # solo «ABC» cambia («abc» resta uguale)
        self.assertEqual(e.con.execute("SELECT t FROM csv_data ORDER BY 1").fetchall(), [("abc",), ("abc",), ("xyz",)])
        e2, _, _ = make("t\nabc\nxyz\n")
        report = e2.replace_all("abc", "abc", True, False, Scope(kind="all", columns=["t"]))
        self.assertEqual(report["cells"], 0)
        self.assertFalse(e2.has_changes())
        self.assertFalse(e2.can_undo())

    def test_count_matches_with_replacement_counts_only_real_changes(self):
        e, _, _ = make("t\nabc\nABC\n")
        scope = Scope(kind="all", columns=["t"])
        self.assertEqual(e.count_matches("abc", False, False, scope), {"t": 2})
        self.assertEqual(e.count_matches("abc", False, False, scope, "abc"), {"t": 1})

    def test_replace_cell_without_effect_is_not_a_change(self):
        e, _, _ = make("t\nabc\n")
        self.assertEqual(e.replace_cell(0, "t", "abc", "abc", True, False)["cells"], 0)
        self.assertFalse(e.has_changes())
        self.assertEqual(e.replace_cell(0, "t", "abc", "q", True, False)["cells"], 1)
        self.assertTrue(e.has_changes())


class TestSafeExport(unittest.TestCase):
    def test_export_to_the_open_source_file_is_refused_and_source_intact(self):
        e, _, p = make()
        original = open(p, "rb").read()
        with self.assertRaises(ImportProblem) as cm:
            e.export_query("SELECT * FROM csv_data WHERE id < 3", p)
        self.assertIn("aperto", str(cm.exception))
        self.assertEqual(open(p, "rb").read(), original)

    def test_export_to_another_open_file_is_refused(self):
        e, d, _ = make()
        other = os.path.join(d, "altro.csv")
        open(other, "w").write("k\n1\n")
        with self.assertRaises(ImportProblem):
            e.export_query("SELECT 1 AS k", other, blocked=[other])
        self.assertEqual(open(other).read(), "k\n1\n")

    def test_failed_export_keeps_previous_file_and_leaves_no_temp(self):
        e, d, _ = make()
        out = os.path.join(d, "out.csv")
        open(out, "w").write("vecchio\n")
        with self.assertRaises(Exception):
            e.export_query("SELECT * FROM tabella_inesistente", out)
        self.assertEqual(open(out).read(), "vecchio\n")
        self.assertEqual([f for f in os.listdir(d) if f.endswith(".tmp")], [])

    def test_trailing_comment_and_semicolon(self):
        e, d, _ = make()
        out = os.path.join(d, "out.csv")
        for sql in ("SELECT id FROM csv_data LIMIT 2 -- solo due righe", "SELECT id FROM csv_data LIMIT 2;",
                    "SELECT id FROM csv_data LIMIT 2; -- commento", "SELECT id FROM csv_data LIMIT 2;\n"):
            if os.path.exists(out):
                os.remove(out)
            e.export_query(sql, out)
            self.assertEqual(open(out).read().split(), ["id", "0", "1"], sql)
        self.assertEqual([f for f in os.listdir(d) if f.endswith(".tmp")], [])

    def test_export_helper_creates_a_new_file(self):
        e, d, _ = make()
        out = os.path.join(d, "nuovo.csv")
        export_query_to_csv(e.con, "SELECT 1 AS k", out)
        self.assertEqual(open(out).read().split(), ["k", "1"])


class TestUndoDiskCap(unittest.TestCase):
    def test_oldest_entries_dropped_when_backups_exceed_cap(self):
        e, _, _ = make(rows=200)
        e.UNDO_BYTES_LIMIT = 1                                         # qualunque backup supera il tetto
        for i in range(4):
            e.set_cell(i, "t", f"v{i}")
        self.assertEqual(len(e._undo), 1)                              # si conserva sempre l'ultima operazione
        tables = e.con.execute("SELECT table_name FROM duckdb_tables() WHERE database_name = 'wk' "
                               "AND table_name LIKE 'undo_%'").fetchall()
        self.assertEqual(len(tables), 1)                               # e i backup scartati sono stati eliminati
        self.assertTrue(e.can_undo())
        e.undo()
        self.assertEqual(e.con.execute("SELECT t FROM csv_data WHERE id = 3").fetchone()[0], "x3")

    def test_default_cap_keeps_normal_history(self):
        e, _, _ = make()
        for i in range(5):
            e.set_cell(i, "t", f"v{i}")
        self.assertEqual(len(e._undo), 5)


class TestEmptyValues(unittest.TestCase):
    """NULL e testo vuoto sono lo stesso valore in tutto il programma."""

    def open(self):
        # «b» ha celle assenti (NULL), una cella con testo vuoto scritto a mano e una con soli spazi
        e, d, p = make('a,b\n1,x\n2,\n3,y\n4,z\n5,w\n')
        e.set_cell(2, "b", "")                  # testo vuoto scritto dall'utente
        e.set_cell(3, "b", "   ")               # solo spazi
        return e

    def test_distinct_values_merge_null_and_empty(self):
        e = self.open()
        values, _ = e.distinct_values("b")
        self.assertEqual([v for v, _n in values if v is None], [None])
        self.assertEqual(dict(values)[None], 2)                       # NULL (riga 2) + testo vuoto (riga 3)
        self.assertEqual(sum(1 for v, _n in values if v == ""), 0)

    def test_value_filter_for_empty_matches_both(self):
        e = self.open()
        ids = lambda spec: [r[0] for r in e.con.execute(
            "SELECT a FROM csv_data" + e_where(e, spec)[0] + " ORDER BY a", e_where(e, spec)[1]).fetchall()]
        self.assertEqual(ids({"b": {"values": [None]}}), [2, 3])
        self.assertEqual(ids({"b": {"values": [None, "x"]}}), [1, 2, 3])

    def test_empty_condition_and_navigation_agree(self):
        e = self.open()
        spec = {"b": {"op": "empty", "value": ""}}
        ids = [r[0] for r in e.con.execute("SELECT a FROM csv_data" + e_where(e, spec)[0] + " ORDER BY a").fetchall()]
        self.assertEqual(ids, [2, 3, 4])                              # NULL, vuoto e solo spazi
        # b = x, NULL, testo vuoto, solo spazi, w: le righe 2-4 sono tutte «vuote» per i salti
        self.assertEqual(e.nav_vertical("b", 0, +1)["target"], 4)     # dalla piena x alla prima piena che segue (w)
        self.assertEqual(e.nav_vertical("b", 4, -1)["target"], 0)     # da w verso l'alto: la prima piena è x
        self.assertEqual(e.nav_vertical("b", 1, +1)["target"], 4)     # da una vuota alla prima piena

    def test_sort_puts_all_empty_values_last_together(self):
        e = self.open()                                               # b = x, NULL, '', '   ', w (a = 1..5)
        order = lambda desc: [r[0] for r in e.preview(0, 10, "", None, ("b", desc))[1]]
        self.assertEqual(order(False), [4, 5, 1, 2, 3])               # «   », w, x; poi NULL e vuoto insieme, in fondo
        self.assertEqual(order(True), [1, 5, 4, 2, 3])


def e_where(engine, spec):
    from csv_reader.core.conditions import build_where
    return build_where(engine.columns, engine.dtypes, "", spec)


class TestNoSilentRounding(unittest.TestCase):
    def test_decimals_into_integer_column_turn_it_into_text(self):
        e, _, _ = make("n\n1\n2\n3\n")
        self.assertIn("INT", e.dtypes["n"].upper())
        result = e.set_cell(0, "n", "1,5")
        self.assertTrue(result["converted"])
        self.assertEqual(e.dtypes["n"], "VARCHAR")
        self.assertEqual(e.con.execute("SELECT n FROM csv_data ORDER BY 1").fetchall(), [("1,5",), ("2",), ("3",)])

    def test_integer_valued_decimals_are_accepted(self):
        e, _, _ = make("n\n1\n2\n")
        for text in ("7", "8,0", " 9 ", "10.0"):
            self.assertFalse(e.set_cell(0, "n", text)["converted"], text)
        self.assertIn("INT", e.dtypes["n"].upper())
        self.assertEqual(e.con.execute("SELECT n FROM csv_data WHERE n IS NOT NULL ORDER BY 1").fetchall(), [(2,), (10,)])

    def test_paste_and_replace_do_not_round_either(self):
        e, _, _ = make("n,m\n1,1\n2,2\n")
        r = e.set_cells([(0, "n", "2,5"), (1, "m", "4")])
        self.assertEqual(r["converted"], ["n"])
        self.assertIn("INT", e.dtypes["m"].upper())
        e2, _, _ = make("n\n10\n20\n")
        report = e2.replace_all("0", ",5", True, False, Scope(kind="all", columns=["n"]))
        self.assertEqual(report["converted"], ["n"])
        self.assertEqual(e2.con.execute("SELECT n FROM csv_data ORDER BY 1").fetchall(), [("1,5",), ("2,5",)])

    def test_decimal_type_does_not_lose_digits(self):
        e, _, _ = make("p\n1\n")
        cast = lambda text, dtype: e.con.execute(f"SELECT {CsvEngine._typed(repr(text), dtype)}").fetchone()[0]
        self.assertIsNone(cast("1,2345", "DECIMAL(18,2)"))                # arrotonderebbe a 1.23
        self.assertEqual(str(cast("1,20", "DECIMAL(18,2)")), "1.20")
        self.assertEqual(str(cast("-7", "DECIMAL(18,2)")), "-7.00")
        self.assertIsNone(cast("2,5", "SMALLINT"))
        self.assertEqual(cast("2,0", "SMALLINT"), 2)
        self.assertIsNone(cast("99999", "SMALLINT"))                      # fuori intervallo
        self.assertIsNone(cast("abc", "BIGINT"))

    def test_float_column_keeps_precision(self):
        e, _, _ = make("p\n1.5\n2.5e0\n")
        e.set_cell(0, "p", "3,14159")
        self.assertAlmostEqual(e.con.execute("SELECT max(p) FROM csv_data").fetchone()[0], 3.14159)


class TestTextDates(unittest.TestCase):
    def ids(self, e, op, value):
        where, params = e_where(e, {"d": {"op": op, "value": value}})
        return [r[0] for r in e.con.execute("SELECT i FROM csv_data" + where + " ORDER BY i", params).fetchall()]

    def open(self):
        e, _, _ = make("i,d\n1,05-03-2024\n2,2024-01-15\n3,5.3.2024 10:30\n4,31/12/2023\n5,2024-02-30\n6,non data\n7,2024-03-05 08:00\n")
        e.con.execute("SELECT 1")
        # le date sono lette come testo: forza il tipo di testo anche se DuckDB ne avesse dedotto un altro
        self.assertEqual(e.dtypes["d"], "VARCHAR")
        return e

    def test_all_accepted_formats_are_compared_as_dates(self):
        e = self.open()
        self.assertEqual(self.ids(e, "gt", "01-01-2024"), [1, 2, 3, 7])        # 02-30 e «non data» sono esclusi
        self.assertEqual(self.ids(e, "lt", "2024-01-01"), [4])
        self.assertEqual(self.ids(e, "gt", "5/3/2024"), [])                    # solo il giorno: le ore non contano
        self.assertEqual(self.ids(e, "lt", "6.3.2024"), [1, 2, 3, 4, 7])

    def test_time_is_compared_when_filter_has_time(self):
        e = self.open()
        self.assertEqual(self.ids(e, "gt", "05/03/2024 09:00"), [3])
        self.assertEqual(self.ids(e, "lt", "2024-03-05 09:00"), [1, 2, 4, 7])


class TestSpaceSearch(unittest.TestCase):
    def test_contains_a_single_space(self):
        e, _, _ = make('t\n"a b"\nab\n" "\n')
        where, params = e_where(e, {"t": {"op": "contains", "value": " "}})
        rows = e.con.execute("SELECT t FROM csv_data" + where + " ORDER BY 1", params).fetchall()
        self.assertEqual(rows, [(" ",), ("a b",)])
        where, params = e_where(e, {"t": {"op": "starts", "value": "a "}})
        self.assertEqual(e.con.execute("SELECT t FROM csv_data" + where, params).fetchall(), [("a b",)])


class TestConcurrencyAndOrder(unittest.TestCase):
    def test_row_identity_follows_file_order_with_many_threads(self):
        d = tempfile.mkdtemp(prefix="p5order_")
        p = os.path.join(d, "big.csv")
        with open(p, "w") as fh:
            fh.write("id,t\n" + "".join(f"{i},v{i}\n" for i in range(300_000)))
        e = CsvEngine()
        e.con.execute("SET threads = 8")
        self.assertTrue(e.con.execute("SELECT current_setting('preserve_insertion_order')").fetchone()[0])
        e.open_csv(p)
        e.set_cell(5, "t", "x")                                       # materializza: assegna gli identificativi
        bad = e.con.execute('SELECT count(*) FROM wk.work WHERE "__csv_row_idx" <> id').fetchone()[0]
        self.assertEqual(bad, 0)
        _cols, rows, rids = e.preview(299_990, 5)
        self.assertEqual([int(r[0]) for r in rows], rids)

    def test_global_queries_while_another_thread_edits(self):
        import threading
        from csv_reader.core.engine import GlobalEngine
        e, _, _ = make(rows=2000)
        e.set_cell(0, "t", "start")
        g = GlobalEngine()
        errors = []

        def editor():
            try:
                for i in range(40):
                    e.set_cell(i, "t", f"edit{i}")
                    if i % 10 == 0:
                        e.add_row()
                        e.delete_rows([1999])
            except Exception as exc:                                  # pragma: no cover
                errors.append(("editor", exc))

        t = threading.Thread(target=editor)
        t.start()
        try:
            for _ in range(25):
                rows, cols = g.run_query({"t1": e}, "SELECT count(*) AS n, count(t) AS m FROM t1")
                self.assertEqual(cols, ["n", "m"])
                self.assertGreater(rows[0][0], 1000)
        except Exception as exc:                                      # pragma: no cover
            errors.append(("query", exc))
        t.join()
        self.assertEqual(errors, [])
        rows, cols = g.run_query({"t1": e}, "SELECT count(*) FROM t1 WHERE t LIKE 'edit%'")
        self.assertGreaterEqual(rows[0][0], 39)

    def test_cached_export_follows_version(self):
        from csv_reader.core.engine import GlobalEngine
        e, _, _ = make()
        g = GlobalEngine()
        e.set_cell(0, "t", "uno")
        self.assertEqual(g.run_query({"t1": e}, "SELECT t FROM t1 WHERE id = 0")[0], [("uno",)])
        e.set_cell(0, "t", "due")
        self.assertEqual(g.run_query({"t1": e}, "SELECT t FROM t1 WHERE id = 0")[0], [("due",)])
        e.undo()
        self.assertEqual(g.run_query({"t1": e}, "SELECT t FROM t1 WHERE id = 0")[0], [("uno",)])


if __name__ == "__main__":
    unittest.main()
