"""Test funzionali della Fase 2 sul motore (nessuna interfaccia):
filtri avanzati, formattazione condizionale, trova/sostituisci, colonne e righe, salvataggio.

Esecuzione:  python -m unittest tests.test_phase2_core -v"""
import csv
import os
import unittest

from csv_reader.core.conditions import (
    CONDITIONS, condition_sql, filter_count, normalize_spec, parse_date, parse_number, validate_condition,
)
from csv_reader.core.engine import CsvEngine, ImportProblem, Scope
from tests.test_core import Base, sha, write

from tests import legacy_types


def setUpModule():
    legacy_types.install()


def tearDownModule():
    legacy_types.uninstall()


PEOPLE = [
    ["id", "nome", "eta", "data", "nota"],
    ["1", "Mario Rossi", "30", "2024-01-05", "ok"],
    ["2", "anna  bianchi ", "9", "2024-02-10", ""],
    ["3", "MARIO verdi", "100", "05/03/2024", "100% (a.b)"],
    ["4", "Luca", "", "", "a_b"],
    ["5", "mario", "45", "2023-12-31", "Mario"],
]


def read_csv(path):
    with open(path, encoding="utf-8", newline="") as fh:
        return list(csv.reader(fh))


class P2(Base):
    def open(self, rows=PEOPLE, name="p.csv"):
        path = self.p(name)
        write(path, ",", "utf-8", rows=rows)
        e = self.engine()
        e.open_csv(path)
        return e, path

    def col(self, e, name, **kw):
        cols, rows, ids = e.preview(0, 1000, **kw)
        i = cols.index(name)
        return [r[i] for r in rows]

    def ids(self, e, spec_by_col, search=""):
        return e.preview(0, 1000, search, spec_by_col)[2]


