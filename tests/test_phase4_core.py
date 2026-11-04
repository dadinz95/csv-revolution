"""Fase 4 - motore: Annulla, incolla in blocco, salti Excel, appunti TSV, nuovo CSV.
Esecuzione:  python -m unittest tests.test_phase4_core -v"""
import os
import random
import tempfile
import unittest

from csv_reader.core import newcsv
from csv_reader.core.clipboard import format_tsv, parse_tsv
from csv_reader.core.engine import CsvEngine, ImportOptions, ImportProblem, Scope

from tests import legacy_types


def setUpModule():
    legacy_types.install()


def tearDownModule():
    legacy_types.uninstall()



def make(rows=12):
    d = tempfile.mkdtemp(prefix="p4core_")
    p = os.path.join(d, "a.csv")
    with open(p, "w") as fh:
        fh.write("id,n,t,dt\n" + "".join(f"{i},{i * 1.5},x{i},2024-01-{i % 28 + 1:02d}\n" for i in range(rows)))
    e = CsvEngine()
    e.open_csv(p)
    return e, d


def state(e):
    return e.con.execute("SELECT * FROM csv_data").fetchall(), list(e.columns), dict(e.dtypes)


class TestUndoEngine(unittest.TestCase):
    def test_every_operation_is_undone_to_identical_state(self):
        e, _ = make()
        s0 = state(e)
        self.assertFalse(e.can_undo())
        self.assertIsNone(e.undo())
        snaps = [s0]
        ops = [
            lambda: e.set_cell(3, "n", "abc"),                                  # converte la colonna in testo
            lambda: e.set_cell(2, "id", "99"),
            lambda: e.replace_all("x", "Y", True, False, Scope(kind="all", columns=["t", "id"])),
            lambda: e.set_cells([(0, "id", "7"), (1, "id", "q"), (1, "t", "zz")]),
            lambda: e.add_column("nuova", "id"),
            lambda: e.rename_column("t", "tt"),
            lambda: e.delete_column("dt"),
            lambda: e.delete_rows([1, 2, 5]),
            lambda: e.add_row(3),
        ]
        for op in ops:
            op()
            self.assertEqual(len(e._undo), len(snaps))          # ogni operazione ha una voce di cronologia
            snaps.append(state(e))
        self.assertEqual(e.change_count > 0, True)
        for expected in reversed(snaps[:-1]):
            self.assertIsNotNone(e.undo())
            self.assertEqual(state(e), expected)
        self.assertFalse(e.has_changes())
        self.assertEqual((e.change_count, e.structure_changed), (0, False))
        self.assertFalse(e.can_undo())

    def test_undo_after_save_marks_unsaved_again_and_back(self):
        e, _ = make()
        e.set_cell(1, "t", "A")
        e.mark_saved()
        self.assertFalse(e.has_changes())
        e.set_cell(2, "t", "B")
        self.assertTrue(e.has_changes())
        e.undo()
        self.assertFalse(e.has_changes())                 # tornati allo stato salvato
        e.undo()
        self.assertTrue(e.has_changes())                  # prima del salvataggio: ora differisce dal file salvato

    def test_set_cells_is_single_step_and_skips_unchanged(self):
        e, _ = make()
        r = e.set_cells([(0, "t", "x0"), (1, "t", "nuovo")])   # la prima cella è già uguale
        self.assertEqual(r["changed"], 1)
        self.assertEqual(len(e._undo), 1)
        self.assertEqual(e.set_cells([(0, "t", "x0")])["changed"], 0)
        self.assertEqual(len(e._undo), 1)

    def test_history_is_capped(self):
        e, _ = make()
        for i in range(e.UNDO_LIMIT + 10):
            e.set_cell(i % 12, "t", f"v{i}")
        self.assertEqual(len(e._undo), e.UNDO_LIMIT)
        n = 0
        while e.undo():
            n += 1
        self.assertEqual(n, e.UNDO_LIMIT)

    def test_random_sequence_roundtrip(self):
        rnd = random.Random(7)
        e, _ = make(30)
        s0 = state(e)
        for k in range(25):
            c = rnd.choice(["cell", "rows", "addrow", "ren", "addcol"])
            n = e.count_total()
            if c == "cell":
                e.set_cell(rnd.randrange(n), rnd.choice(["t", "id"]), rnd.choice(["a", "5", ""]))
            elif c == "rows" and n > 5:
                e.delete_rows(rnd.sample(range(n), 2))
            elif c == "addrow":
                e.add_row(rnd.randrange(n))
            elif c == "ren":
                e.rename_column(e.columns[-1], f"c{k}")
            elif c == "addcol":
                e.add_column(f"n{k}", e.columns[0])
        while e.undo():
            pass
        self.assertEqual(state(e), s0)


