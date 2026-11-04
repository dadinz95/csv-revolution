"""Condizioni su una colonna (filtri e formattazione condizionale) tradotte in SQL DuckDB.

Un'unica implementazione serve filtri, formattazione condizionale e ricerca/sostituzione, così il
significato di «contiene», «maggiore di», «vuoto»… è identico ovunque.

Regole di confronto (documentate anche nel README):

* testo: insensibile a maiuscole/minuscole salvo ``case=True``; «uguale/diverso» ignorano gli spazi
  iniziali e finali (in una colonna di testo il confronto è sempre tra testi: «1.0» e «1» sono diversi);
  «contiene», «inizia con» e «termina con» non tolgono gli spazi, quindi si può cercare anche solo " ";
  i confronti testuali non usano LIKE, quindi ``%`` e ``_`` sono caratteri normali;
* numeri: colonne numeriche si confrontano come numeri; «maggiore/minore» su una colonna di testo che contiene
  numeri (anche con la virgola decimale) li confronta come numeri, quindi 9 < 10 < 100. La virgola è sempre il
  separatore decimale;
* date: colonne DATE/TIMESTAMP e, per «maggiore/minore», testo in formato aaaa-mm-gg o gg/mm/aaaa (anche con - o .,
  giorni e mesi a una cifra, con o senza ora) si confrontano come date;
* NULL: nessun confronto positivo (uguale, contiene, maggiore…) lo soddisfa; lo soddisfano invece
  «diverso da» e «non contiene», perché un valore assente è diverso da qualunque valore;
* «vuoto» = NULL, stringa vuota o solo spazi. Un valore assente (NULL) e un testo vuoto sono la stessa cosa in
  ogni punto del programma: filtro per valore «(vuoto)», elenco dei valori, ordinamento, salti con Ctrl+Freccia.
"""
from __future__ import annotations

import datetime as dt
import re

CONDITIONS: list[tuple[str, str]] = [
    ("contains", "Contiene"),
    ("eq", "Uguale a"),
    ("ne", "Diverso da"),
    ("not_contains", "Non contiene"),
    ("starts", "Inizia con"),
    ("ends", "Termina con"),
    ("gt", "Maggiore di"),
    ("lt", "Minore di"),
    ("empty", "Vuoto"),
    ("not_empty", "Non vuoto"),
]
CONDITION_LABELS = dict(CONDITIONS)
NO_VALUE_OPS = {"empty", "not_empty"}
TEXT_OPS = {"contains", "not_contains", "starts", "ends"}       # il valore non viene ripulito dagli spazi
DEFAULT_OP = "contains"
EMPTY_LABEL = "(vuoto)"


def quote_identifier(identifier: str) -> str:
    """Quote an SQL identifier safely."""
    return '"' + identifier.replace('"', '""') + '"'


def sql_literal(value) -> str:
    """Valore Python -> letterale SQL (usato solo per mostrare la query generata)."""
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, (int, float)):
        return repr(value)
    return "'" + str(value).replace("'", "''") + "'"


def inline_params(sql: str, params: list) -> str:
    """Sostituisce i segnaposto ``?`` con i valori, per mostrare la query nell'editor SQL."""
    it = iter(params)
    return re.sub(r"\?", lambda _m: sql_literal(next(it)), sql)


# --------------------------------------------------------------------------- tipi e valori

def type_family(dtype: str | None) -> str:
    t = (dtype or "VARCHAR").upper()
    if t == "DATE":
        return "date"
    if t.startswith("TIMESTAMP") or t == "DATETIME":
        return "timestamp"
    if (t.startswith(("TINYINT", "SMALLINT", "INTEGER", "BIGINT", "HUGEINT", "UTINYINT", "USMALLINT", "UINTEGER",
                      "UBIGINT", "UHUGEINT", "FLOAT", "DOUBLE", "DECIMAL", "REAL", "INT")) and not t.startswith("INTERVAL")):
        return "num"
    return "text"


_NUM_RE = re.compile(r"^[+-]?(\d+([.,]\d+)?|[.,]\d+)([eE][+-]?\d+)?$")


def parse_number(text) -> float | None:
    s = ("" if text is None else str(text)).strip().replace(" ", "")
    if not s or not _NUM_RE.match(s):
        return None
    try:
        return float(s.replace(",", "."))
    except ValueError:
        return None


_ISO_RE = re.compile(r"^(\d{4})-(\d{1,2})-(\d{1,2})(?:[ T](\d{1,2}):(\d{2})(?::(\d{2})(?:\.\d+)?)?)?$")
_IT_RE = re.compile(r"^(\d{1,2})[/.-](\d{1,2})[/.-](\d{4})(?:[ T](\d{1,2}):(\d{2})(?::(\d{2}))?)?$")