class TestConditions(P2):
    def test_all_ops_available_and_default_is_contains(self):
        self.assertEqual(CONDITIONS[0], ("contains", "Contiene"))
        self.assertEqual({k for k, _ in CONDITIONS},
                         {"eq", "ne", "contains", "not_contains", "starts", "ends", "gt", "lt", "empty", "not_empty"})
        self.assertEqual(normalize_spec({"value": "x"})["op"], "contains")
        self.assertEqual(normalize_spec("x")["op"], "contains")
        self.assertEqual(normalize_spec({"text": "x", "values": ["a"]})["value"], "x")   # forma precedente

    def test_text_ops_case_insensitive_by_default(self):
        e, _ = self.open()
        f = lambda op, v, **kw: self.ids(e, {"nome": {"op": op, "value": v, **kw}})
        self.assertEqual(f("contains", "mario"), [0, 2, 4])
        self.assertEqual(f("contains", "mario", case=True), [4])
        self.assertEqual(f("not_contains", "mario"), [1, 3])
        self.assertEqual(f("starts", "MARIO"), [0, 2, 4])
        self.assertEqual(f("starts", "MARIO", case=True), [2])
        self.assertEqual(f("ends", "ROSSI"), [0])
        self.assertEqual(f("eq", "MARIO"), [4])
        self.assertEqual(f("eq", "anna  bianchi"), [1])              # spazi finali ignorati
        self.assertEqual(f("ne", "mario"), [0, 1, 2, 3])

    def test_percent_and_underscore_are_literal(self):
        e, _ = self.open()
        self.assertEqual(self.ids(e, {"nota": {"op": "contains", "value": "%"}}), [2])
        self.assertEqual(self.ids(e, {"nota": {"op": "contains", "value": "_"}}), [3])
        self.assertEqual(self.ids(e, {"nota": {"op": "contains", "value": "a.b"}}), [2])
        self.assertEqual(self.ids(e, {"nota": {"op": "contains", "value": "'; DROP"}}), [])

    def test_numbers_compare_as_numbers_not_text(self):
        e, _ = self.open()
        self.assertEqual(e.dtypes["eta"], "BIGINT")
        self.assertEqual(self.ids(e, {"eta": {"op": "gt", "value": "9"}}), [0, 2, 4])     # 100 > 9 (non come testo)
        self.assertEqual(self.ids(e, {"eta": {"op": "lt", "value": "30"}}), [1])
        self.assertEqual(self.ids(e, {"eta": {"op": "eq", "value": "30,0"}}), [0])        # virgola decimale
        self.assertEqual(self.ids(e, {"eta": {"op": "ne", "value": "30"}}), [1, 2, 3, 4])  # NULL è «diverso»

    def test_dates(self):
        rows = [["d", "t"], ["2024-01-05", "25/12/2024"], ["2024-03-01", "01/01/2025"], ["", "x"]]
        e, _ = self.open(rows, "d.csv")
        self.assertEqual(e.dtypes["d"], "DATE")
        self.assertEqual(self.ids(e, {"d": {"op": "gt", "value": "2024-02-01"}}), [1])
        self.assertEqual(self.ids(e, {"d": {"op": "lt", "value": "10/03/2024"}}), [0, 1])  # data in formato italiano
        self.assertEqual(self.ids(e, {"d": {"op": "eq", "value": "05/01/2024"}}), [0])
        self.assertEqual(e.dtypes["t"], "VARCHAR")
        self.assertEqual(self.ids(e, {"t": {"op": "gt", "value": "31/12/2024"}}), [1])     # testo con date gg/mm/aaaa
        self.assertEqual(parse_date("31/02/2024"), None)
        self.assertEqual(parse_date("2024-02-29 10:30"), ("2024-02-29", "10:30:00"))
        self.assertEqual(parse_number("1,5"), 1.5)
        self.assertIsNone(parse_number("abc"))

    def test_empty_notempty_and_spaces(self):
        rows = [["a"], ["x"], [""], ["   "], ["y"]]
        e, _ = self.open(rows, "e.csv")
        self.assertEqual(self.ids(e, {"a": {"op": "empty"}}), [1, 2])          # NULL, '' e solo spazi
        self.assertEqual(self.ids(e, {"a": {"op": "not_empty"}}), [0, 3])

    def test_filters_are_anded_and_independent(self):
        e, _ = self.open()
        spec = {"nome": {"op": "contains", "value": "mario"}, "eta": {"op": "gt", "value": "40"}}
        self.assertEqual(self.ids(e, spec), [2, 4])
        self.assertEqual(e.count_filtered("", spec), 2)
        self.assertEqual(self.ids(e, {**spec, "nota": {"values": ["Mario"]}}), [4])    # + valori univoci
        self.assertEqual(filter_count({"op": "contains", "value": "x", "values": ["a", "b"]}), 3)
        # uno spazio è un valore cercabile per «contiene»; per «uguale» (che ignora gli spazi) il valore è vuoto
        self.assertEqual(filter_count({"op": "contains", "value": "  ", "values": []}), 1)
        self.assertEqual(filter_count({"op": "eq", "value": "  ", "values": []}), 0)
        self.assertEqual(filter_count({"op": "contains", "value": "", "values": []}), 0)

    def test_validation_messages(self):
        self.assertIsNone(validate_condition("BIGINT", "gt", "5"))
        self.assertIn("non è un numero", validate_condition("BIGINT", "eq", "abc"))
        self.assertIn("data valida", validate_condition("DATE", "lt", "ieri"))
        self.assertIsNone(validate_condition("VARCHAR", "contains", " "))
        self.assertIn("valore", validate_condition("VARCHAR", "contains", ""))
        self.assertIn("valore", validate_condition("VARCHAR", "eq", " "))
        self.assertIsNone(validate_condition("DATE", "empty", ""))

    def test_filters_with_sort_and_pages(self):
        e, _ = self.open()
        spec = {"nome": {"op": "contains", "value": "mario"}}
        cols, rows, ids = e.preview(0, 2, "", spec, ("eta", True))
        self.assertEqual([r[2] for r in rows], [100, 45])
        _, rows2, ids2 = e.preview(2, 2, "", spec, ("eta", True))
        self.assertEqual([r[2] for r in rows2], [30])
        self.assertEqual(ids + ids2, [2, 4, 0])