class TestViewHelpers(unittest.TestCase):
    def make_gaps(self):
        d = tempfile.mkdtemp(prefix="p4core_")
        p = os.path.join(d, "g.csv")
        with open(p, "w") as fh:
            fh.write("a,b\n")
            for i in range(20):
                fh.write(f"{'' if i in (5, 6, 7, 15) else f'v{i}'},{i}\n")
        e = CsvEngine()
        e.open_csv(p)
        return e

    def test_vertical_jumps_match_excel_rules(self):
        e = self.make_gaps()
        t = lambda pos, d: e.nav_vertical("a", pos, d)["target"]
        self.assertEqual(t(0, 1), 4)       # fine del blocco
        self.assertEqual(t(4, 1), 8)       # prima cella piena dopo il vuoto
        self.assertEqual(t(8, 1), 14)
        self.assertEqual(t(14, 1), 16)
        self.assertEqual(t(16, 1), 19)     # fino all'ultima riga
        self.assertEqual(t(19, 1), 19)
        self.assertEqual(t(8, -1), 4)
        self.assertEqual(t(10, -1), 8)
        self.assertEqual(t(0, -1), 0)
        self.assertEqual(t(6, 1), 8)       # da cella vuota: prossima piena
        self.assertEqual(t(6, -1), 4)

    def test_jumps_follow_filters_and_sort_and_view_rids(self):
        e = self.make_gaps()
        res = e.nav_vertical("b", 0, 1, column_filters=None, order=("b", True))
        self.assertEqual(res["target"], 19)
        rids = e.view_rids(0, 3, order=("b", True))
        self.assertEqual(rids, [19, 18, 17])
        rids = e.view_rids(2, 5, search="v1")
        self.assertTrue(all(isinstance(r, int) for r in rids))
        self.assertEqual(e.view_rids(0, 0), [])


class TestClipboardFormat(unittest.TestCase):
    def test_roundtrip_special_characters(self):
        rows = [["a", "b\tc", "d\ne", 'q"x', ""], ["1", "", "3", "", ""], ["à ü €", "\r\n", '"', "'", " "]]
        self.assertEqual(parse_tsv(format_tsv(rows)), rows)

    def test_excel_clipboard_shapes(self):
        self.assertEqual(parse_tsv("a\tb\r\nc\td\r\n"), [["a", "b"], ["c", "d"]])
        self.assertEqual(parse_tsv("x"), [["x"]])
        self.assertEqual(parse_tsv(""), [])
        self.assertEqual(parse_tsv("a\t\tb"), [["a", "", "b"]])
        self.assertEqual(parse_tsv('"abc'), [['"abc']])
        self.assertEqual(parse_tsv('"a ""q""\nb"\tz'), [['a "q"\nb', "z"]])