def parse_date(text) -> tuple[str, str | None] | None:
    """Testo -> (data ISO, ora ISO o None). Accetta aaaa-mm-gg e gg/mm/aaaa (anche con - o .)."""
    s = ("" if text is None else str(text)).strip()
    m = _ISO_RE.match(s)
    if m:
        y, mo, d = int(m[1]), int(m[2]), int(m[3])
    else:
        m = _IT_RE.match(s)
        if not m:
            return None
        d, mo, y = int(m[1]), int(m[2]), int(m[3])
    try:
        day = dt.date(y, mo, d)
    except ValueError:
        return None
    time = None
    if m[4] is not None:
        try:
            time = dt.time(int(m[4]), int(m[5]), int(m[6] or 0)).isoformat()
        except ValueError:
            return None
    return day.isoformat(), time


# --------------------------------------------------------------------------- specifica di filtro

def normalize_spec(spec) -> dict:
    """Specifica di filtro in forma canonica: {"op", "value", "case", "values"}.

    Accetta anche le forme precedenti: una stringa o {"text": ..., "values": [...]} (= «contiene»)."""
    if isinstance(spec, str):
        spec = {"op": "contains", "value": spec}
    spec = dict(spec or {})
    if "text" in spec and "value" not in spec:
        spec["value"] = spec.get("text") or ""
        spec.setdefault("op", "contains")
    op = spec.get("op") or DEFAULT_OP
    if op not in CONDITION_LABELS:
        op = DEFAULT_OP
    return {"op": op, "value": "" if spec.get("value") is None else str(spec.get("value")),
            "case": bool(spec.get("case")), "values": list(spec.get("values") or [])}


def condition_active(spec: dict) -> bool:
    if spec["op"] in NO_VALUE_OPS:
        return True
    if spec["op"] in TEXT_OPS:
        return spec["value"] != ""                 # anche " " è un valore cercabile
    return bool(spec["value"].strip())


def spec_is_active(spec) -> bool:
    spec = normalize_spec(spec)
    return bool(spec["values"]) or condition_active(spec)


def filter_count(spec) -> int:
    """Quanti filtri contiene la specifica di una colonna: ogni valore scelto + l'eventuale condizione."""
    spec = normalize_spec(spec)
    return len(spec["values"]) + (1 if condition_active(spec) else 0)


def describe_condition(op: str, value: str, case: bool = False) -> str:
    label = CONDITION_LABELS.get(op, op).lower()
    if op in NO_VALUE_OPS:
        return label
    shown = value if op in TEXT_OPS else value.strip()
    if shown and not shown.strip():
        shown = f"{len(shown)} spazi" if len(shown) > 1 else "spazio"
    return f"{label} «{shown}»" + (" (maiuscole/minuscole)" if case else "")


def describe_filter(spec) -> str:
    spec = normalize_spec(spec)
    parts = []
    if condition_active(spec):
        parts.append(describe_condition(spec["op"], spec["value"], spec["case"]))
    values = spec["values"]
    if len(values) == 1:
        parts.append(EMPTY_LABEL if values[0] is None else str(values[0]))
    elif values:
        parts.append(f"{len(values)} valori")
    return ", ".join(parts)


def validate_condition(dtype: str | None, op: str, value: str) -> str | None:
    """Messaggio d'errore se la condizione non è applicabile alla colonna, altrimenti None."""
    if op in NO_VALUE_OPS:
        return None
    if (value or "") == "" if op in TEXT_OPS else not (value or "").strip():
        return "Inserisci un valore."
    fam = type_family(dtype)
    if op in ("eq", "ne", "gt", "lt"):
        if fam == "num" and parse_number(value) is None:
            return f"«{value.strip()}» non è un numero: la colonna è numerica."
        if fam in ("date", "timestamp") and parse_date(value) is None:
            return f"«{value.strip()}» non è una data valida (usa aaaa-mm-gg o gg/mm/aaaa)."
    return None


# --------------------------------------------------------------------------- SQL

def condition_sql(ident: str, dtype: str | None, op: str, value, case: bool = False) -> tuple[str, list]:
    """Condizione su una colonna -> (frammento SQL booleano mai NULL, parametri posizionali)."""
    fam = type_family(dtype)
    s = f"CAST({ident} AS VARCHAR)"
    v = "" if value is None else str(value)
    if op == "empty":
        return f"({ident} IS NULL OR trim({s}) = '')", []
    if op == "not_empty":
        return f"({ident} IS NOT NULL AND trim({s}) <> '')", []
    a = s if case else f"lower({s})"
    b = "?" if case else "lower(?)"

    if op in ("contains", "not_contains", "starts", "ends"):
        fn = {"contains": "contains", "not_contains": "contains", "starts": "starts_with", "ends": "ends_with"}[op]
        core = f"coalesce({fn}({a}, {b}), false)"
        return (f"(NOT {core})" if op == "not_contains" else core), [v]

    if op in ("eq", "ne"):
        base, params = _equality(ident, fam, s, a, b, v)
        core = f"coalesce(({base}), false)"
        return (f"(NOT {core})" if op == "ne" else core), params

    if op in ("gt", "lt"):
        cmp = ">" if op == "gt" else "<"
        base, params = _ordering(ident, fam, s, a, b, v, cmp)
        return f"coalesce(({base}), false)", params
    raise ValueError(f"Condizione sconosciuta: {op}")