class TestConditionalFormatting(P2):
    def test_rules_colour_cells_with_precedence_and_hidden_columns(self):
        e, _ = self.open()
        rules = [
            {"enabled": True, "column": "eta", "op": "gt", "value": "40", "color": "#ff0000"},
            {"enabled": True, "column": "eta", "op": "gt", "value": "20", "color": "#00ff00"},    # dopo: vince la prima
            {"enabled": False, "column": "nome", "op": "contains", "value": "mario", "color": "#0000ff"},
            {"enabled": True, "column": "nome", "op": "starts", "value": "mario", "color": "#ffff00"},
            {"enabled": True, "column": "inesistente", "op": "empty", "color": "#123456"},
        ]
        cols, rows, ids, colors = e.preview_cf(0, 10, "", None, None, rules)
        eta, nome = cols.index("eta"), cols.index("nome")
        self.assertEqual(colors[0], {eta: "#00ff00", nome: "#ffff00"})       # 30: solo la seconda; Mario Rossi: inizia con
        self.assertEqual(colors[2][eta], "#ff0000")                           # 100: la prima regola prevale
        self.assertNotIn(nome, colors[1])                                     # regola disattivata ignorata
        self.assertEqual(rows[0][0], 1)                                       # i dati non cambiano

    def test_rules_follow_filters_and_sort_and_pages(self):
        e, _ = self.open()
        rules = [{"enabled": True, "column": "eta", "op": "gt", "value": "40", "color": "#ff0000"}]
        spec = {"nome": {"op": "contains", "value": "mario"}}
        _, rows, ids, colors = e.preview_cf(0, 2, "", spec, ("eta", False), rules)     # 30, 45
        self.assertEqual(ids, [0, 4])
        self.assertEqual([bool(c) for c in colors], [False, True])
        _, rows, ids, colors = e.preview_cf(2, 2, "", spec, ("eta", False), rules)     # seconda pagina: 100
        self.assertEqual(ids, [2])
        self.assertTrue(colors[0])

    def test_rules_do_not_alter_data_search_sql_or_saved_file(self):
        e, _ = self.open()
        e.preview_cf(0, 10, "", None, None, [{"enabled": True, "column": "nome", "op": "contains", "value": "m", "color": "#fff"}])
        self.assertEqual(e.run_query("SELECT count(*) FROM csv_data")[0][0][0], 5)
        self.assertEqual(e.count_filtered("mario", {}), 3)
        self.assertFalse(e.has_changes())
        e.save_csv(self.p("out.csv"))
        self.assertEqual(read_csv(self.p("out.csv"))[1][1], "Mario Rossi")


