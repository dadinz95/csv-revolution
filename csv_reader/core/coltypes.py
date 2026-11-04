"""Tipi di dato di una colonna: riconoscimento e conversione ESPLICITI.

All'apertura tutte le colonne sono testo (nessuna conversione automatica). Qui ci sono le espressioni SQL che:

* riconoscono se i valori di una colonna sono interi, decimali, date, date e ora o booleani;
* convertono la colonna nel tipo scelto dall'utente (``CsvEngine.convert_column``, annullabile).

Formati accettati (sul testo, dopo aver tolto gli spazi ai bordi; i vuoti restano vuoti):

* intero: ``123``, ``-5``, ``+7`` (fino a 18 cifre);
* decimale: ``1.50`` e ``1,50`` (la virgola è sempre un separatore decimale); con le migliaia solo se il raggruppamento
  è inequivocabile: ``1.234,56``, ``1.234.567``, ``1,234.56``, ``1,234,567``. ``1,234`` e ``1.234`` sono decimali;
* data: ``aaaa-mm-gg`` e ``gg/mm/aaaa`` (anche con ``-`` o ``.``: il giorno viene prima del mese);
* data e ora: come la data, con ora ``hh:mm`` o ``hh:mm:ss`` (la sola data vale mezzanotte);
* booleano: true/false, vero/falso, sì/no, yes/no, 1/0.

Il riconoscimento non propone mai un numero per identificatori con zeri iniziali (``00123``) o con più di 15 cifre
(codici, IUV, partite IVA…): restano testo.
"""
from __future__ import annotations

from csv_reader.core.conditions import quote_identifier, text_date_sql

#: (chiave, etichetta, tipo DuckDB di base)
TYPES: list[tuple[str, str, str]] = [
    ("text", "Testo", "VARCHAR"),
    ("int", "Numero intero", "BIGINT"),
    ("decimal", "Numero decimale", "DECIMAL"),
    ("date", "Data", "DATE"),
    ("datetime", "Data e ora", "TIMESTAMP"),
    ("bool", "Booleano", "BOOLEAN"),
]
LABELS = {k: label for k, label, _ in TYPES}
MAX_INT_DIGITS = 18
ID_DIGITS = 15            # più cifre di così: identificatore, non numero (Excel arrotonda oltre 15 cifre)
PARTIAL_RATIO = 0.9       # quota di valori validi oltre la quale si propone comunque un tipo («prevalentemente»)


def kind_of(dtype: str | None) -> str:
    """Tipo DuckDB -> chiave del tipo (text/int/decimal/date/datetime/bool); i tipi non previsti contano come testo."""
    t = (dtype or "VARCHAR").upper()
    if t == "DATE":
        return "date"
    if t.startswith("TIMESTAMP") or t == "DATETIME":
        return "datetime"
    if t == "BOOLEAN":
        return "bool"
    if t.startswith(("TINYINT", "SMALLINT", "INTEGER", "BIGINT", "HUGEINT", "UTINYINT", "USMALLINT", "UINTEGER",
                     "UBIGINT", "UHUGEINT", "INT")):
        return "int"
    if t.startswith(("DECIMAL", "DOUBLE", "FLOAT", "REAL")):
        return "decimal"
    return "text"


def text_of(ident: str) -> str:
    """Valore della colonna come testo ripulito; vuoto -> NULL."""
    return f"NULLIF(trim(CAST({ident} AS VARCHAR)), '')"


# ---- espressioni (v = testo ripulito, non vuoto o NULL)

def int_sql(v: str) -> str:
    return f"(CASE WHEN regexp_full_match({v}, '[+-]?\\d{{1,{MAX_INT_DIGITS}}}') THEN TRY_CAST({v} AS BIGINT) END)"


_DEC_PLAIN = r"[+-]?\d+([.,]\d+)?"
_DEC_IT = r"[+-]?\d{1,3}(\.\d{3})+,\d+|[+-]?\d{1,3}(\.\d{3}){2,}"          # 1.234,56  1.234.567
_DEC_EN = r"[+-]?\d{1,3}(,\d{3})+\.\d+|[+-]?\d{1,3}(,\d{3}){2,}"          # 1,234.56  1,234,567


def decimal_text_sql(v: str) -> str:
    """Testo -> numero normalizzato con il punto decimale (NULL se non è un numero)."""
    return ("(CASE "
            "WHEN regexp_full_match(" + v + ", '" + _DEC_PLAIN + "') THEN replace(" + v + ", ',', '.') "
            "WHEN regexp_full_match(" + v + ", '" + _DEC_IT + "') THEN replace(replace(" + v + ", '.', ''), ',', '.') "
            "WHEN regexp_full_match(" + v + ", '" + _DEC_EN + "') THEN replace(" + v + ", ',', '') END)")


def decimal_sql(v: str, precision: int, scale: int) -> str:
    return f"TRY_CAST({decimal_text_sql(v)} AS DECIMAL({precision},{scale}))"


def date_sql(v: str) -> str:
    # solo data: un testo con l'ora non è una data (si usa «Data e ora»)
    return (f"(CASE WHEN NOT regexp_matches({v}, '[ T]\\d{{1,2}}:\\d{{2}}') THEN {text_date_sql(v, False)} END)")


def datetime_sql(v: str) -> str:
    return text_date_sql(v, True)


def bool_sql(v: str) -> str:
    return (f"(CASE WHEN lower({v}) IN ('true','vero','si','sì','yes','1') THEN true "
            f"WHEN lower({v}) IN ('false','falso','no','0') THEN false END)")