class TestNewCsv(unittest.TestCase):
    def test_creation_and_reopen_for_every_encoding_and_delimiter(self):
        d = tempfile.mkdtemp(prefix="p4core_")
        for cols, rows, dl, enc in [(3, 5, ",", "utf-8"), (1, 4, ";", "cp1252"), (4, 0, "\t", "utf-8-sig"),
                                    (2, 1000, "|", "utf-16"), (2, 3, ";", "latin-1")]:
            v = newcsv.validate(str(cols), str(rows), d, f"t{cols}_{rows}_{enc}", enc, dl)
            newcsv.create_csv(v["path"], cols, rows, dl, enc)
            e = CsvEngine()
            columns, total = e.open_csv(v["path"], ImportOptions(delimiter=dl, encoding=enc, header=True))
            self.assertEqual((columns, total), ([f"Colonna{i}" for i in range(1, cols + 1)], rows))

    def test_validation_messages(self):
        d = tempfile.mkdtemp(prefix="p4core_")
        cases = [(("", "1"), "Indica"), (("a", "1"), "numero intero"), (("0", "1"), "almeno 1"), (("3", "-1"), "almeno 0"),
                 (("3", "1.5"), "numero intero"), (("99999999", "1"), "non può superare"), (("5000", "10000000"), "celle")]
        for (c, r), text in cases:
            with self.assertRaises(ImportProblem) as cm:
                newcsv.validate(c, r, d, "x", "utf-8", ",")
            self.assertIn(text, str(cm.exception))
        for bad in ("", "a/b", "CON", ".."):
            with self.assertRaises(ImportProblem):
                newcsv.clean_file_name(bad)
        with self.assertRaises(ImportProblem):
            newcsv.validate("2", "2", os.path.join(d, "manca"), "x", "utf-8", ",")
        self.assertEqual(newcsv.clean_file_name("ok"), "ok.csv")
        self.assertEqual(newcsv.clean_file_name("ok.CSV"), "ok.CSV")

    def test_big_file_is_streamed_not_built_in_memory(self):
        import resource
        d = tempfile.mkdtemp(prefix="p4core_")
        before = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        path = os.path.join(d, "grande.csv")
        newcsv.create_csv(path, 20, 2_000_000, ",", "utf-8")
        grown_mb = (resource.getrusage(resource.RUSAGE_SELF).ru_maxrss - before) / 1024
        self.assertGreater(os.path.getsize(path), 35_000_000)
        self.assertLess(grown_mb, 60)
        with open(path) as fh:
            self.assertEqual(sum(1 for _ in fh), 2_000_001)


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class TestDocumentation(unittest.TestCase):
    def read(self, name):
        with open(os.path.join(ROOT, name), encoding="utf-8") as fh:
            return fh.read()

    def test_readme_lite_references_real_files_and_commands(self):
        import re
        text = self.read("README_LITE.md")
        for path in set(re.findall(r"(?:packaging[/\\][\w./\\-]+|requirements(?:-dev)?\.txt|run_csv_reader\.py)", text)):
            clean = path.replace("\\", "/").rstrip(".,)")
            if clean.endswith("/"):
                self.assertTrue(os.path.isdir(os.path.join(ROOT, clean)), clean)
            elif "/" in clean or "." in clean:
                self.assertTrue(os.path.exists(os.path.join(ROOT, clean)), clean)
        for needle in ("PyInstaller", "py -m venv .venv", "python3 -m venv .venv", "build.ps1", "build.sh", "sistema operativo di destinazione",
                       "Windows", "macOS", "Linux", "Inno Setup"):
            self.assertIn(needle, text)

    def test_project_description_sections_and_honesty(self):
        text = self.read("DESCRIZIONE_PROGETTO.md")
        for section in ("## Descrizione", "## Funzioni principali", "## Sistemi operativi", "## Tecnologie", "## Architettura",
                        "## Punti di forza", "## Limiti", "## Modalità di sviluppo"):
            self.assertIn(section, text)
        self.assertIn("vibe coded", text)
        self.assertIn("non è una garanzia di qualità", text)
        self.assertIn("Non verificato", text)
        for lic in ("licenza", "prezzo", "garanzia di funzionamento"):
            self.assertNotIn(lic, text.lower().replace("non è una garanzia di qualità", ""))

    def test_run_ps1_exists_and_is_documented(self):
        lines = [l.strip() for l in self.read("run.ps1").splitlines() if l.strip()]
        self.assertEqual(lines, ["Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass", "py -m venv .venv",
                                 r".\.venv\Scripts\Activate.ps1", "python -m pip install -r requirements.txt", "python run_csv_reader.py"])
        self.assertTrue(os.path.exists(os.path.join(ROOT, "run_csv_reader.py")))
        unix = '#!/bin/bash\ncd "$(dirname "$0")"\n\npython3 -m venv .venv\nsource .venv/bin/activate\npython -m pip install -r requirements.txt\npython run_csv_reader.py\n'
        for name in ("run.command", "run.sh"):
            self.assertEqual(self.read(name), unix, name)
        for name in ("README.md", "README_LITE.md"):
            for needle in ("run.ps1", "bash run.command", "bash run.sh"):
                self.assertIn(needle, self.read(name), (name, needle))

    def test_menu_rename_is_documented_everywhere(self):
        for name in ("README.md", "README_LITE.md", "DESCRIZIONE_PROGETTO.md"):
            self.assertNotIn("«Formattazione»", self.read(name).replace("«Formattazione» è ora", ""), name)

    def test_shortcut_registry_covers_required_commands(self):
        text = self.read(os.path.join("csv_reader", "desktop", "shortcuts.py"))
        for needle in ("Ctrl+Z", "Ctrl+C", "Ctrl+V", "Ctrl+Freccia", "Ctrl+Shift+Freccia", "Shift+Freccia", "Ctrl+S", "Ctrl+Shift+S"):
            self.assertIn(needle, text)


if __name__ == "__main__":
    unittest.main()