class TestEditsReachFiltersAndSql(P2):
    def test_cell_edit_is_visible_to_filters_search_sort_and_sql(self):
        e, path = self.open()
        before = sha(path)
        self.assertFalse(e.mutated)
        e.set_cell(3, "nome", "Giulia")
        self.assertTrue(e.mutated and e.has_changes())
        self.assertEqual(self.ids(e, {"nome": {"op": "eq", "value": "giulia"}}), [3])
        self.assertEqual(e.count_filtered("giulia", {}), 1)
        self.assertEqual(e.run_query("SELECT nome FROM csv_data WHERE nome = 'Giulia'")[0], [("Giulia",)])
        self.assertEqual(self.col(e, "nome", order=("nome", False))[0], "anna  bianchi ")
        self.assertEqual(sha(path), before)                              # l'originale non cambia mai

    def test_numeric_column_edit_and_incompatible_value_turns_column_to_text(self):
        e, _ = self.open()
        r = e.set_cell(0, "eta", "31")
        self.assertFalse(r["converted"])
        self.assertEqual(e.dtypes["eta"], "BIGINT")
        self.assertEqual(self.ids(e, {"eta": {"op": "gt", "value": "30"}}), [0, 2, 4])
        self.assertTrue(e.set_cell(1, "eta", "n.d.")["converted"])      # non è un numero: la colonna diventa testo
        self.assertEqual(e.dtypes["eta"], "VARCHAR")
        self.assertEqual(self.col(e, "eta"), ["31", "n.d.", "100", None, "45"])   # nessun valore perso
        self.assertEqual(self.ids(e, {"eta": {"op": "gt", "value": "40"}}), [2, 4])   # confronto ancora numerico sul testo

    def test_noop_edit_does_not_mark_changes(self):
        e, _ = self.open()
        self.assertFalse(e.set_cell(0, "nome", "Mario Rossi")["changed"])
        self.assertFalse(e.set_cell(3, "eta", "")["changed"])            # NULL e vuoto sono la stessa cosa
        self.assertFalse(e.mutated)
        self.assertFalse(e.has_changes())

    def test_row_identity_with_sort_and_filter(self):
        e, _ = self.open()
        cols, rows, ids = e.preview(0, 10, "", {"nome": {"op": "contains", "value": "mario"}}, ("eta", True))
        self.assertEqual(ids[0], 2)                                      # «MARIO verdi» (100), riga 3 del file
        e.set_cell(ids[0], "nota", "toccata")
        e.save_csv(self.p("o.csv"))
        out = read_csv(self.p("o.csv"))
        self.assertEqual(out[3][4], "toccata")
        self.assertEqual(out[1][4], "ok")


