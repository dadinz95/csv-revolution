from __future__ import annotations

import atexit
import codecs
import contextlib
import os
import re
import shutil
import tempfile
import threading
from dataclasses import dataclass, field
from pathlib import Path

import duckdb

from csv_reader.core import coltypes
from csv_reader.core import excel as xl
from csv_reader.core.conditions import (  # noqa: F401  (quote_identifier è riesportato: lo importano altri moduli)
    build_where, condition_sql, quote_identifier, validate_condition,
)

RID = "__csv_row_idx"   # identità stabile di ogni record: numero d'ordine nel file, a partire da 0


@dataclass
class Scope:
    """Insieme di celle su cui operano «trova» e «sostituisci».

    kind: "all" (intero dataset), "columns" (colonne intere), "rows" (righe intere, tutte le colonne),
    "cells" (celle o intervallo selezionati). ``filtered`` limita all/columns ai record che soddisfano
    ricerca e filtri correnti."""
    kind: str = "all"
    columns: list[str] = field(default_factory=list)
    rids: list[int] | None = None
    cells: dict[str, list[int]] | None = None
    filtered: bool = False
    search: str = ""
    column_filters: dict | None = None


def _re_escape(text: str) -> str:
    """Testo letterale per un'espressione regolare RE2: si protegge solo la punteggiatura ASCII."""
    return re.sub(r"([\x21-\x2f\x3a-\x40\x5b-\x60\x7b-\x7e])", r"\\\1", text)


def xl_family(dtype: str) -> str:
    from csv_reader.core.conditions import type_family
    return type_family(dtype)


# --------------------------------------------------------------------------- opzioni di importazione

#: codifica scelta dall'utente -> (etichetta, nome interno). None = automatica.
ENCODINGS = {
    "utf-8": "UTF-8",
    "utf-8-sig": "UTF-8 con BOM",
    "cp1252": "Windows-1252",
    "latin-1": "ISO-8859-1",
    "utf-16": "UTF-16",
}
DELIMITERS = {";": "punto e virgola ( ; )", ",": "virgola ( , )", "\t": "tabulazione", "|": "pipe ( | )"}


@dataclass
class ImportOptions:
    """Impostazioni di importazione CSV. None = rilevamento automatico."""
    delimiter: str | None = None      # ";" "," "\t" "|" oppure un delimitatore personalizzato (1-4 caratteri)
    encoding: str | None = None       # chiavi di ENCODINGS
    header: bool | None = None        # True/False oppure automatico
    decimal: str | None = None        # "," oppure "."

    def is_auto(self) -> bool:
        return self.delimiter is None and self.encoding is None and self.header is None and self.decimal is None


class ImportProblem(Exception):
    """Errore di importazione con messaggio già comprensibile per l'utente."""

    def __init__(self, message: str, *, fixable: bool = True):
        super().__init__(message)
        self.user_message = message
        self.fixable = fixable   # True se cambiare le impostazioni di importazione può aiutare


def validate_options(options: ImportOptions) -> None:
    if options.delimiter is not None and not (1 <= len(options.delimiter) <= 4):
        raise ImportProblem("Il delimitatore personalizzato deve avere da 1 a 4 caratteri.")
    if options.delimiter is not None and options.delimiter in ('"', "\n", "\r"):
        raise ImportProblem("Il delimitatore non può essere un apice doppio o un a capo.")
    if options.delimiter == "," and options.decimal == ",":
        raise ImportProblem("Configurazione incompatibile: delimitatore e separatore decimale non possono essere entrambi la virgola. "
                            "Scegli come delimitatore il punto e virgola oppure come decimale il punto.")
    if options.delimiter == options.decimal and options.delimiter is not None:
        raise ImportProblem("Configurazione incompatibile: delimitatore e separatore decimale coincidono.")
    if options.encoding is not None and options.encoding not in ENCODINGS:
        raise ImportProblem(f"Codifica non supportata: {options.encoding}")


