"""Solo per i test: apre i CSV con l'inferenza dei tipi di DuckDB (comportamento precedente alla v0.17.0).

Dalla v0.17.0 l'applicazione apre TUTTO come testo e i tipi si assegnano solo su richiesta. Molti test storici
verificano filtri, ordinamenti, modifiche e salvataggi su colonne TIPIZZATE (numeri, date): quelle colonne restano
possibili (le crea «Tipi di dati»), quindi i test continuano a usare dati tipizzati, ottenuti qui con la vecchia lettura.
Il comportamento nuovo (tutto testo all'apertura, riconoscimento e conversione esplicita) è in test_phase6_*.py."""
from csv_reader.core.engine import CsvEngine

_original = CsvEngine.__dict__["_read_expr"]


def install() -> None:
    inner = _original.__func__

    def read_expr(path, params):
        return inner(path, {k: v for k, v in params.items() if k != "all_varchar"})

    CsvEngine._read_expr = staticmethod(read_expr)


def uninstall() -> None:
    CsvEngine._read_expr = _original


def suggested_types(engine) -> None:
    """Assegna a ogni colonna il tipo riconosciuto (come farebbe l'utente con «Usa i tipi riconosciuti»)."""
    for column in list(engine.columns):
        info = engine.analyze_column(column)
        if info["kind"] != "text" and not info["partial"]:
            engine.convert_column(column, info["kind"])