class TestReplace(P2):
    def scope(self, e, kind="all", **kw):
        return Scope(kind=kind, columns=kw.pop("columns", list(e.columns)), **kw)

    def test_replace_all_whole_dataset_case_insensitive(self):
        e, _ = self.open()
        report = e.replace_all("mario", "Marco", False, False, self.scope(e))
        self.assertEqual(report["cells"], 4)                             # 3 in «nome» + 1 in «nota»
        self.assertEqual(report["columns"], {"nome": 3, "nota": 1})
        self.assertEqual(self.col(e, "nome"), ["Marco Rossi", "anna  bianchi ", "Marco verdi", "Luca", "Marco"])
        self.assertTrue(e.has_changes())

    def test_case_sensitive_and_exact(self):
        e, _ = self.open()
        self.assertEqual(e.replace_all("Mario", "X", True, False, self.scope(e))["cells"], 2)    # «Mario Rossi», nota «Mario»
        self.assertEqual(self.col(e, "nome")[:3], ["X Rossi", "anna  bianchi ", "MARIO verdi"])
        e2, _ = self.open(name="q.csv")
        self.assertEqual(e2.replace_all("mario", "Z", False, True, self.scope(e2))["cells"], 2)  # cella intera: «mario» e nota «Mario»
        self.assertEqual(self.col(e2, "nome"), ["Mario Rossi", "anna  bianchi ", "MARIO verdi", "Luca", "Z"])

    def test_special_characters_are_literal(self):
        rows = [["t"], ["a.b (x) $1 \\ 100%"], ["aXb"], ["A.B"]]
        e, _ = self.open(rows, "s.csv")
        sc = self.scope(e)
        self.assertEqual(e.replace_all("a.b", "[$1]", False, False, sc)["cells"], 2)           # insensibile: «a.b» e «A.B», non «aXb»
        self.assertEqual(self.col(e, "t"), ["[$1] (x) $1 \\ 100%", "aXb", "[$1]"])
        self.assertEqual(e.replace_all("(x)", "\\1", False, False, sc)["cells"], 1)            # il backslash è letterale
        self.assertEqual(self.col(e, "t")[0], "[$1] \\1 $1 \\ 100%")
        self.assertEqual(e.replace_all("\\", "/", False, False, sc)["cells"], 1)
        self.assertEqual(e.replace_all("100%", "5%", True, False, sc)["cells"], 1)
        self.assertEqual(self.col(e, "t")[0], "[$1] /1 $1 / 5%")  # il backslash di «\\1» è stato sostituito come ogni altro

    def test_empty_find_rules(self):
        e, _ = self.open()
        with self.assertRaises(ImportProblem):
            e.replace_all("", "x", False, False, self.scope(e))
        r = e.replace_all("", "n.d.", False, True, self.scope(e, columns=["nota", "eta"]))      # celle vuote
        self.assertEqual(r["columns"], {"nota": 1, "eta": 1})
        self.assertEqual(self.col(e, "nota")[1], "n.d.")
        self.assertEqual(e.dtypes["eta"], "VARCHAR")                    # «n.d.» in colonna numerica -> testo
        self.assertEqual(r["converted"], ["eta"])

    def test_scope_columns_rows_cells_and_filtered(self):
        e, _ = self.open()
        # colonne selezionate: tutti i record, anche fuori pagina
        r = e.replace_all("mario", "M", False, False, self.scope(e, "columns", columns=["nome"]))
        self.assertEqual(r["cells"], 3)
        self.assertEqual(self.col(e, "nota")[4], "Mario")                # altra colonna intatta
        # righe selezionate: tutte le colonne, comprese le nascoste (qui «nota»)
        e2, _ = self.open(name="r.csv")
        r = e2.replace_all("mario", "M", False, False, Scope(kind="rows", columns=list(e2.columns), rids=[4]))
        self.assertEqual(r["columns"], {"nome": 1, "nota": 1})
        self.assertEqual(self.col(e2, "nome")[0], "Mario Rossi")         # altre righe intatte
        # celle selezionate
        e3, _ = self.open(name="c.csv")
        r = e3.replace_all("mario", "M", False, False, Scope(kind="cells", columns=["nome"], cells={"nome": [0, 4]}))
        self.assertEqual(r["cells"], 2)
        self.assertEqual(self.col(e3, "nome"), ["M Rossi", "anna  bianchi ", "MARIO verdi", "Luca", "M"])
        # solo record filtrati
        e4, _ = self.open(name="f.csv")
        spec = {"eta": {"op": "gt", "value": "40"}}
        r = e4.replace_all("mario", "M", False, False,
                           Scope(kind="all", columns=list(e4.columns), filtered=True, column_filters=spec))
        self.assertEqual(r["cells"], 3)                                  # righe con eta>40: «MARIO verdi» e «mario» (+ nota «Mario»)
        self.assertEqual(self.col(e4, "nome")[0], "Mario Rossi")

    def test_count_matches_does_not_modify(self):
        e, _ = self.open()
        self.assertEqual(sum(e.count_matches("mario", False, False, self.scope(e)).values()), 4)
        self.assertFalse(e.mutated)

    def test_replace_cell_only_that_cell(self):
        e, _ = self.open()
        self.assertEqual(e.replace_cell(0, "nome", "mario", "M", False, False)["cells"], 1)
        self.assertEqual(e.replace_cell(0, "nome", "mario", "M", False, False)["cells"], 0)    # già sostituito
        self.assertEqual(self.col(e, "nome")[:3], ["M Rossi", "anna  bianchi ", "MARIO verdi"])

    def test_replace_keeps_row_identity_after_sort(self):
        e, _ = self.open()
        _, _, ids = e.preview(0, 10, "", None, ("eta", True))
        e.replace_cell(ids[0], "nome", "mario", "X", False, False)
        self.assertEqual(self.col(e, "nome")[2], "X verdi")