def _equality(ident, fam, s, a, b, v):
    if fam == "num":
        n = parse_number(v)
        return ("false", []) if n is None else (f"CAST({ident} AS DOUBLE) = ?", [n])
    if fam in ("date", "timestamp"):
        parsed = parse_date(v)
        if parsed is None:
            return "false", []
        day, time = parsed
        if fam == "timestamp" and time:
            return f"CAST({ident} AS TIMESTAMP) = CAST(? AS TIMESTAMP)", [f"{day} {time}"]
        return f"CAST({ident} AS DATE) = CAST(? AS DATE)", [day]
    return f"trim({a}) = trim({b})", [v]


_IT_SQL = "^(\\d{1,2})[/.-](\\d{1,2})[/.-](\\d{4})((?:[ T]\\d{1,2}:\\d{2}(?::\\d{2})?)?)$"
_ISO_SQL = "^\\d{4}-\\d{1,2}-\\d{1,2}(?:[ T]\\d{1,2}:\\d{2}(?::\\d{2}(?:\\.\\d+)?)?)?$"


def text_date_sql(s: str, with_time: bool) -> str:
    """Espressione SQL che legge come data (o data e ora) il testo ``s`` negli stessi formati accettati da ``parse_date``
    (aaaa-mm-gg e gg/mm/aaaa, anche con - o ., giorni e mesi a una cifra, con o senza ora); NULL se non è una data."""
    norm = f"regexp_replace(trim({s}), '{_IT_SQL}', '\\3-\\2-\\1\\4')"
    target = "TIMESTAMP" if with_time else "DATE"
    value = f"replace({norm}, 'T', ' ')" if with_time else norm
    return f"(CASE WHEN regexp_matches({norm}, '{_ISO_SQL}') THEN TRY_CAST({value} AS {target}) END)"


def _ordering(ident, fam, s, a, b, v, cmp):
    if fam == "num":
        n = parse_number(v)
        return ("false", []) if n is None else (f"CAST({ident} AS DOUBLE) {cmp} ?", [n])
    if fam in ("date", "timestamp"):
        parsed = parse_date(v)
        if parsed is None:
            return "false", []
        day, time = parsed
        if fam == "timestamp" and time:
            return f"CAST({ident} AS TIMESTAMP) {cmp} CAST(? AS TIMESTAMP)", [f"{day} {time}"]
        return f"CAST({ident} AS DATE) {cmp} CAST(? AS DATE)", [day]
    # colonna di testo: se il valore è un numero o una data si confronta come tale, altrimenti come testo
    n = parse_number(v)
    if n is not None:
        return f"TRY_CAST(replace(trim({s}), ',', '.') AS DOUBLE) {cmp} ?", [n]
    parsed = parse_date(v)
    if parsed is not None:
        day, time = parsed
        if time:
            return f"{text_date_sql(s, True)} {cmp} CAST(? AS TIMESTAMP)", [f"{day} {time}"]
        return f"{text_date_sql(s, False)} {cmp} CAST(? AS DATE)", [day]
    return f"{a} {cmp} {b}", [v]


def build_conditions(columns: list[str], dtypes: dict[str, str], search: str = "",
                     column_filters: dict | None = None) -> list[tuple[str, list]]:
    """Condizioni separate [(sql, parametri)] di ricerca globale + filtri per colonna (da combinare con AND)."""
    out: list[tuple[str, list]] = []
    if search.strip() and columns:
        clauses = [f"coalesce(contains(lower(CAST({quote_identifier(col)} AS VARCHAR)), lower(?)), false)" for col in columns]
        out.append(("(" + " OR ".join(clauses) + ")", [search.strip()] * len(clauses)))
    for col, raw in (column_filters or {}).items():
        if col not in columns:
            continue
        spec = normalize_spec(raw)
        ident = quote_identifier(col)
        if condition_active(spec):
            value = spec["value"].strip() if spec["op"] in ("eq", "ne", "gt", "lt") else spec["value"]
            out.append(condition_sql(ident, dtypes.get(col), spec["op"], value, spec["case"]))
        values = spec["values"]
        if values:
            non_null = [x for x in values if x is not None]
            parts, params = [], []
            if non_null:
                parts.append(f"CAST({ident} AS VARCHAR) IN ({', '.join('?' * len(non_null))})")
                params.extend(non_null)
            if any(x is None for x in values):
                parts.append(f"({ident} IS NULL OR CAST({ident} AS VARCHAR) = '')")      # NULL e testo vuoto: stesso valore
            out.append(("(" + " OR ".join(parts) + ")", params))
    return out


def build_where(columns: list[str], dtypes: dict[str, str], search: str = "", column_filters: dict | None = None):
    """WHERE per ricerca globale + filtri per colonna (combinati con AND) -> (testo, parametri)."""
    conds = build_conditions(columns, dtypes, search, column_filters)
    if not conds:
        return "", []
    return " WHERE " + " AND ".join(c for c, _ in conds), [x for _, p in conds for x in p]