def target_dtype(kind: str, scale: int = 2, digits: int = 18) -> str:
    """Tipo DuckDB di destinazione (come lo riporta DESCRIBE)."""
    if kind == "text":
        return "VARCHAR"
    if kind == "int":
        return "BIGINT"
    if kind == "date":
        return "DATE"
    if kind == "datetime":
        return "TIMESTAMP"
    if kind == "bool":
        return "BOOLEAN"
    if kind == "decimal":
        if digits + scale > 38:
            return "DOUBLE"
        return f"DECIMAL({18 if digits + scale <= 18 else 38},{scale})"
    raise ValueError(f"Tipo sconosciuto: {kind}")


def convert_sql(kind: str, ident: str, dtype: str) -> str:
    """Espressione che porta il valore della colonna ``ident`` nel tipo ``dtype`` (NULL = vuoto o non convertibile)."""
    if kind == "text":
        return f"CAST({ident} AS VARCHAR)"
    v = text_of(ident)
    if kind == "int":
        return int_sql(v)
    if kind == "decimal":
        if dtype == "DOUBLE":
            return f"TRY_CAST({decimal_text_sql(v)} AS DOUBLE)"
        p, s = dtype[len("DECIMAL("):-1].split(",")
        return decimal_sql(v, int(p), int(s))
    if kind == "date":
        return date_sql(v)
    if kind == "datetime":
        return datetime_sql(v)
    if kind == "bool":
        return bool_sql(v)
    raise ValueError(f"Tipo sconosciuto: {kind}")


# ---- riconoscimento

def analysis_sql(ident: str, source: str) -> str:
    """Una sola query che conta, per la colonna, i valori validi per ogni tipo."""
    v = text_of(ident)
    return f"""
SELECT count(v) AS nonempty,
       count(i) AS n_int,
       count(dn) AS n_decimal,
       count(d) AS n_date,
       count(t) AS n_datetime,
       count(b) AS n_bool,
       count(*) FILTER (WHERE regexp_full_match(v, '[+-]?0\\d+([.,]\\d+)?')) AS n_leading_zero,
       count(*) FILTER (WHERE regexp_full_match(v, '[+-]?\\d{{{ID_DIGITS + 1},}}')) AS n_long_digits,
       coalesce(max(CASE WHEN dn IS NOT NULL THEN length(split_part(dn, '.', 2)) END), 0) AS max_decimals,
       coalesce(max(CASE WHEN dn IS NOT NULL THEN length(regexp_replace(split_part(dn, '.', 1), '[^0-9]', '', 'g')) END), 0) AS max_int_digits
FROM (SELECT v, {int_sql('v')} AS i, {decimal_text_sql('v')} AS dn, {date_sql('v')} AS d,
             {datetime_sql('v')} AS t, {bool_sql('v')} AS b
      FROM (SELECT {v} AS v FROM {source}))
"""


def suggest(stats: dict) -> dict:
    """Proposta di tipo dai conteggi di ``analysis_sql``.

    Ritorna {"kind", "valid", "invalid", "partial", "reason"}. ``valid``/``invalid`` contano i valori non vuoti."""
    n = int(stats["nonempty"])
    if n == 0:
        return {"kind": "text", "valid": 0, "invalid": 0, "partial": False, "reason": "Nessun valore: colonna vuota."}
    if stats["n_leading_zero"] or stats["n_long_digits"]:
        return {"kind": "text", "valid": n, "invalid": 0, "partial": False,
                "reason": "Contiene identificatori (zeri iniziali o più di 15 cifre): meglio lasciarli testo."}
    counts = {"int": stats["n_int"], "decimal": stats["n_decimal"], "date": stats["n_date"],
              "datetime": stats["n_datetime"], "bool": stats["n_bool"]}
    for kind in ("int", "decimal", "date", "datetime", "bool"):
        if counts[kind] == n:
            return {"kind": kind, "valid": n, "invalid": 0, "partial": False, "reason": "Tutti i valori sono validi."}
    best = max(("int", "decimal", "date", "datetime", "bool"), key=lambda k: (counts[k], -("int decimal date datetime bool".split().index(k))))
    if counts[best] / n >= PARTIAL_RATIO:
        return {"kind": best, "valid": counts[best], "invalid": n - counts[best], "partial": True,
                "reason": f"Prevalentemente {LABELS[best].lower()}: {n - counts[best]} valori non convertibili."}
    return {"kind": "text", "valid": n, "invalid": 0, "partial": False, "reason": "Valori misti: nessun tipo adatto."}


def decimals_for(stats: dict) -> tuple[int, int]:
    """(cifre decimali minime, cifre intere) per un DECIMAL che non arrotonda nessun valore della colonna."""
    return int(stats["max_decimals"]), int(stats["max_int_digits"])


def count_invalid_sql(ident: str, expr: str, source: str) -> str:
    return f"SELECT count(*) FROM {source} WHERE {text_of(ident)} IS NOT NULL AND ({expr}) IS NULL"


def examples_sql(ident: str, expr: str, source: str, limit: int = 5) -> str:
    return (f"SELECT DISTINCT {text_of(ident)} FROM {source} WHERE {text_of(ident)} IS NOT NULL AND ({expr}) IS NULL "
            f"LIMIT {int(limit)}")


__all__ = ["TYPES", "LABELS", "kind_of", "target_dtype", "convert_sql", "analysis_sql", "suggest", "decimals_for",
           "count_invalid_sql", "examples_sql", "quote_identifier"]