class TestFindNext(P2):
    def find(self, e, find, after=None, scope=None, cols=None, **kw):
        scope = scope or Scope(kind="all", columns=list(e.columns))
        return e.find_next(find, kw.pop("case", False), kw.pop("exact", False), scope, cols or list(e.columns),
                           after=after, **kw)

    def test_walks_all_matches_in_view_order_and_wraps(self):
        e, _ = self.open()
        seen, after = [], None
        for _ in range(6):
            hit = self.find(e, "mario", after)
            seen.append((hit["pos"], hit["column"], hit["wrapped"]))
            after = (hit["pos"], list(e.columns).index(hit["column"]))
        self.assertEqual(seen, [(1, "nome", False), (3, "nome", False), (5, "nome", False), (5, "nota", False),
                                (1, "nome", True), (3, "nome", False)])

    def test_follows_filter_and_sort(self):
        e, _ = self.open()
        spec = {"eta": {"op": "gt", "value": "20"}}
        hit = self.find(e, "mario", None, column_filters=spec, order=("eta", True))
        self.assertEqual((hit["pos"], hit["rid"]), (1, 2))               # «MARIO verdi» (100) è la prima in ordine decrescente
        hit = self.find(e, "mario", (1, 1), column_filters=spec, order=("eta", True))
        self.assertEqual((hit["pos"], hit["rid"]), (2, 4))

    def test_scope_limits_the_search(self):
        e, _ = self.open()
        sc = Scope(kind="cells", columns=["nome"], cells={"nome": [4]})
        hit = self.find(e, "mario", None, scope=sc, cols=["nome"])
        self.assertEqual(hit["rid"], 4)
        self.assertIsNone(self.find(e, "mario", (5, 0), scope=Scope(kind="rows", columns=list(e.columns), rids=[1]), cols=["nome"]))
        self.assertIsNone(self.find(e, "zzz", None))

    def test_hidden_columns_are_skipped_in_navigation(self):
        e, _ = self.open()
        hit = self.find(e, "mario", (3, 1), cols=["nome"])               # «nota» è nascosta: salta alla riga 5 / nome
        self.assertEqual((hit["pos"], hit["column"]), (5, "nome"))

    def test_special_characters_and_empty_cells(self):
        e, _ = self.open()
        self.assertEqual(self.find(e, "100%", None)["rid"], 2)
        self.assertEqual(self.find(e, "a_b", None)["rid"], 3)
        self.assertEqual(self.find(e, "", None, exact=True, cols=["nota"])["rid"], 1)    # cella vuota