def _sql_str(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _friendly_duckdb_error(exc: Exception, path: str, options: ImportOptions) -> ImportProblem:
    text = str(exc)
    low = text.lower()
    if isinstance(exc, duckdb.IOException) or "no such file" in low or "cannot open" in low:
        if "permission" in low or "denied" in low or "used by another" in low:
            return ImportProblem("Il file non è leggibile: permessi insufficienti o file in uso da un'altra applicazione.", fixable=False)
        return ImportProblem("Impossibile leggere il file: potrebbe essere stato spostato, rinominato o essere in uso.", fixable=False)
    if "invalid unicode" in low or "byte sequence mismatch" in low or "utf-8" in low and "invalid" in low:
        return ImportProblem("La codifica scelta non corrisponde al contenuto del file (caratteri non validi). "
                             "Prova «Windows-1252» o «ISO-8859-1» se il file arriva da Excel o da un gestionale italiano.")
    if "error when sniffing" in low or "sniff" in low:
        return ImportProblem("Non riesco a riconoscere automaticamente il formato del file. "
                             "Imposta manualmente delimitatore, codifica e intestazione.")
    if "conversion error" in low or "could not convert" in low:
        hint = " Se i numeri usano la virgola decimale, scegli «Virgola» come separatore decimale." if options.decimal != "," else ""
        return ImportProblem("Un valore non è compatibile con il tipo rilevato per la sua colonna." + hint)
    m = re.search(r"Line:?\s*(\d+)", text)
    if "expected number of columns" in low or "csv error on line" in low or "columns" in low and m:
        where = f" alla riga {m.group(1)}" if m else ""
        return ImportProblem(f"Il numero di colonne non è coerente{where}: probabilmente il delimitatore è sbagliato "
                             "oppure il file contiene virgolette non chiuse. Prova a scegliere il delimitatore a mano.")
    first = text.splitlines()[0] if text else type(exc).__name__
    return ImportProblem(f"Impossibile importare il file: {first}")


def _same_path(a: str, b: str) -> bool:
    if os.path.normcase(os.path.abspath(a)) == os.path.normcase(os.path.abspath(b)):
        return True
    try:
        return os.path.exists(a) and os.path.exists(b) and os.path.samefile(a, b)
    except OSError:
        return False


_TRAILING_SEMICOLON = re.compile(r";(?:\s|--[^\n]*)*$")


def export_query_to_csv(con, sql: str, destination: str, blocked=()) -> str:
    """Scrive il risultato di una query in un CSV in modo sicuro: mai su un file aperto nel programma (si leggerebbe
    mentre lo si tronca) e con scrittura atomica (il file precedente resta intatto finché il nuovo non è completo).
    La query va su righe proprie, così un commento finale (``-- …``) non ingloba la parentesi di chiusura."""
    dest = os.path.abspath(str(Path(destination).expanduser()))
    folder = os.path.dirname(dest) or "."
    if not os.path.isdir(folder):
        raise ImportProblem("La cartella di destinazione non esiste.", fixable=False)
    if os.path.isdir(dest):
        raise ImportProblem("La destinazione è una cartella: indica il nome di un file.", fixable=False)
    for other in blocked:
        if other and _same_path(dest, other):
            raise ImportProblem(f"«{os.path.basename(dest)}» è un file aperto nel programma: scegli un altro nome per il risultato, "
                                "altrimenti il file originale verrebbe sovrascritto.", fixable=False)
    if os.path.exists(dest) and not os.access(dest, os.W_OK):
        raise ImportProblem("Non hai i permessi per scrivere in questo file.", fixable=False)
    if not os.access(folder, os.W_OK):
        raise ImportProblem("Non hai i permessi per scrivere in questa cartella.", fixable=False)
    query = _TRAILING_SEMICOLON.sub("", sql.strip()).rstrip()
    fd, tmp = tempfile.mkstemp(prefix=".csvreader_", suffix=".csv.tmp", dir=folder)
    os.close(fd)
    try:
        cur = con.cursor()
        cur.execute(f"COPY (\n{query}\n) TO {_sql_str(tmp)} (HEADER, DELIMITER ',')")
        CsvEngine._publish(tmp, dest)
    except BaseException:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise
    return dest


def _write_problem(exc: Exception) -> ImportProblem:
    """Errore di scrittura -> messaggio comprensibile."""
    import errno
    text = str(exc).lower()
    code = getattr(exc, "errno", None)
    if code == errno.ENOSPC or "no space" in text or "disk full" in text or "not enough space" in text:
        return ImportProblem("Spazio su disco insufficiente per completare il salvataggio. Libera spazio o scegli un'altra unità: "
                             "le modifiche restano nel programma.", fixable=False)
    if code in (errno.EACCES, errno.EPERM) or "permission" in text or "denied" in text:
        return ImportProblem("Permessi insufficienti per scrivere in questa posizione. Scegli un'altra cartella: "
                             "le modifiche restano nel programma.", fixable=False)
    if "used by another" in text or code == getattr(errno, "EBUSY", -1):
        return ImportProblem("Il file è in uso da un'altra applicazione. Chiudilo e riprova.", fixable=False)
    return ImportProblem(f"Scrittura non riuscita: {str(exc).splitlines()[0] if str(exc) else type(exc).__name__}. "
                         "Le modifiche restano nel programma.", fixable=False)


def _transcode(src: str, dst: str, encoding: str, chunk: int = 8 << 20) -> None:
    """Converte in UTF-8 a blocchi: non carica mai l'intero file in memoria."""
    decoder = codecs.getincrementaldecoder(encoding)(errors="replace")
    with open(src, "rb") as fin, open(dst, "w", encoding="utf-8", newline="") as fout:
        while True:
            block = fin.read(chunk)
            if not block:
                break
            fout.write(decoder.decode(block))
        fout.write(decoder.decode(b"", final=True))


def detect_encoding(path: str, chunk: int = 8 << 20) -> tuple[str, str | None]:
    """Rileva la codifica leggendo il file a blocchi. Restituisce (chiave ENCODINGS o nome codec, nota)."""
    with open(path, "rb") as fh:
        head = fh.read(4096)
    if head.startswith(b"\xef\xbb\xbf"):
        return "utf-8-sig", None
    if head.startswith((b"\xff\xfe", b"\xfe\xff")):
        return "utf-16", None
    if head and head.count(b"\x00") > len(head) * 0.2:
        even = head[0::2].count(b"\x00")
        odd = head[1::2].count(b"\x00")
        return ("utf-16-be" if even > odd else "utf-16-le"), "UTF-16 senza BOM"
    decoder = codecs.getincrementaldecoder("utf-8")()
    try:
        with open(path, "rb") as fh:
            while True:
                block = fh.read(chunk)
                if not block:
                    break
                if block.isascii() and not decoder.getstate()[0]:
                    continue                        # solo ASCII (controllo in C): valido e senza creare la stringa
                decoder.decode(block)
            decoder.decode(b"", final=True)
        return "utf-8", None
    except UnicodeDecodeError:
        return "cp1252", "Il file non è UTF-8 valido: assunta la codifica Windows-1252."


_DECIMAL_COMMA = re.compile(r"^[+-]?\d+,\d+$")


class CsvEngine:
    """Motore DuckDB locale per un singolo file (CSV o foglio Excel). Espone la vista ``csv_data``."""

    def __init__(self) -> None:
        self.con = duckdb.connect(database=":memory:")
        # L'identità dei record prima della prima modifica è row_number() OVER (): vale solo se DuckDB conserva l'ordine
        # di lettura del file anche con più thread. È l'impostazione predefinita; qui è esplicita e verificata dai test.
        self.con.execute("SET preserve_insertion_order = true")
        self.csv_path: str | None = None       # percorso del file originale (mai modificato)
        self.columns: list[str] = []
        self.source_kind = "csv"               # "csv" | "excel"
        self.view_expr: str | None = None      # espressione read_csv(...) usata dalla vista
        self.options = ImportOptions()
        self.info: dict = {}                   # valori rilevati e avvisi dell'ultima importazione
        self.excel_meta: xl.ExcelMeta | None = None
        self.dtypes: dict[str, str] = {}       # tipo DuckDB di ogni colonna
        # Dataset di lavoro: finché non si modifica nulla le query leggono direttamente il file (vista csv_data).
        # Alla prima modifica i dati vengono copiati in una tabella su DISCO (wk.work, file temporaneo): da quel
        # momento csv_data è una vista su quella tabella, quindi filtri, ordinamento, ricerca e SQL vedono le modifiche.
        self.mutated = False
        self.version = 0                       # +1 a ogni modifica
        self.saved_version = 0
        self.change_count = 0                  # modifiche dall'ultimo salvataggio
        self.structure_changed = False         # colonne/righe aggiunte, eliminate o rinominate
        self._export_version = -1
        self._export_path: str | None = None
        self._candidate_dtypes: dict[str, str] = {}
        # cronologia di «Annulla»: voci LIFO con i soli dati modificati (delta) in tabelle su disco (wk.undo_N)
        self._undo: list[dict] = []
        self._state_counter = 0                # ogni stato dei dati ha un identificativo mai riutilizzato
        self._undo_seq = 0
        self._backups: list[dict] | None = None
        self._in_tx = False                    # un blocco atomico è in corso (vedi _atomic)
        self._export_lock = threading.Lock()   # l'esportazione per le query tra file può partire da un altro thread
        self._tmpdir: str | None = None
        self._temp_files: list[str] = []
        atexit.register(self._cleanup_all)

    # ---- file temporanei
    def _new_temp(self, suffix: str) -> str:
        if self._tmpdir is None:
            self._tmpdir = tempfile.mkdtemp(prefix="csvreader_")
        fd, path = tempfile.mkstemp(suffix=suffix, dir=self._tmpdir)
        os.close(fd)
        return path

    def _drop_temps(self, keep: str | None = None) -> None:
        for p in list(self._temp_files):
            if p != keep:
                try:
                    os.remove(p)
                except OSError:
                    pass
                self._temp_files.remove(p)

    def _cleanup_all(self) -> None:
        if self._tmpdir and os.path.isdir(self._tmpdir):
            shutil.rmtree(self._tmpdir, ignore_errors=True)

    # ---- apertura CSV
    def open_csv(self, path: str, options: ImportOptions | None = None) -> tuple[list[str], int]:
        """Apre (o riapre) un CSV con le opzioni date. Se fallisce, il dataset precedente resta intatto."""
        options = options or ImportOptions()
        validate_options(options)
        path = str(Path(path).expanduser().resolve())
        if not os.path.isfile(path):
            raise ImportProblem("File non trovato.", fixable=False)
        if not os.access(path, os.R_OK):
            raise ImportProblem("Il file non è leggibile: permessi insufficienti.", fixable=False)
        if os.path.getsize(path) == 0:
            raise ImportProblem("Il file è vuoto.", fixable=False)

        new_temp: str | None = None
        try:
            notes: list[str] = []
            # 1) codifica
            enc_key = options.encoding
            enc_detected = None
            if enc_key is None:
                enc_detected, note = detect_encoding(path)
                enc_key = enc_detected
                if note:
                    notes.append(note)
            read_path, duck_enc = path, None
            if enc_key in ("utf-8", "utf-8-sig"):
                pass
            elif enc_key == "latin-1":
                duck_enc = "latin-1"
            elif enc_key in ("cp1252", "utf-16", "utf-16-le", "utf-16-be"):
                # DuckDB non legge queste codifiche in modo affidabile: conversione a blocchi in un file temporaneo UTF-8.
                new_temp = self._new_temp(".csv")
                _transcode(path, new_temp, enc_key)
                read_path = new_temp
            else:
                raise ImportProblem(f"Codifica non supportata: {enc_key}")

            # 2) dialetto (delimitatore, virgolette, intestazione) - rilevato o imposto
            # all_varchar: nessuna conversione automatica; i tipi si assegnano solo su richiesta (convert_column)
            params: dict = {"sample_size": -1, "ignore_errors": False, "all_varchar": True}
            if duck_enc:
                params["encoding"] = duck_enc
            sniff_params = {k: v for k, v in params.items() if k == "encoding"}
            if options.delimiter is not None:
                sniff_params["delim"] = options.delimiter
            if options.header is not None:
                sniff_params["header"] = options.header
            detected = self._sniff(read_path, sniff_params)
            delimiter = options.delimiter if options.delimiter is not None else detected["delimiter"]
            header = options.header if options.header is not None else detected["header"]
            params.update({"delim": delimiter, "header": header, "auto_detect": True})
            if detected["quote"] is not None:
                params["quote"] = detected["quote"]
            if detected["escape"] is not None:
                params["escape"] = detected["escape"]
            if detected["skip_rows"]:
                params["skip"] = detected["skip_rows"]
                notes.append(f"Ignorate {detected['skip_rows']} righe iniziali (il rilevamento le considera estranee ai dati): verifica delimitatore e intestazione.")
            decimal = options.decimal or "."

            # 3) vista candidata
            expr = self._read_expr(read_path, params)
            columns = self._create_candidate(expr)

            count = int(self.con.execute("SELECT COUNT(*) FROM csv_data_candidate").fetchone()[0])
        except ImportProblem:
            self._abort_candidate(new_temp)
            raise
        except Exception as exc:
            self._abort_candidate(new_temp)
            raise _friendly_duckdb_error(exc, path, options) from exc

        # 5) avvisi di affidabilità
        warnings = list(notes)
        if len(columns) == 1 and options.delimiter is None:
            with open(read_path, "rb") as fh:
                first_line = fh.readline(65536)
            if any(ch in first_line for ch in (b";", b",", b"\t", b"|")):
                warnings.append("È stata rilevata una sola colonna: il delimitatore potrebbe non essere stato riconosciuto.")
        if not header and options.header is None:
            warnings.append("La prima riga non sembra un'intestazione: le colonne sono state chiamate column0, column1…")

        self._commit_candidate(new_temp)
        self.source_kind = "csv"
        self.csv_path = path
        self.columns = columns
        self.view_expr = expr
        self.options = options
        self.excel_meta = None
        self.info = {
            "delimiter": delimiter, "encoding": enc_key, "encoding_auto": options.encoding is None,
            "header": bool(header), "decimal": decimal, "warnings": warnings, "sheet": None,
            "dateformat": detected.get("dateformat"), "timestampformat": detected.get("timestampformat"),
        }
        self.dtypes = dict(self._candidate_dtypes)       # stessi tipi della vista appena rinominata: nessuna nuova lettura
        return self.columns, count

    # ---- apertura Excel
    def open_excel(self, path: str, sheet: str, header: bool = True) -> tuple[list[str], int]:
        path = str(Path(path).expanduser().resolve())
        if not os.path.isfile(path):
            raise ImportProblem("File non trovato.", fixable=False)
        new_temp = self._new_temp(".csv")
        try:
            meta = xl.import_sheet(path, sheet, new_temp, header=header)
            cols = ", ".join(f"{_sql_str(c)}: {_sql_str(t)}" for c, t in meta.types.items())
            expr = (f"read_csv({_sql_str(new_temp)}, header=false, auto_detect=false, delim=',', quote='\"', "
                    f"escape='\"', null_padding=true, parallel=false, ignore_errors=false, columns={{{cols}}})")
            columns = self._create_candidate(expr)
            count = int(self.con.execute("SELECT COUNT(*) FROM csv_data_candidate").fetchone()[0])
        except xl.ExcelProblem as exc:
            self._abort_candidate(new_temp)
            raise ImportProblem(str(exc), fixable=False) from exc
        except ImportProblem:
            self._abort_candidate(new_temp)
            raise
        except Exception as exc:
            self._abort_candidate(new_temp)
            raise ImportProblem(f"Impossibile importare il foglio: {str(exc).splitlines()[0]}", fixable=False) from exc
        self._commit_candidate(new_temp)
        self.source_kind = "excel"
        self.csv_path = path
        self.columns = columns
        self.view_expr = expr
        self.options = ImportOptions(header=header)
        self.excel_meta = meta
        self.info = {"delimiter": None, "encoding": None, "encoding_auto": False, "header": header,
                     "decimal": None, "warnings": list(meta.warnings), "sheet": sheet}
        self._refresh_dtypes()
        return self.columns, count

    # ---- supporto all'apertura
    def _sniff(self, path: str, params: dict) -> dict:
        args = ", ".join(f"{k}={_sql_str(v) if isinstance(v, str) else str(v).lower() if isinstance(v, bool) else v}"
                         for k, v in params.items())
        sql = f"SELECT Delimiter, Quote, Escape, SkipRows, HasHeader, DateFormat, TimestampFormat FROM sniff_csv({_sql_str(path)}{', ' + args if args else ''})"
        row = self.con.execute(sql).fetchone()
        none = lambda v: None if v in (None, "(empty)") else v  # noqa: E731
        return {"delimiter": row[0], "quote": none(row[1]) if row[1] != "(empty)" else "", "escape": none(row[2]) if row[2] != "(empty)" else "",
                "skip_rows": int(row[3] or 0), "header": bool(row[4]),
                "dateformat": row[5], "timestampformat": row[6]}

    @staticmethod
    def _read_expr(path: str, params: dict) -> str:
        parts = []
        for key, value in params.items():
            if isinstance(value, bool):
                parts.append(f"{key}={'true' if value else 'false'}")
            elif isinstance(value, int):
                parts.append(f"{key}={value}")
            else:
                parts.append(f"{key}={_sql_str(str(value))}")
        return f"read_csv({_sql_str(path)}, {', '.join(parts)})"

    def _create_candidate(self, expr: str) -> list[str]:
        self.con.execute(f"CREATE OR REPLACE VIEW csv_data_candidate AS SELECT * FROM {expr}")
        # una sola lettura/rilevamento dei tipi: DESCRIBE dà nomi e tipi (prima servivano LIMIT 0 e poi DESCRIBE)
        rows = self.con.execute("DESCRIBE SELECT * FROM csv_data_candidate").fetchall()
        self._candidate_dtypes = {r[0]: r[1] for r in rows}
        return [r[0] for r in rows]

    def _looks_decimal_comma(self, columns: list[str]) -> bool:
        """Colonne di testo i cui valori (primi 500 non nulli) sono tutti numeri con virgola decimale."""
        types = self.con.execute("DESCRIBE SELECT * FROM csv_data_candidate").fetchall()
        for name, dtype in ((t[0], t[1]) for t in types):
            if dtype != "VARCHAR":
                continue
            ident = quote_identifier(name)
            values = [r[0] for r in self.con.execute(
                f"SELECT {ident} FROM csv_data_candidate WHERE {ident} IS NOT NULL LIMIT 500").fetchall()]
            if values and all(_DECIMAL_COMMA.match(v.strip()) for v in values):
                return True
        return False

    def _abort_candidate(self, new_temp: str | None) -> None:
        try:
            self.con.execute("DROP VIEW IF EXISTS csv_data_candidate")
        except Exception:
            pass
        if new_temp:
            try:
                os.remove(new_temp)
            except OSError:
                pass
            if new_temp in self._temp_files:
                self._temp_files.remove(new_temp)

    def _commit_candidate(self, new_temp: str | None) -> None:
        self.con.execute("DROP VIEW IF EXISTS csv_data")
        self.con.execute("DROP VIEW IF EXISTS csv_src")
        self._reset_work()
        self.con.execute("ALTER VIEW csv_data_candidate RENAME TO csv_data")
        if new_temp and new_temp not in self._temp_files:
            self._temp_files.append(new_temp)
        self._drop_temps(keep=new_temp)

    # ------------------------------------------------------------------ letture
    def _filter_sql(self, search: str = "", column_filters: dict | None = None):
        return build_where(self.columns, self.dtypes, search, column_filters)

    def _refresh_dtypes(self) -> None:
        rows = self.con.execute("DESCRIBE SELECT * FROM csv_data").fetchall()
        self.dtypes = {r[0]: r[1] for r in rows}

    def _numbered(self) -> str:
        """Relazione con una colonna __csv_row_idx (identità del record) seguita dalle colonne del dataset."""
        if self.mutated:
            return "wk.work"
        return f'(SELECT row_number() OVER () - 1 AS "{RID}", * FROM csv_data)'

    def _select_list(self) -> str:
        return ", ".join(quote_identifier(c) for c in self.columns)

    def _order_key(self, order) -> str:
        """Chiave di ordinamento: secondo il tipo della colonna (numeri come numeri, date come date); i testi senza
        distinguere maiuscole/minuscole; vuoti (NULL o testo vuoto) in fondo; a parità resta l'ordine del file (pagine stabili)."""
        col = order[0]
        ident = quote_identifier(col)
        if xl_family(self.dtypes.get(col, "VARCHAR")) == "text" and self.dtypes.get(col, "VARCHAR").upper() != "BOOLEAN":
            ident = f"NULLIF({ident}, '') COLLATE NOCASE"         # NULL e testo vuoto sono lo stesso valore: insieme, in fondo
        return f"{ident} {'DESC' if order[1] else 'ASC'} NULLS LAST, \"{RID}\""

    def _order_sql(self, order) -> str:
        if order and order[0] in self.columns:
            return " ORDER BY " + self._order_key(order)
        return f' ORDER BY "{RID}"' if self.mutated else ""

    def preview(self, offset: int = 0, limit: int = 200, search: str = "", column_filters: dict | None = None,
                order: tuple[str, bool] | None = None):
        """Una pagina: colonne, valori e numeri d'ordine (0-based) dei record nel file."""
        columns, rows, row_ids, _ = self.preview_cf(offset, limit, search, column_filters, order, None)
        return columns, rows, row_ids

    def preview_cf(self, offset: int, limit: int, search: str = "", column_filters: dict | None = None,
                   order: tuple[str, bool] | None = None, rules: list[dict] | None = None):
        """Come preview, più i colori di sfondo delle regole di formattazione condizionale.

        Le regole sono valutate in SQL solo sulle righe della pagina. Ritorna (colonne, righe, id_righe,
        colori) con colori[r] = {indice_colonna: "#rrggbb"}. A parità di cella vince la prima regola dell'elenco."""
        if not self.csv_path:
            return self.columns, [], [], []
        where, params = build_where(self.columns, self.dtypes, search, column_filters)
        sel = self._select_list()
        inner = (f'SELECT "{RID}", {sel} FROM {self._numbered()} AS n{where}{self._order_sql(order)} LIMIT ? OFFSET ?')
        active = [r for r in (rules or []) if r.get("enabled", True) and r.get("column") in self.columns]
        n = len(self.columns)
        if active:
            parts, cf_params = [], []
            for k, rule in enumerate(active):
                col = rule["column"]
                sql, p = condition_sql(quote_identifier(col), self.dtypes.get(col), rule["op"], rule.get("value", ""),
                                       bool(rule.get("case")))
                parts.append(f'{sql} AS "__cf{k}"')
                cf_params.extend(p)
            query = f"SELECT *, {', '.join(parts)} FROM ({inner}) AS page"
            args = [*cf_params, *params, int(limit), int(offset)]
        else:
            query, args = inner, [*params, int(limit), int(offset)]
        result = self.con.execute(query, args).fetchall()
        rows = [r[1:1 + n] for r in result]
        row_ids = [int(r[0]) for r in result]
        colors: list[dict] = []
        if active:
            index = {c: i for i, c in enumerate(self.columns)}
            for r in result:
                cell: dict[int, str] = {}
                for k, rule in enumerate(active):
                    ci = index[rule["column"]]
                    if r[1 + n + k] and ci not in cell:
                        cell[ci] = rule["color"]
                colors.append(cell)
        return self.columns, rows, row_ids, colors

    # ---- posizioni nella vista (filtri + ordinamento): servono a incolla e navigazione da tastiera
    def _view_key(self, order) -> str:
        return self._order_key(order) if order and order[0] in self.columns else f'"{RID}"'

    def view_rids(self, offset: int, count: int, search: str = "", column_filters: dict | None = None,
                  order: tuple[str, bool] | None = None) -> list[int]:
        """Identità dei record alle posizioni offset..offset+count-1 della vista (con filtri e ordinamento)."""
        if not self.csv_path or count <= 0:
            return []
        where, params = build_where(self.columns, self.dtypes, search, column_filters)
        rows = self.con.execute(
            f'SELECT "{RID}" FROM {self._numbered()} AS n{where} ORDER BY {self._view_key(order)} LIMIT ? OFFSET ?',
            [*params, int(count), int(offset)]).fetchall()
        return [int(r[0]) for r in rows]

    def nav_vertical(self, column: str, pos: int, direction: int, search: str = "", column_filters: dict | None = None,
                     order: tuple[str, bool] | None = None) -> dict:
        """Salto «Ctrl+Freccia su/giù» come in Excel, calcolato in SQL sulla vista (senza caricare i dati in memoria).

        Dalla cella in ``pos``: se questa e la successiva (nella direzione) sono piene si va all'ultima cella piena
        del blocco; altrimenti alla prima cella piena che segue; se non c'è, all'ultima (o prima) riga.
        Ritorna {"target": posizione 0-based, "total": righe della vista}."""
        self._need_column(column)
        where, params = build_where(self.columns, self.dtypes, search, column_filters)
        ident = quote_identifier(column)
        nb = int(pos) + (1 if direction > 0 else -1)
        row = self.con.execute(
            f"WITH v AS (SELECT row_number() OVER (ORDER BY {self._view_key(order)}) - 1 AS pos, "
            f"({ident} IS NULL OR trim(CAST({ident} AS VARCHAR)) = '') AS e FROM {self._numbered()} AS n{where}) "
            "SELECT count(*), max(CASE WHEN pos = ? THEN CAST(e AS INTEGER) END), max(CASE WHEN pos = ? THEN CAST(e AS INTEGER) END), "
            "min(pos) FILTER (WHERE pos > ? AND e), min(pos) FILTER (WHERE pos > ? AND NOT e), "
            "max(pos) FILTER (WHERE pos < ? AND e), max(pos) FILTER (WHERE pos < ? AND NOT e) FROM v",
            [*params, int(pos), nb, *([int(pos)] * 4)]).fetchone()
        total, e_cur, e_nb, empty_after, filled_after, empty_before, filled_before = row
        total = int(total)
        if total == 0:
            return {"target": 0, "total": 0}
        pos = int(pos)
        if direction > 0:
            if e_cur == 0 and e_nb == 0:                      # blocco di celle piene: fino all'ultima
                target = (empty_after - 1) if empty_after is not None else total - 1
            else:
                target = filled_after if filled_after is not None else total - 1
        else:
            if e_cur == 0 and e_nb == 0:
                target = (empty_before + 1) if empty_before is not None else 0
            else:
                target = filled_before if filled_before is not None else 0
        return {"target": int(max(0, min(total - 1, target))), "total": total}

    def distinct_values(self, column: str, search: str = "", column_filters: dict | None = None, limit: int = 5000):
        """Valori distinti di una colonna (con conteggi), rispettando gli altri filtri attivi."""
        if not self.csv_path or column not in self.columns:
            return [], False
        others = {c: f for c, f in (column_filters or {}).items() if c != column}
        where, params = build_where(self.columns, self.dtypes, search, others)
        ident = quote_identifier(column)
        rows = self.con.execute(
            f"SELECT NULLIF(CAST({ident} AS VARCHAR), '') AS v, COUNT(*) AS n FROM csv_data{where} "
            "GROUP BY 1 ORDER BY 1 NULLS FIRST LIMIT ?",
            [*params, int(limit) + 1],
        ).fetchall()
        return [(r[0], int(r[1])) for r in rows[:limit]], len(rows) > limit

    def count_filtered(self, search: str = "", column_filters: dict | None = None) -> int:
        if not self.csv_path:
            return 0
        where, params = build_where(self.columns, self.dtypes, search, column_filters)
        return int(self.con.execute("SELECT COUNT(*) FROM csv_data" + where, params).fetchone()[0])

    def count_total(self) -> int:
        return int(self.con.execute("SELECT COUNT(*) FROM csv_data").fetchone()[0]) if self.csv_path else 0

    def validate_filter(self, column: str, op: str, value: str) -> str | None:
        return validate_condition(self.dtypes.get(column), op, value)

    # ------------------------------------------------------------------ dataset di lavoro
    def _materialize(self) -> None:
        """Alla prima modifica copia il dataset in una tabella su disco (file temporaneo)."""
        if self.mutated:
            return
        if self._tmpdir is None:
            self._tmpdir = tempfile.mkdtemp(prefix="csvreader_")
        path = os.path.join(self._tmpdir, "work.duckdb")
        for ext in ("", ".wal"):
            if os.path.exists(path + ext):
                os.remove(path + ext)
        self.con.execute(f"ATTACH {_sql_str(path)} AS wk")
        self.con.execute(f'CREATE TABLE wk.work AS SELECT row_number() OVER () - 1 AS "{RID}", {self._select_list()} FROM csv_data')
        self.con.execute("ALTER VIEW csv_data RENAME TO csv_src")   # il file originale resta leggibile (confronti, Excel)
        self.mutated = True
        self._define_view()

    def _define_view(self) -> None:
        self.con.execute(f"CREATE OR REPLACE VIEW csv_data AS SELECT {self._select_list()} FROM wk.work")

    def _reset_work(self) -> None:
        if self.mutated:
            try:
                self.con.execute("DETACH wk")
            except Exception:
                pass
            for ext in ("", ".wal"):
                try:
                    os.remove(os.path.join(self._tmpdir or "", "work.duckdb" + ext))
                except OSError:
                    pass
        self.mutated = False
        self.version = self.saved_version = self.change_count = 0
        self.structure_changed = False
        self._export_version = -1
        self._undo.clear()
        self._backups = None

    def _touch(self, n: int = 1, structural: bool = False, undo: dict | None = None) -> None:
        """Registra una modifica. ``undo`` descrive come annullarla (vedi ``undo()``)."""
        prev = (self.version, self.change_count, self.structure_changed)
        self._state_counter = max(self._state_counter, self.version) + 1
        self.version = self._state_counter
        self.change_count += max(1, n)
        self.structure_changed = self.structure_changed or structural
        if undo is not None:
            undo["prev"], undo["state"] = prev, self.version
            undo["bytes"] = self._entry_bytes(undo)
            self._undo.append(undo)
            # si scartano le operazioni più vecchie se sono troppe o se i loro backup occupano troppo disco
            # (si conserva sempre almeno l'ultima operazione)
            while len(self._undo) > self.UNDO_LIMIT or (
                    len(self._undo) > 1 and sum(e.get("bytes", 0) for e in self._undo) > self.UNDO_BYTES_LIMIT):
                self._drop_entry(self._undo.pop(0))

    # ------------------------------------------------------------------ Annulla
    UNDO_LIMIT = 50
    UNDO_BYTES_LIMIT = 2 * 1024 ** 3        # stima dello spazio su disco dei backup di «Annulla»

    def _entry_bytes(self, entry: dict) -> int:
        """Stima (righe × colonne × 16 byte) dello spazio occupato dai backup di un'operazione."""
        total = 0
        for name in entry.get("tables", []):
            try:
                row = self.con.execute(
                    "SELECT estimated_size, column_count FROM duckdb_tables() WHERE database_name = 'wk' AND table_name = ?",
                    [name.split(".")[-1]]).fetchone()
            except Exception:
                row = None
            if row:
                total += int(row[0] or 0) * int(row[1] or 1) * 16
        return total

    @contextlib.contextmanager
    def _atomic(self, view: bool = False):
        """Esegue un blocco come un'unica transazione: se qualcosa fallisce non resta nulla a metà (dati, colonne e tipi
        tornano come prima). I blocchi annidati fanno parte della transazione esterna.
        Una transazione DuckDB può scrivere in un solo database: la vista ``csv_data`` (in «memory») non può essere
        ridefinita dentro il blocco; con ``view=True`` viene ridefinita subito dopo il COMMIT."""
        if self._in_tx:
            yield
            return
        columns, dtypes = list(self.columns), dict(self.dtypes)
        committed = False
        self.con.execute("BEGIN TRANSACTION")
        self._in_tx = True
        try:
            yield
            self.con.execute("COMMIT")
            committed = True
        except BaseException:
            try:
                self.con.execute("ROLLBACK")
            except Exception:
                pass
            self.columns[:] = columns
            self.dtypes = dtypes
            raise
        finally:
            self._in_tx = False
        if view and committed and self.mutated:
            self._define_view()              # la vista csv_data (database «memory») segue le colonne appena confermate

    def _undo_table(self) -> str:
        self._undo_seq += 1
        return f"wk.undo_{self._undo_seq}"

    def _drop_entry(self, entry: dict) -> None:
        for name in entry.get("tables", []):
            try:
                self.con.execute(f"DROP TABLE IF EXISTS {name}")
            except Exception:
                pass

    def _begin_backups(self) -> None:
        self._backups = []

    def _end_backups(self, ok: bool) -> list[dict]:
        backups, self._backups = self._backups or [], None
        if not ok:
            for b in backups:
                self._drop_entry({"tables": [b["table"]]})
            return []
        return backups

    def can_undo(self) -> bool:
        return bool(self._undo)

    def undo_label(self) -> str:
        return self._undo[-1]["label"] if self._undo else ""

    def undo_state(self) -> int | None:
        """Identificativo dello stato prodotto dall'ultima operazione annullabile."""
        return self._undo[-1]["state"] if self._undo else None

    def undo(self) -> dict | None:
        """Annulla l'ultima operazione e ripristina contatori e stato «non salvato».
        Ritorna {"kind", "label", "state", "columns", "structural", "renamed", "restored_column", "removed_column"}
        oppure None se non c'è nulla da annullare."""
        if not self._undo:
            return None
        entry = self._undo[-1]
        kind = entry["kind"]
        result = {"kind": kind, "label": entry["label"], "state": entry["state"], "columns": [], "structural": False,
                  "renamed": None, "restored_column": None, "removed_column": None}
        with self._atomic(view=True):
            if kind == "cells":
                for b in reversed(entry["backups"]):
                    ident = quote_identifier(b["column"])
                    if b["converted"]:
                        self.con.execute(f'UPDATE wk.work SET {ident} = CAST(u.v AS VARCHAR) FROM {b["table"]} u '
                                         f'WHERE work."{RID}" = u.rid')
                        self.con.execute(f"ALTER TABLE wk.work ALTER COLUMN {ident} SET DATA TYPE {b['dtype']}")
                        self.dtypes[b["column"]] = b["dtype"]
                    else:
                        self.con.execute(f'UPDATE wk.work SET {ident} = u.v FROM {b["table"]} u WHERE work."{RID}" = u.rid')
                    result["columns"].append(b["column"])
            elif kind == "convert_column":
                name, ident = entry["name"], quote_identifier(entry["name"])
                # il tipo di partenza si ripristina e i valori ORIGINALI (testo compreso) tornano dalla copia di sicurezza
                self.con.execute(f"ALTER TABLE wk.work ALTER COLUMN {ident} SET DATA TYPE {entry['dtype']} USING NULL")
                self.con.execute(f'UPDATE wk.work SET {ident} = u.v FROM {entry["tables"][0]} u WHERE work."{RID}" = u.rid')
                self.dtypes[name] = entry["dtype"]
                result["columns"].append(name)
            elif kind == "add_column":
                name = entry["name"]
                self.con.execute(f"ALTER TABLE wk.work DROP COLUMN {quote_identifier(name)}")
                self.columns[:] = entry["columns_before"]
                self.dtypes.pop(name, None)
                result.update(structural=True, removed_column=name)
            elif kind == "delete_column":
                name, ident = entry["name"], quote_identifier(entry["name"])
                self.con.execute(f"ALTER TABLE wk.work ADD COLUMN {ident} {entry['dtype']}")
                self.con.execute(f'UPDATE wk.work SET {ident} = u.v FROM {entry["tables"][0]} u WHERE work."{RID}" = u.rid')
                self.columns.insert(entry["index"], name)
                self.dtypes[name] = entry["dtype"]
                result.update(structural=True, restored_column=name)
            elif kind == "rename_column":
                old, new = entry["old"], entry["new"]
                self.con.execute(f"ALTER TABLE wk.work RENAME COLUMN {quote_identifier(new)} TO {quote_identifier(old)}")
                self.columns[self.columns.index(new)] = old
                self.dtypes = {(old if k == new else k): v for k, v in self.dtypes.items()}
                result.update(structural=True, renamed=(new, old))
            elif kind == "delete_rows":
                cols = self._select_list()
                self.con.execute(
                    f'CREATE OR REPLACE TABLE wk.work AS SELECT row_number() OVER (ORDER BY k, t) - 1 AS "{RID}", {cols} FROM ('
                    f'SELECT {cols}, CAST("{RID}" AS DOUBLE) AS k, 0 AS t FROM wk.work UNION ALL '
                    f'SELECT {cols}, CAST(__old - __j AS DOUBLE) - 0.5 AS k, __old AS t FROM {entry["tables"][0]})')
                result.update(structural=True)
            elif kind == "add_row":
                rid = int(entry["rid"])
                self.con.execute(f'DELETE FROM wk.work WHERE "{RID}" = ?', [rid])
                self.con.execute(f'CREATE OR REPLACE TABLE wk.work AS SELECT row_number() OVER (ORDER BY "{RID}") - 1 AS "{RID}", '
                                 f"{self._select_list()} FROM wk.work")
                result.update(structural=True)
            else:                                                   # pragma: no cover
                raise RuntimeError(f"Operazione non annullabile: {kind}")
        self._undo.pop()
        self._drop_entry(entry)
        self.version, self.change_count, self.structure_changed = entry["prev"]
        return result

    def has_changes(self) -> bool:
        return self.version != self.saved_version

    def mark_saved(self, version: int | None = None) -> None:
        """Registra il salvataggio dello stato «version» (di default l'attuale)."""
        self.saved_version = self.version if version is None else version
        if self.saved_version == self.version:
            self.change_count = 0

    # ------------------------------------------------------------------ modifica dei valori
    @staticmethod
    def _typed(expr: str, dtype: str) -> str:
        """Espressione VARCHAR -> valore del tipo della colonna (NULL se non convertibile).

        Un numero con decimali in una colonna intera (o con più decimali di quanti ne ammette un DECIMAL) non viene
        arrotondato: DuckDB lo farebbe in silenzio, quindi lo si dichiara non convertibile e la colonna diventa testo."""
        fam = xl_family(dtype)
        base = f"NULLIF(trim({expr}), '')"
        if fam == "num":
            number = f"replace({base}, ',', '.')"
            cast = f"TRY_CAST({number} AS {dtype})"
            if dtype.upper().startswith(("FLOAT", "DOUBLE", "REAL")):
                return cast
            return f"(CASE WHEN CAST({cast} AS DOUBLE) = TRY_CAST({number} AS DOUBLE) THEN {cast} END)"
        if fam in ("date", "timestamp"):
            return f"coalesce(TRY_CAST({base} AS {dtype}), CAST(TRY_STRPTIME({base}, '%d/%m/%Y') AS {dtype}))"
        return f"TRY_CAST({base} AS {dtype})"

    def _assign(self, column: str, expr: str, expr_params: list, where: str, where_params: list) -> bool:
        """UPDATE della colonna con un'espressione VARCHAR. Se il testo non è compatibile col tipo della colonna,
        la colonna diventa testo (nessun valore viene perso). Ritorna True se la colonna è stata convertita."""
        ident = quote_identifier(column)
        dtype = self.dtypes.get(column, "VARCHAR")
        where = where or "true"
        converted = False
        backup = None
        if self._backups is not None:
            tbl = self._undo_table()
            self.con.execute(f'CREATE TABLE {tbl} AS SELECT "{RID}" AS rid, {ident} AS v FROM wk.work WHERE {where}', where_params)
            backup = {"column": column, "table": tbl, "dtype": dtype, "converted": False}
            self._backups.append(backup)

        def times(sql: str) -> int:   # quante volte l'espressione compare in sql (e quindi i suoi parametri)
            return sql.count("?") // len(expr_params) if expr_params else 0

        if dtype != "VARCHAR":
            typed = self._typed(expr, dtype)
            bad = self.con.execute(
                f"SELECT count(*) FROM wk.work WHERE ({where}) AND NULLIF(trim({expr}), '') IS NOT NULL AND ({typed}) IS NULL",
                [*where_params, *expr_params, *(expr_params * times(typed))]).fetchone()[0]
            if bad:
                self.con.execute(f"ALTER TABLE wk.work ALTER COLUMN {ident} SET DATA TYPE VARCHAR")
                self.dtypes[column] = dtype = "VARCHAR"
                converted = True
                if backup:
                    backup["converted"] = True
        if dtype == "VARCHAR":
            self.con.execute(f"UPDATE wk.work SET {ident} = {expr} WHERE {where}", [*expr_params, *where_params])
        else:
            typed = self._typed(expr, dtype)
            self.con.execute(f"UPDATE wk.work SET {ident} = {typed} WHERE {where}",
                             [*(expr_params * times(typed)), *where_params])
        return converted

    # ------------------------------------------------------------------ tipi di dato (solo su richiesta)
    def column_kind(self, column: str) -> str:
        """Tipo attuale della colonna: text/int/decimal/date/datetime/bool."""
        self._need_column(column)
        return coltypes.kind_of(self.dtypes.get(column))

    def analyze_column(self, column: str) -> dict:
        """Riconosce il tipo dei valori di una colonna SENZA modificarla.

        Ritorna {"column", "current", "kind" (proposto), "valid", "invalid", "partial", "reason", "nonempty",
        "counts", "min_decimals", "int_digits", "examples" (non convertibili nel tipo proposto)}."""
        self._need_column(column)
        ident = quote_identifier(column)
        cur = self.con.cursor()
        row = cur.execute(coltypes.analysis_sql(ident, "csv_data")).fetchone()
        names = [d[0] for d in cur.description]
        stats = dict(zip(names, row))
        prop = coltypes.suggest(stats)
        min_dec, digits = coltypes.decimals_for(stats)
        out = {"column": column, "current": self.column_kind(column), "nonempty": int(stats["nonempty"]),
               "counts": {k: int(stats[f"n_{k}"]) for k in ("int", "decimal", "date", "datetime", "bool")},
               "min_decimals": min_dec, "int_digits": digits, **prop, "examples": []}
        if prop["invalid"]:
            expr = self._kind_expr(prop["kind"], ident, min_dec, digits)[0]
            out["examples"] = [r[0] for r in cur.execute(coltypes.examples_sql(ident, expr, "csv_data")).fetchall()]
        return out

    @staticmethod
    def _kind_expr(kind: str, ident: str, scale: int, digits: int) -> tuple[str, str]:
        dtype = coltypes.target_dtype(kind, scale, digits)
        return coltypes.convert_sql(kind, ident, dtype), dtype

    def preview_conversion(self, column: str, kind: str, scale: int | None = None) -> dict:
        """Quanti valori si convertono, quanti restano vuoti e alcuni esempi non convertibili (nessuna modifica)."""
        self._need_column(column)
        ident = quote_identifier(column)
        stats = self.analyze_column(column)
        scale = max(stats["min_decimals"], 2 if scale is None else scale) if kind == "decimal" else 0
        expr, dtype = self._kind_expr(kind, ident, scale, stats["int_digits"])
        cur = self.con.cursor()
        invalid = int(cur.execute(coltypes.count_invalid_sql(ident, expr, "csv_data")).fetchone()[0]) if kind != "text" else 0
        examples = [r[0] for r in cur.execute(coltypes.examples_sql(ident, expr, "csv_data")).fetchall()] if invalid else []
        lead = int(cur.execute(f"SELECT count(*) FROM csv_data WHERE regexp_full_match({coltypes.text_of(ident)}, '[+-]?0\\d+([.,]\\d+)?')").fetchone()[0]) \
            if kind in ("int", "decimal") else 0
        return {"dtype": dtype, "nonempty": stats["nonempty"], "invalid": invalid, "examples": examples,
                "leading_zeros": lead, "scale": scale}

    def convert_column(self, column: str, kind: str, scale: int | None = None, plan: dict | None = None) -> dict:
        """Assegna un tipo alla colonna (operazione esplicita e annullabile).

        I valori non convertibili diventano vuoti (se ne dà il conteggio e alcuni esempi); la copia di sicurezza dei
        valori originali è nella cronologia di «Annulla», che li ripristina identici. L'operazione è atomica.
        Ritorna {"changed", "dtype", "converted", "invalid", "examples", "scale"}."""
        self._need_column(column)
        if kind not in coltypes.LABELS:
            raise ImportProblem(f"Tipo di dato sconosciuto: {kind}", fixable=False)
        ident = quote_identifier(column)
        old = self.dtypes.get(column, "VARCHAR")
        plan = plan or self.preview_conversion(column, kind, scale)
        dtype = plan["dtype"]
        if old == dtype:
            return {"changed": False, "dtype": dtype, "converted": 0, "invalid": 0, "examples": [], "scale": plan["scale"]}
        expr = coltypes.convert_sql(kind, ident, dtype)
        self._materialize()
        table = self._undo_table()
        ok = False
        try:
            with self._atomic():
                self.con.execute(f'CREATE TABLE {table} AS SELECT "{RID}" AS rid, {ident} AS v FROM wk.work')
                self.con.execute(f"ALTER TABLE wk.work ALTER COLUMN {ident} SET DATA TYPE {dtype} USING {expr}")
                self.dtypes[column] = dtype
            ok = True
        finally:
            if not ok:
                self._drop_entry({"tables": [table]})
        converted = max(0, plan["nonempty"] - plan["invalid"])
        self._touch(max(1, converted), undo={"kind": "convert_column", "label": f"tipo di «{column}»", "name": column,
                                              "dtype": old, "tables": [table]})
        return {"changed": True, "dtype": dtype, "converted": converted, "invalid": plan["invalid"],
                "examples": plan["examples"], "scale": plan["scale"]}

    def _need_column(self, column: str) -> None:
        if column not in self.columns:
            raise ImportProblem(f"La colonna «{column}» non esiste.", fixable=False)

    def cell_text(self, rid: int, column: str) -> str | None:
        row = self.con.execute(
            f'SELECT CAST({quote_identifier(column)} AS VARCHAR) FROM {self._numbered()} AS n WHERE "{RID}" = ?',
            [int(rid)]).fetchone()
        return row[0] if row else None

    def set_cell(self, rid: int, column: str, text: str) -> dict:
        """Imposta il valore di una cella. Ritorna {"changed", "converted"}."""
        self._need_column(column)
        current = self.cell_text(rid, column)
        if (current or "") == (text or ""):
            return {"changed": False, "converted": False}
        self._materialize()
        self._begin_backups()
        ok = False
        try:
            with self._atomic():
                converted = self._assign(column, "?", [text], f'"{RID}" = ?', [int(rid)])
            ok = True
        finally:
            backups = self._end_backups(ok)
        self._touch(1, undo={"kind": "cells", "label": "modifica cella", "backups": backups,
                             "tables": [b["table"] for b in backups]})
        return {"changed": True, "converted": converted}

    def set_cells(self, changes: list[tuple[int, str, str]], label: str = "incolla") -> dict:
        """Imposta molte celle in un'unica operazione annullabile. ``changes``: [(rid, colonna, testo)].
        Ritorna {"changed": celle cambiate, "converted": [colonne diventate testo]}."""
        by_col: dict[str, tuple[list, list]] = {}
        for rid, col, text in changes:
            self._need_column(col)
            r, t = by_col.setdefault(col, ([], []))
            r.append(int(rid))
            t.append(text if text is not None else "")
        if not by_col:
            return {"changed": 0, "converted": []}
        self._materialize()
        self._begin_backups()
        ok = False
        changed, converted = 0, []
        try:
            with self._atomic():
                for col, (rids, texts) in by_col.items():
                    ident = quote_identifier(col)
                    dtype = self.dtypes.get(col, "VARCHAR")
                    self.con.execute("DROP TABLE IF EXISTS temp.paste")
                    self.con.execute(
                        f'CREATE TEMP TABLE paste AS SELECT p.rid AS rid, p.txt AS txt FROM (SELECT unnest(?) AS rid, unnest(?) AS txt) p '
                        f'JOIN wk.work w ON w."{RID}" = p.rid '
                        f"WHERE coalesce(CAST(w.{ident} AS VARCHAR), '') <> coalesce(p.txt, '')", [rids, texts])
                    n = self.con.execute("SELECT count(*) FROM temp.paste").fetchone()[0]
                    if not n:
                        continue
                    tbl = self._undo_table()
                    self.con.execute(f'CREATE TABLE {tbl} AS SELECT w."{RID}" AS rid, w.{ident} AS v FROM wk.work w '
                                     f'JOIN temp.paste p ON p.rid = w."{RID}"')
                    backup = {"column": col, "table": tbl, "dtype": dtype, "converted": False}
                    self._backups.append(backup)
                    if dtype != "VARCHAR":
                        bad = self.con.execute(
                            f"SELECT count(*) FROM temp.paste WHERE NULLIF(trim(txt), '') IS NOT NULL AND ({self._typed('txt', dtype)}) IS NULL"
                        ).fetchone()[0]
                        if bad:
                            self.con.execute(f"ALTER TABLE wk.work ALTER COLUMN {ident} SET DATA TYPE VARCHAR")
                            self.dtypes[col] = dtype = "VARCHAR"
                            backup["converted"] = True
                            converted.append(col)
                    value = "p.txt" if dtype == "VARCHAR" else self._typed("p.txt", dtype)
                    self.con.execute(f'UPDATE wk.work SET {ident} = {value} FROM temp.paste p WHERE work."{RID}" = p.rid')
                    changed += int(n)
            ok = True
        finally:
            try:
                self.con.execute("DROP TABLE IF EXISTS temp.paste")
            except Exception:
                pass
            backups = self._end_backups(ok)
        if not backups:
            return {"changed": 0, "converted": []}
        self._touch(changed, undo={"kind": "cells", "label": label, "backups": backups,
                                   "tables": [b["table"] for b in backups]})
        return {"changed": changed, "converted": converted}

    # ------------------------------------------------------------------ ambito di ricerca/sostituzione
    def _targets(self, scope: "Scope", columns: list[str] | None = None) -> list[tuple[str, str, list]]:
        """[(colonna, condizione WHERE senza la parola WHERE, parametri)] per le celle dell'ambito."""
        cols = [c for c in (columns if columns is not None else scope.columns) if c in self.columns]
        out = []
        if scope.kind in ("all", "columns"):
            where, params = "", []
            if scope.filtered:
                w, params = build_where(self.columns, self.dtypes, scope.search, scope.column_filters)
                where = w[len(" WHERE "):] if w else ""
            out = [(c, where, list(params)) for c in cols]
        elif scope.kind == "rows":
            out = [(c, f'"{RID}" IN (SELECT unnest(?))', [list(scope.rids or [])]) for c in cols]
        elif scope.kind == "cells":
            for c in cols:
                rids = (scope.cells or {}).get(c)
                if rids:
                    out.append((c, f'"{RID}" IN (SELECT unnest(?))', [list(rids)]))
        return out

    @staticmethod
    def _match_sql(ident: str, find: str, case: bool, exact: bool) -> tuple[str, list]:
        s = f"CAST({ident} AS VARCHAR)"
        if exact:
            if find == "":
                return f"({ident} IS NULL OR {s} = '')", []
            return (f"coalesce({s} = ?, false)", [find]) if case else (f"coalesce(lower({s}) = lower(?), false)", [find])
        return (f"coalesce(contains({s}, ?), false)", [find]) if case else (
            f"coalesce(contains(lower({s}), lower(?)), false)", [find])

    @staticmethod
    def _replace_expr(ident: str, find: str, repl: str, case: bool, exact: bool) -> tuple[str, list]:
        s = f"CAST({ident} AS VARCHAR)"
        if exact:
            return "?", [repl]
        if case:
            return f"replace({s}, ?, ?)", [find, repl]
        # senza distinzione maiuscole/minuscole: espressione regolare con il testo cercato «scappato» (nessun
        # carattere speciale ha significato) e il testo sostitutivo letterale (i backslash raddoppiati)
        return f"regexp_replace({s}, ?, ?, 'gi')", [_re_escape(find), repl.replace("\\", "\\\\")]

    def _check_find(self, find: str, exact: bool) -> None:
        if find == "" and not exact:
            raise ImportProblem("Il campo «Trova» è vuoto. Con «Corrispondenza esatta» puoi cercare le celle vuote.",
                                fixable=False)

    def _changing_sql(self, ident: str, find: str, repl: str | None, case: bool, exact: bool) -> tuple[str, list]:
        """Condizione «la cella contiene il testo» e, se è indicato il sostitutivo, «e il risultato è diverso dal valore
        attuale» (una sostituzione che non cambia nulla non conta come modifica; NULL e testo vuoto sono lo stesso valore)."""
        m, mp = self._match_sql(ident, find, case, exact)
        if repl is None:
            return m, mp
        e, ep = self._replace_expr(ident, find, repl, case, exact)
        return (f"({m} AND coalesce(CAST({ident} AS VARCHAR), '') <> coalesce(({e}), ''))", [*mp, *ep])

    def count_matches(self, find: str, case: bool, exact: bool, scope: "Scope", repl: str | None = None) -> dict[str, int]:
        """Quante celle dell'ambito contengono il testo cercato (e, se è indicato ``repl``, cambierebbero davvero),
        per colonna. Non modifica nulla."""
        self._check_find(find, exact)
        counts: dict[str, int] = {}
        for col, where, params in self._targets(scope):
            m, mp = self._changing_sql(quote_identifier(col), find, repl, case, exact)
            n = self.con.execute(
                f"SELECT count(*) FROM {self._numbered()} AS n WHERE ({where or 'true'}) AND {m}", [*params, *mp]).fetchone()[0]
            if n:
                counts[col] = int(n)
        return counts

    def replace_all(self, find: str, repl: str, case: bool, exact: bool, scope: "Scope") -> dict:
        """Sostituisce nell'ambito. Ritorna {"cells", "columns": {col: n}, "converted": [colonne]}.
        Conta e modifica solo le celle il cui valore cambia davvero. L'operazione è atomica."""
        self._check_find(find, exact)
        counts = self.count_matches(find, case, exact, scope, repl)
        report = {"cells": 0, "columns": {}, "converted": []}
        if not counts:
            return report
        self._materialize()
        self._begin_backups()
        ok = False
        try:
            with self._atomic():
                for col, where, params in self._targets(scope, list(counts)):
                    ident = quote_identifier(col)
                    m, mp = self._changing_sql(ident, find, repl, case, exact)
                    e, ep = self._replace_expr(ident, find, repl, case, exact)
                    if self._assign(col, e, ep, f"({where or 'true'}) AND {m}", [*params, *mp]):
                        report["converted"].append(col)
                    report["columns"][col] = counts[col]
                    report["cells"] += counts[col]
            ok = True
        finally:
            backups = self._end_backups(ok)
        self._touch(report["cells"], undo={"kind": "cells", "label": "sostituzione", "backups": backups,
                                           "tables": [b["table"] for b in backups]})
        return report

    def replace_cell(self, rid: int, column: str, find: str, repl: str, case: bool, exact: bool) -> dict:
        """Sostituisce solo nella cella indicata (tutte le occorrenze al suo interno), se il valore cambia."""
        self._check_find(find, exact)
        self._need_column(column)
        ident = quote_identifier(column)
        m, mp = self._changing_sql(ident, find, repl, case, exact)
        hit = self.con.execute(f'SELECT count(*) FROM {self._numbered()} AS n WHERE "{RID}" = ? AND {m}',
                               [int(rid), *mp]).fetchone()[0]
        if not hit:
            return {"cells": 0, "converted": []}
        self._materialize()
        e, ep = self._replace_expr(ident, find, repl, case, exact)
        self._begin_backups()
        ok = False
        try:
            with self._atomic():
                converted = self._assign(column, e, ep, f'"{RID}" = ? AND {m}', [int(rid), *mp])
            ok = True
        finally:
            backups = self._end_backups(ok)
        self._touch(1, undo={"kind": "cells", "label": "sostituzione", "backups": backups,
                             "tables": [b["table"] for b in backups]})
        return {"cells": 1, "converted": [column] if converted else []}

    def find_next(self, find: str, case: bool, exact: bool, scope: "Scope", nav_columns: list[str],
                  search: str = "", column_filters: dict | None = None, order: tuple[str, bool] | None = None,
                  after: tuple[int, int] | None = None) -> dict | None:
        """Prossima cella che corrisponde, nell'ordine in cui il dataset è mostrato (filtri e ordinamento inclusi).

        after = (posizione 1-based della riga, indice della colonna in nav_columns) della cella corrente.
        Ritorna {"pos", "rid", "column", "wrapped"} oppure None. La ricerca ricomincia dall'inizio a fine dataset."""
        self._check_find(find, exact)
        cols = [c for c in nav_columns if c in self.columns]
        if not cols:
            return None
        where, wparams = build_where(self.columns, self.dtypes, search, column_filters)
        if order and order[0] in self.columns:
            win = "ORDER BY " + self._order_key(order)
        else:
            win = f'ORDER BY "{RID}"'
        m_sqls, m_params = [], []
        for c in cols:
            m, mp = self._match_sql(quote_identifier(c), find, case, exact)
            if scope.kind == "rows":
                m, mp = f'({m} AND "{RID}" IN (SELECT unnest(?)))', [*mp, list(scope.rids or [])]
            elif scope.kind == "cells":
                m, mp = f'({m} AND "{RID}" IN (SELECT unnest(?)))', [*mp, list((scope.cells or {}).get(c, []))]
            m_sqls.append(m)
            m_params.append(mp)
        select_m = ", ".join(f"{m} AS m{i}" for i, m in enumerate(m_sqls))
        any_m = " OR ".join(m_sqls)
        flat = [x for mp in m_params for x in mp]

        def run(start_pos: int):
            sql = (f"WITH v AS (SELECT n.*, row_number() OVER ({win}) AS __pos FROM {self._numbered()} AS n{where}) "
                   f'SELECT __pos, "{RID}", {select_m} FROM v WHERE __pos >= ? AND ({any_m}) ORDER BY __pos LIMIT 50')
            return self.con.execute(sql, [*wparams, *flat, int(start_pos), *flat]).fetchall()

        def pick(rows, after_cell):
            for r in rows:
                for i in range(len(cols)):
                    if r[2 + i] and (after_cell is None or (r[0], i) > after_cell):
                        return {"pos": int(r[0]), "rid": int(r[1]), "column": cols[i]}
            return None

        found = pick(run(after[0] if after else 1), after)
        if found:
            found["wrapped"] = False
            return found
        if after is not None:
            found = pick(run(1), None)
            if found:
                found["wrapped"] = True
                return found
        return None

    # ------------------------------------------------------------------ colonne e righe
    def _validate_new_name(self, name: str, ignore: str | None = None) -> str:
        name = (name or "").strip()
        if not name:
            raise ImportProblem("Il nome della colonna non può essere vuoto.", fixable=False)
        if name == RID:
            raise ImportProblem("Questo nome è riservato: scegline un altro.", fixable=False)
        for c in self.columns:
            if c != ignore and c.lower() == name.lower():
                raise ImportProblem(f"Esiste già una colonna chiamata «{c}». Scegli un altro nome.", fixable=False)
        return name

    def add_column(self, name: str, after: str | None = None) -> str:
        """Aggiunge una colonna di testo con tutti i valori vuoti (NULL), dopo la colonna ``after`` (in fondo se None)."""
        name = self._validate_new_name(name)
        if after is not None:
            self._need_column(after)
        self._materialize()
        columns_before = list(self.columns)
        with self._atomic(view=True):
            self.con.execute(f"ALTER TABLE wk.work ADD COLUMN {quote_identifier(name)} VARCHAR")
            if after is not None and after != self.columns[-1]:
                order = list(self.columns)
                order.insert(order.index(after) + 1, name)
                select = ", ".join(quote_identifier(c) for c in order)
                self.con.execute(f'CREATE OR REPLACE TABLE wk.work AS SELECT "{RID}", {select} FROM wk.work ORDER BY "{RID}"')
                self.columns[:] = order
            else:
                self.columns.append(name)
            self.dtypes[name] = "VARCHAR"
        self._touch(1, structural=True, undo={"kind": "add_column", "label": "aggiunta colonna", "name": name,
                                              "columns_before": columns_before})
        return name

    def delete_column(self, name: str) -> None:
        self._need_column(name)
        if len(self.columns) <= 1:
            raise ImportProblem("Non si può eliminare l'unica colonna del dataset.", fixable=False)
        self._materialize()
        with self._atomic(view=True):
            tbl = self._undo_table()
            ident = quote_identifier(name)
            self.con.execute(f'CREATE TABLE {tbl} AS SELECT "{RID}" AS rid, {ident} AS v FROM wk.work')
            index, dtype = self.columns.index(name), self.dtypes.get(name, "VARCHAR")
            self.con.execute(f"ALTER TABLE wk.work DROP COLUMN {ident}")
            self.columns.remove(name)
            self.dtypes.pop(name, None)
        self._touch(1, structural=True, undo={"kind": "delete_column", "label": "eliminazione colonna", "name": name,
                                              "index": index, "dtype": dtype, "tables": [tbl]})

    def rename_column(self, old: str, new: str) -> str:
        self._need_column(old)
        new = self._validate_new_name(new, ignore=old)
        if new == old:
            return new
        self._materialize()
        with self._atomic(view=True):
            self.con.execute(f"ALTER TABLE wk.work RENAME COLUMN {quote_identifier(old)} TO {quote_identifier(new)}")
            self.columns[self.columns.index(old)] = new
            self.dtypes = {(new if k == old else k): v for k, v in self.dtypes.items()}
        self._touch(1, structural=True, undo={"kind": "rename_column", "label": "rinomina colonna", "old": old, "new": new})
        return new

    def delete_rows(self, rids: list[int]) -> int:
        """Elimina i record indicati e rinumera i rimanenti: il numero di riga resta quello del file salvato."""
        rids = sorted({int(r) for r in rids})
        if not rids:
            return 0
        self._materialize()
        n = self.con.execute(f'SELECT count(*) FROM wk.work WHERE "{RID}" IN (SELECT unnest(?))', [rids]).fetchone()[0]
        if not n:
            return 0
        with self._atomic(view=True):
            tbl = self._undo_table()
            self.con.execute(f'CREATE TABLE {tbl} AS SELECT "{RID}" AS __old, row_number() OVER (ORDER BY "{RID}") - 1 AS __j, '
                             f'{self._select_list()} FROM wk.work WHERE "{RID}" IN (SELECT unnest(?))', [rids])
            self.con.execute(f'DELETE FROM wk.work WHERE "{RID}" IN (SELECT unnest(?))', [rids])
            self.con.execute(f'CREATE OR REPLACE TABLE wk.work AS SELECT row_number() OVER (ORDER BY "{RID}") - 1 AS "{RID}", '
                             f"{self._select_list()} FROM wk.work")
        self._touch(int(n), structural=True, undo={"kind": "delete_rows", "label": "eliminazione righe", "tables": [tbl]})
        return int(n)

    def add_row(self, after_rid: int | None = None) -> int:
        """Aggiunge un record con tutti i valori vuoti dopo il record ``after_rid`` (in fondo se None).
        Ritorna il suo numero d'ordine (0-based); i record successivi vengono rinumerati."""
        self._materialize()
        with self._atomic():
            last = int(self.con.execute(f'SELECT coalesce(max("{RID}"), -1) FROM wk.work').fetchone()[0])
            if after_rid is None or int(after_rid) >= last:
                rid = last + 1
                self.con.execute(f'INSERT INTO wk.work ("{RID}") VALUES (?)', [rid])
            else:
                rid = int(after_rid) + 1
                self.con.execute(f'INSERT INTO wk.work ("{RID}") VALUES (?)', [-1])
                self.con.execute(
                    f'CREATE OR REPLACE TABLE wk.work AS SELECT row_number() OVER (ORDER BY (CASE WHEN "{RID}" = -1 THEN {rid - 0.5} '
                    f'ELSE "{RID}" END)) - 1 AS "{RID}", {self._select_list()} FROM wk.work')
        self._touch(1, structural=True, undo={"kind": "add_row", "label": "aggiunta riga", "rid": rid})
        return rid

    # ------------------------------------------------------------------ salvataggio
    # ---- destinazione e scrittura atomica
    def protected_path(self) -> str | None:
        """File che il programma non sovrascrive mai: l'Excel importato."""
        return self.csv_path if self.source_kind == "excel" else None

    def check_destination(self, destination: str) -> str:
        """Controlla la destinazione prima di scrivere. Ritorna il percorso assoluto."""
        dest = os.path.abspath(str(Path(destination).expanduser()))
        folder = os.path.dirname(dest) or "."
        if not os.path.isdir(folder):
            raise ImportProblem("La cartella di destinazione non esiste.", fixable=False)
        if os.path.isdir(dest):
            raise ImportProblem("La destinazione è una cartella: indica il nome di un file.", fixable=False)
        protected = self.protected_path()
        if protected and os.path.exists(dest) and os.path.samefile(dest, protected):
            raise ImportProblem(f"Il file Excel originale «{os.path.basename(protected)}» non viene mai sovrascritto: "
                                "scegli un altro nome per la copia.", fixable=False)
        if os.path.exists(dest) and not os.access(dest, os.W_OK):
            raise ImportProblem("Non hai i permessi per scrivere in questo file.", fixable=False)
        if not os.access(folder, os.W_OK):
            raise ImportProblem("Non hai i permessi per scrivere in questa cartella.", fixable=False)
        return dest

    @staticmethod
    def _temp_beside(dest: str, suffix: str) -> str:
        fd, tmp = tempfile.mkstemp(prefix=".csvreader_", suffix=suffix + ".tmp", dir=os.path.dirname(dest) or ".")
        os.close(fd)
        return tmp

    @staticmethod
    def _publish(tmp: str, dest: str) -> None:
        """Sostituzione atomica: il file precedente resta intatto finché la nuova scrittura non è completa."""
        try:
            os.replace(tmp, dest)
        except PermissionError as exc:
            raise ImportProblem("Impossibile sostituire il file: è aperto in un'altra applicazione o protetto da scrittura. "
                                "Chiudilo e riprova, oppure scegli un altro nome.", fixable=False) from exc
        except OSError as exc:
            raise _write_problem(exc) from exc

    def export_defaults(self) -> dict:
        """Delimitatore, codifica e separatore decimale con cui il file è stato importato."""
        info = self.info or {}
        enc = info.get("encoding") or "utf-8"
        if enc.startswith("utf-16"):
            enc = "utf-16"
        if enc not in ENCODINGS:
            enc = "utf-8"
        delim = info.get("delimiter") if self.source_kind == "csv" else None
        return {"delimiter": delim if delim in DELIMITERS else ",", "encoding": enc, "decimal": info.get("decimal") or "."}

    def _export_select(self, decimal: str) -> str:
        parts = []
        for c in self.columns:
            ident = quote_identifier(c)
            dtype = self.dtypes.get(c, "VARCHAR").upper()
            if decimal == "," and (dtype in ("DOUBLE", "FLOAT", "REAL") or dtype.startswith("DECIMAL")):
                parts.append(f"replace(CAST({ident} AS VARCHAR), '.', ',') AS {ident}")
            else:
                parts.append(ident)
        return ", ".join(parts)

    def save_csv(self, destination: str, delimiter: str | None = None, encoding: str | None = None,
                 decimal: str | None = None) -> dict:
        """Scrive il dataset completo (tutte le righe e colonne, nascoste comprese) in un CSV.

        La scrittura avviene in un file temporaneo nella stessa cartella, poi sostituzione atomica: in caso di errore il
        file di destinazione precedente resta intatto. Ritorna {"version": versione salvata, "rows": n}."""
        if not self.csv_path:
            raise RuntimeError("Apri prima un file.")
        defaults = self.export_defaults()
        delimiter = delimiter or defaults["delimiter"]
        encoding = encoding or defaults["encoding"]
        decimal = decimal or defaults["decimal"]
        if len(delimiter) != 1:
            raise ImportProblem("Il delimitatore deve essere un solo carattere.", fixable=False)
        if encoding not in ENCODINGS:
            raise ImportProblem(f"Codifica non supportata: {encoding}", fixable=False)
        if decimal not in (".", ","):
            raise ImportProblem("Il separatore decimale deve essere «.» o «,».", fixable=False)
        if decimal == delimiter:
            raise ImportProblem("Delimitatore e separatore decimale non possono essere lo stesso carattere.", fixable=False)
        dest = self.check_destination(destination)
        version = self.version
        info = self.info or {}
        extra = ""
        if info.get("dateformat"):
            extra += f", DATEFORMAT {_sql_str(info['dateformat'])}"
        if info.get("timestampformat"):
            extra += f", TIMESTAMPFORMAT {_sql_str(info['timestampformat'])}"
        tmp = self._temp_beside(dest, ".csv")
        final = tmp
        try:
            query = f'SELECT {self._export_select(decimal)} FROM {self._numbered()} AS n ORDER BY "{RID}"'
            try:
                self.con.execute(f"COPY ({query}) TO {_sql_str(tmp)} (HEADER, DELIMITER {_sql_str(delimiter)}{extra})")
            except OSError as exc:
                raise _write_problem(exc) from exc
            except duckdb.IOException as exc:
                raise _write_problem(exc) from exc
            if encoding != "utf-8":
                final = self._temp_beside(dest, ".csv")
                self._recode(tmp, final, encoding)
            self._publish(final, dest)
            final = tmp
        finally:
            for path in {tmp, final}:
                if os.path.exists(path):
                    try:
                        os.remove(path)
                    except OSError:
                        pass
        rows = int(self.con.execute(f"SELECT count(*) FROM {self._numbered()} AS n").fetchone()[0])
        return {"version": version, "rows": rows}

    @staticmethod
    def _recode(src: str, dst: str, encoding: str, chunk: int = 8 << 20) -> None:
        """UTF-8 -> codifica scelta, a blocchi. Un carattere non rappresentabile è un errore (nessuna perdita silenziosa)."""
        codec = {"utf-8-sig": "utf-8-sig", "cp1252": "cp1252", "latin-1": "iso-8859-1", "utf-16": "utf-16"}[encoding]
        encoder = codecs.getincrementalencoder(codec)(errors="strict")
        try:
            with open(src, "r", encoding="utf-8", newline="") as fin, open(dst, "wb") as fout:
                while True:
                    block = fin.read(chunk)
                    if not block:
                        break
                    fout.write(encoder.encode(block))
                fout.write(encoder.encode("", final=True))
        except UnicodeEncodeError as exc:
            bad = exc.object[exc.start:exc.start + 1]
            raise ImportProblem(f"Il carattere «{bad}» non può essere scritto nella codifica {ENCODINGS[encoding]}. "
                                "Scegli UTF-8 oppure correggi il valore.", fixable=False) from exc
        except OSError as exc:
            raise _write_problem(exc) from exc

    def save_xlsx(self, destination: str, sheet_name: str = "Dati") -> dict:
        """Scrive il dataset in un file .xlsx.

        Origine Excel: nuova copia dell'originale con le modifiche (fogli e formule non toccate restano).
        Origine CSV: nuova cartella di lavoro con un solo foglio, scritta in streaming."""
        if not self.csv_path:
            raise RuntimeError("Apri prima un file.")
        dest = self.check_destination(destination)
        if not dest.lower().endswith(".xlsx"):
            raise ImportProblem("Il file Excel deve avere estensione .xlsx.", fixable=False)
        if self.source_kind == "excel":
            version = self.version
            report = self.save_excel_copy(dest)
            report["version"] = version
            return report
        version = self.version
        total = int(self.con.execute(f"SELECT count(*) FROM {self._numbered()} AS n").fetchone()[0])
        if total + 1 > xl.MAX_SHEET_ROWS:
            raise ImportProblem(f"Excel non può contenere più di {xl.MAX_SHEET_ROWS - 1:,} righe di dati per foglio "
                                f"(il dataset ne ha {total:,}). Salva in CSV.".replace(",", "."), fixable=False)
        if len(self.columns) > xl.MAX_SHEET_COLUMNS:
            raise ImportProblem(f"Excel non può contenere più di {xl.MAX_SHEET_COLUMNS} colonne. Salva in CSV.", fixable=False)
        select = ", ".join(quote_identifier(c) for c in self.columns)
        cur = self.con.execute(f'SELECT {select} FROM {self._numbered()} AS n ORDER BY "{RID}"')

        def rows():
            while True:
                chunk = cur.fetchmany(5000)
                if not chunk:
                    return
                yield from chunk

        tmp = self._temp_beside(dest, ".xlsx")
        try:
            try:
                report = xl.write_new_workbook(tmp, sheet_name, list(self.columns), rows())
            except OSError as exc:
                raise _write_problem(exc) from exc
            except xl.ExcelProblem as exc:
                raise ImportProblem(str(exc), fixable=False) from exc
            self._publish(tmp, dest)
        finally:
            if os.path.exists(tmp):
                try:
                    os.remove(tmp)
                except OSError:
                    pass
        report.update({"version": version, "sheet": sheet_name, "rows": total})
        return report

    def excel_edits(self) -> dict[tuple[int, str], str]:
        """Celle cambiate rispetto al foglio importato: {(indice_riga_dati, colonna): testo}.
        Valido solo se la struttura non è cambiata (colonne e righe come all'import)."""
        edits: dict[tuple[int, str], str] = {}
        if not self.mutated or self.structure_changed:
            return edits
        src = f'(SELECT row_number() OVER () - 1 AS "{RID}", * FROM csv_src)'
        for col in self.columns:
            ident = quote_identifier(col)
            rows = self.con.execute(
                f'SELECT w."{RID}", CAST(w.{ident} AS VARCHAR) FROM wk.work w JOIN {src} s ON s."{RID}" = w."{RID}" '
                f"WHERE CAST(w.{ident} AS VARCHAR) IS DISTINCT FROM CAST(s.{ident} AS VARCHAR)").fetchall()
            for rid, text in rows:
                edits[(int(rid), col)] = text or ""
        return edits

    def _excel_types(self) -> dict[str, str]:
        """Tipo con cui scrivere in Excel le celle modificate: testo salvo le colonne a cui l'utente ha assegnato un tipo."""
        out = {}
        for c in self.columns:
            fam = xl_family(self.dtypes.get(c, "VARCHAR"))
            out[c] = {"num": "DOUBLE", "date": "DATE", "timestamp": "TIMESTAMP", "bool": "BOOLEAN"}.get(fam, "VARCHAR")
            if fam == "num" and self.dtypes.get(c, "").upper() in ("BIGINT", "INTEGER", "HUGEINT", "SMALLINT", "TINYINT"):
                out[c] = "BIGINT"
        return out

    def save_excel_copy(self, destination: str) -> dict:
        """Salva una NUOVA copia Excel; l'originale non viene mai toccato.

        Se la struttura non è cambiata si scrivono solo le celle modificate (gli altri contenuti del foglio restano
        quelli dell'originale). Se colonne o righe sono state aggiunte/eliminate/rinominate, il foglio importato viene
        riscritto con i valori del dataset."""
        if self.source_kind != "excel" or not self.excel_meta or not self.csv_path:
            raise RuntimeError("Nessun file Excel aperto.")
        try:
            if self.structure_changed:
                select = ", ".join(quote_identifier(c) for c in self.columns)
                cur = self.con.execute(f'SELECT {select} FROM {self._numbered()} AS n ORDER BY "{RID}"')

                def rows():
                    while True:
                        chunk = cur.fetchmany(5000)
                        if not chunk:
                            return
                        yield from chunk

                report = xl.save_rewrite(self.csv_path, destination, self.excel_meta, list(self.columns), rows())
                report["rewritten"] = True
                return report
            report = xl.save_copy(self.csv_path, destination, self.excel_meta, self.excel_edits(), self._excel_types())
            report["rewritten"] = False
            return report
        except xl.ExcelProblem as exc:
            raise ImportProblem(str(exc), fixable=False) from exc
        except PermissionError as exc:
            raise ImportProblem("Impossibile sostituire il file: è aperto in un'altra applicazione o protetto da scrittura.",
                                fixable=False) from exc
        except OSError as exc:
            raise _write_problem(exc) from exc

    def global_expr(self) -> str | None:
        """Espressione che il motore delle query tra file usa per leggere QUESTO dataset (modifiche incluse).

        Può essere chiamata dal thread della scheda «SQL globale» mentre questa scheda lavora sulla propria connessione:
        l'esportazione usa quindi un cursore separato (vede solo dati confermati, le modifiche sono transazioni) e
        un lock protegge i dati di cache."""
        if not self.mutated:
            return self.view_expr
        with self._export_lock:
            version = self.version
            if self._export_version != version or not self._export_path:
                old = self._export_path
                path = os.path.join(self._tmpdir, f"export_{version}.parquet")
                if os.path.exists(path):
                    os.remove(path)
                cur = self.con.cursor()
                cur.execute(f'COPY (SELECT {self._select_list()} FROM wk.work ORDER BY "{RID}") TO {_sql_str(path)} (FORMAT PARQUET)')
                self._export_path, self._export_version = path, version
                if old and old != path and os.path.exists(old):
                    try:
                        os.remove(old)
                    except OSError:
                        pass
            return f"read_parquet({_sql_str(self._export_path)})"

    @staticmethod
    def _sql_literal(value: str) -> str:
        return "'" + value.replace("'", "''") + "'"

    def run_query(self, sql: str):
        if not self.csv_path:
            raise RuntimeError("Apri prima un file CSV.")
        cur = self.con.cursor()                  # cursore proprio: colonne e righe vengono dalla stessa esecuzione
        rows = cur.execute(sql).fetchall()
        return rows, [d[0] for d in cur.description]

    def export_query(self, sql: str, destination: str, blocked=()) -> None:
        if not self.csv_path:
            raise RuntimeError("Apri prima un file CSV.")
        # mai sul file sorgente né su quelli passati da chi chiama (i file aperti nelle altre schede)
        export_query_to_csv(self.con, sql, destination, [self.csv_path, *blocked])

    def close(self) -> None:
        self.con.close()
        self._cleanup_all()


class GlobalEngine:
    """Connessione che espone più file come viste separate, per le query tra file."""

    def __init__(self) -> None:
        self.con = duckdb.connect(database=":memory:")
        self._views: set[str] = set()

    def sync(self, tables: dict[str, str]) -> None:
        for name in self._views - set(tables):
            self.con.execute(f"DROP VIEW IF EXISTS {quote_identifier(name)}")
        for name, source in tables.items():
            # «source» è un motore di file (si legge il suo dataset, modifiche incluse) o direttamente l'espressione read_csv(...)
            expr = source.global_expr() if hasattr(source, "global_expr") else source
            self.con.execute(f"CREATE OR REPLACE VIEW {quote_identifier(name)} AS SELECT * FROM {expr}")
        self._views = set(tables)

    def run_query(self, tables: dict[str, str], sql: str):
        self.sync(tables)
        cur = self.con.cursor()                  # cursore proprio: colonne e righe vengono dalla stessa esecuzione
        rows = cur.execute(sql).fetchall()
        return rows, [d[0] for d in cur.description]

    def export_query(self, tables: dict[str, str], sql: str, destination: str, blocked=()) -> None:
        self.sync(tables)
        export_query_to_csv(self.con, sql, destination, blocked)

    def close(self) -> None:
        self.con.close()