class TestStructure(P2):
    def test_add_column_validation_and_availability(self):
        e, path = self.open()
        with self.assertRaises(ImportProblem):
            e.add_column("   ")
        with self.assertRaises(ImportProblem):
            e.add_column("NOME")                                          # duplicato, anche con maiuscole diverse
        self.assertEqual(e.columns[-1], "nota")
        e.add_column("Nuova colonna")
        self.assertEqual(e.columns[-1], "Nuova colonna")
        self.assertEqual(e.count_total(), 5)                              # il numero di record non cambia
        self.assertEqual(self.col(e, "Nuova colonna"), [None] * 5)
        e.set_cell(1, "Nuova colonna", "x")
        self.assertEqual(self.ids(e, {"Nuova colonna": {"op": "eq", "value": "X"}}), [1])
        self.assertEqual(self.ids(e, {"Nuova colonna": {"op": "empty"}}), [0, 2, 3, 4])
        self.assertEqual(e.preview(0, 10, "", None, ("Nuova colonna", True))[2][0], 1)
        self.assertEqual(e.run_query("SELECT count(*) FROM csv_data WHERE \"Nuova colonna\" IS NULL")[0][0][0], 4)
        e.save_csv(self.p("o.csv"))
        out = read_csv(self.p("o.csv"))
        self.assertEqual(out[0], PEOPLE[0] + ["Nuova colonna"])           # ordine delle colonne = ordine mostrato
        self.assertEqual(out[2][-1], "x")

    def test_rename_column_keeps_values_and_rejects_duplicates(self):
        e, _ = self.open()
        before = self.col(e, "nome")
        with self.assertRaises(ImportProblem):
            e.rename_column("nome", "eta")
        with self.assertRaises(ImportProblem):
            e.rename_column("nome", "")
        self.assertEqual(e.rename_column("nome", "Cognome e nome"), "Cognome e nome")
        self.assertEqual(self.col(e, "Cognome e nome"), before)
        self.assertEqual(e.columns[1], "Cognome e nome")                  # posizione invariata
        self.assertEqual(e.dtypes["Cognome e nome"], "VARCHAR")
        self.assertEqual(self.ids(e, {"Cognome e nome": {"op": "contains", "value": "luca"}}), [3])
        self.assertEqual(e.rename_column("eta", "ETA"), "ETA")            # cambio di sole maiuscole consentito
        e.save_csv(self.p("o.csv"))
        self.assertEqual(read_csv(self.p("o.csv"))[0], ["id", "Cognome e nome", "ETA", "data", "nota"])

    def test_delete_column(self):
        e, _ = self.open()
        e.delete_column("eta")
        self.assertEqual(e.columns, ["id", "nome", "data", "nota"])
        self.assertEqual(e.preview(0, 1)[1][0], (1, "Mario Rossi", "2024-01-05", "ok"))
        with self.assertRaises(ImportProblem):
            e.delete_column("eta")
        self.assertEqual(e.run_query("SELECT * FROM csv_data LIMIT 1")[1], ["id", "nome", "data", "nota"])
        e.save_csv(self.p("o.csv"))
        self.assertEqual(read_csv(self.p("o.csv"))[0], ["id", "nome", "data", "nota"])
        one, _ = self.open([["solo"], ["a"]], "u.csv")
        with self.assertRaises(ImportProblem):
            one.delete_column("solo")

    def test_delete_rows_renumbers_and_add_row(self):
        e, _ = self.open()
        self.assertEqual(e.delete_rows([1, 3]), 2)
        self.assertEqual(self.col(e, "id"), [1, 3, 5])
        self.assertEqual(e.preview(0, 10)[2], [0, 1, 2])                  # il numero di riga resta quello del file salvato
        self.assertEqual(e.count_total(), 3)
        rid = e.add_row()
        self.assertEqual(rid, 3)
        self.assertEqual(e.count_total(), 4)
        e.set_cell(rid, "nome", "Nuovo")
        self.assertEqual(e.preview(0, 10)[1][-1][1], "Nuovo")
        e.save_csv(self.p("o.csv"))
        out = read_csv(self.p("o.csv"))
        self.assertEqual([r[0] for r in out], ["id", "1", "3", "5", ""])
        self.assertEqual(out[4][1], "Nuovo")

    def test_changes_survive_save_and_reopen(self):
        e, path = self.open()
        before = sha(path)
        e.rename_column("nome", "Nome")
        e.add_column("Extra")
        e.delete_column("data")
        e.replace_all("mario", "Marco", False, False, Scope(kind="all", columns=list(e.columns)))
        e.set_cell(0, "Extra", "e0")
        e.delete_rows([3])
        e.save_csv(self.p("saved.csv"))
        self.assertEqual(sha(path), before)
        e2 = self.engine()
        cols, n = e2.open_csv(self.p("saved.csv"))
        self.assertEqual(cols, ["id", "Nome", "eta", "nota", "Extra"])
        self.assertEqual(n, 4)
        rows = e2.preview(0, 10)[1]
        self.assertEqual([r[1] for r in rows], ["Marco Rossi", "anna  bianchi ", "Marco verdi", "Marco"])
        self.assertEqual(rows[0][4], "e0")
        self.assertEqual(rows[3][3], "Marco")                             # nota «Mario» sostituita

    def test_reload_discards_work_table(self):
        e, path = self.open()
        e.set_cell(0, "nome", "X")
        self.assertTrue(e.mutated)
        e.open_csv(path)
        self.assertFalse(e.mutated)
        self.assertFalse(e.has_changes())
        self.assertEqual(self.col(e, "nome")[0], "Mario Rossi")

    def test_global_expr_reflects_changes(self):
        e, _ = self.open()
        self.assertTrue(e.global_expr().startswith("read_csv"))
        e.set_cell(0, "nome", "ZZ")
        expr = e.global_expr()
        self.assertTrue(expr.startswith("read_parquet"))
        self.assertEqual(e.con.execute(f"SELECT nome FROM {expr} ORDER BY id LIMIT 1").fetchone()[0], "ZZ")
        e.set_cell(0, "nome", "YY")
        self.assertEqual(e.con.execute(f"SELECT nome FROM {e.global_expr()} ORDER BY id LIMIT 1").fetchone()[0], "YY")


class TestExcelPhase2(P2):
    def make(self):
        import openpyxl
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "Dati"
        ws.append(["ID", "Nome", "Importo"])
        for i in range(1, 5):
            ws.append([i, f"n{i}", i * 10.5])
        ws["F9"] = "=A2*2"
        wb.create_sheet("Altro")["A1"] = "resto"
        path = self.p("x.xlsx")
        wb.save(path)
        return path

    def test_value_edits_keep_other_content_and_structure_changes_rewrite_sheet(self):
        import openpyxl
        path = self.make()
        before = sha(path)
        e = self.engine()
        e.open_excel(path, "Dati", True)
        e.replace_all("n2", "due", False, False, Scope(kind="all", columns=list(e.columns)))
        r = e.save_excel_copy(self.p("a.xlsx"))
        self.assertFalse(r["rewritten"])
        ws = openpyxl.load_workbook(self.p("a.xlsx"))["Dati"]
        self.assertEqual(ws["B3"].value, "due")
        self.assertEqual(ws["F9"].value, "=A2*2")                         # formula mantenuta (nessuna modifica strutturale)
        e.rename_column("Nome", "Nominativo")
        e.add_column("Note")
        e.delete_rows([0])
        r = e.save_excel_copy(self.p("b.xlsx"))
        self.assertTrue(r["rewritten"])
        wb = openpyxl.load_workbook(self.p("b.xlsx"))
        ws = wb["Dati"]
        self.assertEqual([c.value for c in ws[1]][:4], ["ID", "Nominativo", "Importo", "Note"])
        # foglio riscritto: i valori delle colonne senza tipo assegnato sono scritti come testo (quello che si vede)
        self.assertEqual([c.value for c in ws[2]][:3], ["2", "due", "21"])
        self.assertEqual(ws.max_row, 4)                                   # 3 record rimasti + intestazione
        self.assertEqual(wb["Altro"]["A1"].value, "resto")                # altri fogli conservati
        self.assertEqual(sha(path), before)                               # originale intatto


if __name__ == "__main__":
    unittest.main()


class TestInsertPositions(unittest.TestCase):
    def setUp(self):
        import tempfile, os
        self.dir = tempfile.mkdtemp()
        self.path = os.path.join(self.dir, "p.csv")
        with open(self.path, "w") as fh:
            fh.write("a,b,c\n" + "".join(f"{i},x{i},y{i}\n" for i in range(5)))
        self.e = CsvEngine()
        self.e.open_csv(self.path)

    def test_add_column_after_given_column_keeps_values_and_order(self):
        self.e.add_column("n", after="a")
        self.assertEqual(self.e.columns, ["a", "n", "b", "c"])
        cols, rows, ids = self.e.preview(0, 10)
        self.assertEqual(cols, ["a", "n", "b", "c"])
        self.assertEqual(list(rows[2]), [2, None, "x2", "y2"])
        self.e.add_column("z", after="c")
        self.assertEqual(self.e.columns[-1], "z")
        self.e.add_column("e")
        self.assertEqual(self.e.columns[-1], "e")

    def test_add_row_after_given_record_renumbers_the_following(self):
        rid = self.e.add_row(after_rid=1)
        self.assertEqual(rid, 2)
        cols, rows, ids = self.e.preview(0, 10)
        self.assertEqual([r[0] for r in rows], [0, 1, None, 2, 3, 4])
        self.assertEqual(ids, [0, 1, 2, 3, 4, 5])
        self.assertEqual(self.e.add_row(), 6)
        self.assertEqual(self.e.add_row(after_rid=5), 6)
