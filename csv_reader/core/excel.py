"""Lettura e salvataggio (solo in nuova copia) di file Excel .xlsx e .xls.

Dipendenze: openpyxl (.xlsx) e xlrd >= 2.0.1 (.xls).

Importazione
  * .xlsx: lettura in streaming (openpyxl read_only), valori memorizzati nelle celle
    (data_only): le formule vengono importate come ultimo valore calcolato, se presente.
  * .xls: xlrd carica il foglio in memoria (limite del formato, max 65.536 righe).
  * Il foglio viene scritto in un CSV temporaneo UTF-8 e letto da DuckDB con tutte le colonne di TESTO: nessuna
    conversione automatica. Il testo di ogni cella è quello che Excel mostra con il formato della cella
    (vedi ``xlformat``): «00123» con formato 00000, «1.50» con 0.00, le date con il loro formato.
    Riconoscere i tipi è un'operazione esplicita (Tipi di dati). Le celle di errore (#DIV/0!, #N/A…) restano testo.

Salvataggio
  * Il file originale non viene mai aperto in scrittura né sovrascritto.
  * .xlsx: si carica il file completo con openpyxl e si riscrivono solo le celle modificate;
    gli altri fogli e le formule non toccate restano. Grafici, immagini, macro e parte della
    formattazione avanzata possono andare persi (limite di openpyxl).
  * .xls: il formato non è scrivibile; la copia è un .xlsx con i valori di tutti i fogli.
"""
from __future__ import annotations

import csv
import datetime as dt
import os
import re
import tempfile
from dataclasses import dataclass, field

from csv_reader.core.xlformat import display_text

MAX_SHEET_ROWS = 1_048_576      # limite di righe di un foglio .xlsx (intestazione compresa)
MAX_SHEET_COLUMNS = 16_384
_ILLEGAL_XML = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f]")


class ExcelProblem(Exception):
    """Errore comprensibile all'utente."""


def excel_kind(path: str) -> str | None:
    ext = os.path.splitext(path)[1].lower()
    return {".xlsx": "xlsx", ".xlsm": "xlsx", ".xls": "xls"}.get(ext)


def _need(module: str, pip_name: str):
    try:
        return __import__(module)
    except ImportError as exc:
        raise ExcelProblem(
            f"Per aprire questo file serve il pacchetto «{pip_name}». Installalo con:  pip install {pip_name}"
        ) from exc


# --------------------------------------------------------------------------- elenco fogli

def list_sheets(path: str) -> list[tuple[str, bool]]:
    """Elenco (nome, visibile) dei fogli di dati."""
    kind = excel_kind(path)
    try:
        if kind == "xlsx":
            openpyxl = _need("openpyxl", "openpyxl")
            wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
            try:
                return [(ws.title, ws.sheet_state == "visible") for ws in wb.worksheets]
            finally:
                wb.close()
        if kind == "xls":
            xlrd = _need("xlrd", "xlrd")
            book = xlrd.open_workbook(path, on_demand=True)
            try:
                return [(s.name, getattr(s, "visibility", 0) == 0) for s in (book.sheet_by_index(i) for i in range(book.nsheets))]
            finally:
                book.release_resources()
    except ExcelProblem:
        raise
    except Exception as exc:  # file corrotto, protetto da password, formato diverso dall'estensione
        raise ExcelProblem(_friendly_open_error(exc, path)) from exc
    raise ExcelProblem("Formato Excel non riconosciuto.")


def _friendly_open_error(exc: Exception, path: str) -> str:
    text = str(exc)
    if isinstance(exc, PermissionError):
        return "Il file non è leggibile: controlla i permessi o se è aperto in un'altra applicazione."
    if isinstance(exc, FileNotFoundError):
        return "File non trovato."
    if "encrypt" in text.lower() or "password" in text.lower():
        return "Il file Excel è protetto da password: rimuovi la protezione e riprova."
    if "zip" in text.lower() or "not a" in text.lower() or "xlrd" in text.lower() or "Unsupported format" in text:
        return ("Il file non sembra un documento Excel valido (o è corrotto). "
                "Controlla che l'estensione corrisponda al formato reale.")
    return f"Impossibile leggere il file Excel: {text.splitlines()[0] if text else type(exc).__name__}"


# --------------------------------------------------------------------------- importazione

@dataclass
class ExcelMeta:
    kind: str                       # "xlsx" | "xls"
    sheet: str
    header: bool
    first_data_row: int             # riga del foglio (1-based) del primo record
    header_row: int | None
    columns: list[str] = field(default_factory=list)
    types: dict[str, str] = field(default_factory=dict)   # nome colonna -> tipo DuckDB
    rows: int = 0
    warnings: list[str] = field(default_factory=list)


def _iter_xlsx(path: str, sheet: str):
    openpyxl = _need("openpyxl", "openpyxl")
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    try:
        if sheet not in wb.sheetnames:
            raise ExcelProblem(f"Il foglio «{sheet}» non esiste più nel file.")
        ws = wb[sheet]
        try:
            ws.reset_dimensions()  # dimensioni dichiarate spesso errate: si legge fino alla fine
        except Exception:
            pass
        for row in ws.iter_rows():
            yield tuple(display_text(c.value, getattr(c, "number_format", None)) if c.value is not None else None
                        for c in row)
    finally:
        wb.close()


def _iter_xls(path: str, sheet: str):
    xlrd = _need("xlrd", "xlrd")
    book = xlrd.open_workbook(path, on_demand=True, formatting_info=True)
    try:
        if sheet not in book.sheet_names():
            raise ExcelProblem(f"Il foglio «{sheet}» non esiste più nel file.")
        sh = book.sheet_by_name(sheet)
        for r in range(sh.nrows):
            out = []
            for cell in sh.row(r):
                value = _xls_value(xlrd, book, cell)
                out.append(display_text(value, _xls_format(book, cell)) if value is not None else None)
            yield tuple(out)
    finally:
        book.release_resources()


def _xls_format(book, cell) -> str | None:
    """Formato numero della cella di un .xls (None se non disponibile)."""
    try:
        xf = book.xf_list[cell.xf_index]
        return book.format_map[xf.format_key].format_str
    except Exception:
        return None


def _xls_value(xlrd, book, cell):
    t, v = cell.ctype, cell.value
    if t in (xlrd.XL_CELL_EMPTY, xlrd.XL_CELL_BLANK):
        return None
    if t == xlrd.XL_CELL_DATE:
        try:
            return xlrd.xldate_as_datetime(v, book.datemode)
        except Exception:
            return v
    if t == xlrd.XL_CELL_BOOLEAN:
        return bool(v)
    if t == xlrd.XL_CELL_ERROR:
        return xlrd.error_text_from_code.get(v, "#ERRORE")
    return v


def _unique_names(raw: list, width: int) -> tuple[list[str], int]:
    """Nomi colonna: vuoti -> colonna_N, duplicati -> nome_2, nome_3 (senza distinguere maiuscole)."""
    names, seen, fixed = [], {}, 0
    for j in range(width):
        value = raw[j] if j < len(raw) else None
        name = str(value).strip().replace("\n", " ") if value is not None else ""
        if not name:
            name = f"colonna_{j + 1}"
            fixed += 1
        base, key = name, name.lower()
        if key in seen:
            seen[key] += 1
            name = f"{base}_{seen[key]}"
            while name.lower() in seen:
                seen[key] += 1
                name = f"{base}_{seen[key]}"
            fixed += 1
        seen.setdefault(name.lower(), 1)
        names.append(name)
    return names, fixed


def import_sheet(path: str, sheet: str, dest_csv: str, header: bool = True) -> ExcelMeta:
    """Scrive il foglio in dest_csv (UTF-8, senza intestazione) e restituisce nomi, tipi e mappa righe."""
    kind = excel_kind(path)
    if kind is None:
        raise ExcelProblem("Formato Excel non riconosciuto.")
    try:
        rows = _iter_xlsx(path, sheet) if kind == "xlsx" else _iter_xls(path, sheet)
        return _write_rows(rows, dest_csv, kind, sheet, header)
    except ExcelProblem:
        raise
    except Exception as exc:
        raise ExcelProblem(_friendly_open_error(exc, path)) from exc


def _write_rows(rows, dest_csv: str, kind: str, sheet: str, header: bool) -> ExcelMeta:
    header_values: list = []
    header_row = None
    first_data_row = None
    width = 0
    n_rows = 0
    pending_blank = 0
    sheet_row = 0
    with open(dest_csv, "w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh, lineterminator="\n")
        for values in rows:
            sheet_row += 1
            last = max((i for i, v in enumerate(values) if v is not None and v != ""), default=-1)
            blank = last < 0
            if first_data_row is None:
                if blank:
                    continue  # righe vuote iniziali
                if header:
                    header_row = sheet_row
                    header_values = list(values)
                    width = max(width, last + 1)
                    first_data_row = sheet_row + 1
                    continue
                first_data_row = sheet_row
            if blank:
                pending_blank += 1  # le righe vuote finali vengono scartate, quelle in mezzo conservate
                continue
            for _ in range(pending_blank):
                fh.write('""\n')
                n_rows += 1
            pending_blank = 0
            width = max(width, last + 1)
            writer.writerow(["" if values[j] is None else values[j] for j in range(last + 1)])
            n_rows += 1
    if first_data_row is None:
        raise ExcelProblem(f"Il foglio «{sheet}» non contiene dati.")
    if width == 0:
        raise ExcelProblem(f"Il foglio «{sheet}» non contiene colonne con dati.")
    names, fixed = _unique_names(header_values if header else [], width)
    meta = ExcelMeta(kind=kind, sheet=sheet, header=header, first_data_row=first_data_row,
                     header_row=header_row, columns=names, rows=n_rows)
    meta.types = {name: "VARCHAR" for name in names}         # tutto testo: i tipi si assegnano solo su richiesta
    if fixed:
        meta.warnings.append(f"{fixed} nomi di colonna vuoti o duplicati sono stati rinominati (colonna_N, nome_2…).")
    if n_rows == 0:
        meta.warnings.append("Il foglio contiene solo l'intestazione.")
    return meta


# --------------------------------------------------------------------------- salvataggio in nuova copia

def _convert(text: str, duck_type: str):
    """Converte il testo modificato nel tipo della colonna. Ritorna (valore, convertito_ok)."""
    if text is None or text == "":
        return None, True
    t = text.strip()
    try:
        if duck_type == "BIGINT":
            return int(t), True
        if duck_type == "DOUBLE":
            return float(t.replace(",", ".")) if re.fullmatch(r"[+-]?\d+([.,]\d+)?([eE][+-]?\d+)?", t) else float(t), True
        if duck_type == "BOOLEAN":
            if t.lower() in ("true", "vero", "1", "si", "sì"):
                return True, True
            if t.lower() in ("false", "falso", "0", "no"):
                return False, True
            raise ValueError
        if duck_type == "DATE":
            return dt.date.fromisoformat(t[:10]) if len(t) >= 10 else dt.date.fromisoformat(t), True
        if duck_type == "TIMESTAMP":
            return dt.datetime.fromisoformat(t), True
    except (ValueError, TypeError):
        return text, False  # non convertibile: resta testo
    return text, True


def _put(ws, row: int, column: int, value):
    """Scrive un valore in una cella senza che un testo venga interpretato come formula."""
    if isinstance(value, str):
        value = _ILLEGAL_XML.sub("", value)
    cell = ws.cell(row=row, column=column, value=value)
    if isinstance(value, str) and value.startswith("="):
        cell.data_type = "s"
    return cell


def write_new_workbook(path: str, sheet_name: str, columns: list[str], rows) -> dict:
    """Nuova cartella di lavoro con un solo foglio, scritta in streaming (memoria costante).

    Testi sempre come testo (anche «00123» o «=SOMMA(A1)»), numeri come numeri, date come date, vuoti come celle vuote.
    Gli interi oltre 15 cifre diventano testo: Excel li arrotonderebbe."""
    from openpyxl import Workbook
    from openpyxl.cell import WriteOnlyCell
    from openpyxl.styles import Font
    import decimal as _dec

    wb = Workbook(write_only=True)
    title = re.sub(r"[\\/*?:\[\]]", "_", sheet_name)[:31] or "Dati"
    ws = wb.create_sheet(title)
    ws.freeze_panes = "A2"
    bold = Font(bold=True)
    head = []
    for name in columns:
        cell = WriteOnlyCell(ws, value=_ILLEGAL_XML.sub("", str(name)))
        cell.font = bold
        cell.data_type = "s"
        head.append(cell)
    ws.append(head)
    stripped = 0
    written = 0
    for values in rows:
        out = []
        for value in values:
            if value is None:
                out.append(None)
            elif isinstance(value, str):
                clean = _ILLEGAL_XML.sub("", value)
                stripped += clean != value
                if clean.startswith("="):
                    cell = WriteOnlyCell(ws, value=clean)
                    cell.data_type = "s"
                    out.append(cell)
                else:
                    out.append(clean)
            elif isinstance(value, bool):
                out.append(value)
            elif isinstance(value, int):
                if abs(value) >= 10 ** 15:
                    out.append(str(value))
                else:
                    out.append(value)
            elif isinstance(value, _dec.Decimal):
                out.append(float(value))
            elif isinstance(value, dt.datetime):
                cell = WriteOnlyCell(ws, value=value)
                cell.number_format = "yyyy-mm-dd hh:mm:ss"
                out.append(cell)
            elif isinstance(value, dt.date):
                cell = WriteOnlyCell(ws, value=value)
                cell.number_format = "yyyy-mm-dd"
                out.append(cell)
            else:
                out.append(value if isinstance(value, (float, dt.time)) else str(value))
        ws.append(out)
        written += 1
    wb.save(path)
    return {"written": written, "as_text": 0, "skipped": 0, "stripped": stripped, "converted_from_xls": False}


def save_copy(source: str, destination: str, meta: ExcelMeta, edits: dict, types: dict | None = None) -> dict:
    """Crea una NUOVA copia con le modifiche. Non apre mai l'originale in scrittura.

    edits: {(indice_riga_dati_0based, nome_colonna): testo}. types: tipo DuckDB attuale di ogni colonna (le celle
    modificate di una colonna con un tipo assegnato dall'utente sono scritte come numeri/date, le altre come testo).
    Ritorna un resoconto."""
    types = types if types is not None else meta.types
    src = os.path.abspath(source)
    dst = os.path.abspath(destination)
    if os.path.exists(dst) and os.path.samefile(src, dst):
        raise ExcelProblem("Non è possibile sovrascrivere il file originale: scegli un altro nome per la copia.")
    if not dst.lower().endswith(".xlsx"):
        raise ExcelProblem("La copia deve avere estensione .xlsx.")
    openpyxl = _need("openpyxl", "openpyxl")
    report = {"written": 0, "as_text": 0, "skipped": 0, "converted_from_xls": meta.kind == "xls"}
    positions = {name: j for j, name in enumerate(meta.columns)}

    tmp_fd, tmp_path = tempfile.mkstemp(suffix=".xlsx", dir=os.path.dirname(dst) or None)
    os.close(tmp_fd)
    try:
        if meta.kind == "xlsx":
            wb = openpyxl.load_workbook(src)  # modalità completa: conserva fogli e formule non toccate
            ws = wb[meta.sheet]
        else:
            wb, ws = _xls_to_workbook(src, meta, openpyxl)
        for (row_idx, col_name), text in edits.items():
            j = positions.get(col_name)
            if j is None:
                continue
            value, ok = _convert(text, types.get(col_name, "VARCHAR"))
            try:
                cell = _put(ws, meta.first_data_row + int(row_idx), j + 1, value)
                if isinstance(value, dt.datetime):
                    cell.number_format = "yyyy-mm-dd hh:mm:ss"
                elif isinstance(value, dt.date):
                    cell.number_format = "yyyy-mm-dd"
                report["written"] += 1
                if not ok:
                    report["as_text"] += 1
            except AttributeError:  # cella in un'area unita: non modificabile
                report["skipped"] += 1
        wb.save(tmp_path)
        os.replace(tmp_path, dst)
    finally:
        if os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except OSError:
                pass
    return report


def save_rewrite(source: str, destination: str, meta: ExcelMeta, columns: list[str], rows) -> dict:
    """NUOVA copia in cui il foglio importato è riscritto con il dataset corrente (valori, non formule).

    Serve quando colonne o righe sono state aggiunte, eliminate o rinominate: le celle non si corrispondono più
    una a una con l'originale. Gli altri fogli e le righe sopra l'intestazione restano quelli dell'originale;
    la formattazione e le formule del foglio riscritto non vengono mantenute. L'originale non viene mai toccato."""
    src = os.path.abspath(source)
    dst = os.path.abspath(destination)
    if os.path.exists(dst) and os.path.samefile(src, dst):
        raise ExcelProblem("Non è possibile sovrascrivere il file originale: scegli un altro nome per la copia.")
    if not dst.lower().endswith(".xlsx"):
        raise ExcelProblem("La copia deve avere estensione .xlsx.")
    openpyxl = _need("openpyxl", "openpyxl")
    tmp_fd, tmp_path = tempfile.mkstemp(suffix=".xlsx", dir=os.path.dirname(dst) or None)
    os.close(tmp_fd)
    written = 0
    try:
        if meta.kind == "xlsx":
            wb = openpyxl.load_workbook(src)
            ws = wb[meta.sheet]
        else:
            wb, ws = _xls_to_workbook(src, meta, openpyxl)
        start = meta.header_row if (meta.header and meta.header_row) else meta.first_data_row
        if ws.max_row >= start:
            ws.delete_rows(start, ws.max_row - start + 1)
        r = start
        if meta.header:
            for j, name in enumerate(columns):
                _put(ws, r, j + 1, name)
            r += 1
        for values in rows:
            for j, value in enumerate(values):
                if value is None:
                    continue
                cell = _put(ws, r, j + 1, value)
                if isinstance(value, dt.datetime):
                    cell.number_format = "yyyy-mm-dd hh:mm:ss"
                elif isinstance(value, dt.date):
                    cell.number_format = "yyyy-mm-dd"
            r += 1
            written += 1
        wb.save(tmp_path)
        os.replace(tmp_path, dst)
    finally:
        if os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except OSError:
                pass
    return {"written": written, "as_text": 0, "skipped": 0, "converted_from_xls": meta.kind == "xls"}


def _xls_to_workbook(path: str, meta: ExcelMeta, openpyxl):
    xlrd = _need("xlrd", "xlrd")
    book = xlrd.open_workbook(path)
    wb = openpyxl.Workbook()
    wb.remove(wb.active)
    target = None
    for i in range(book.nsheets):
        sh = book.sheet_by_index(i)
        ws = wb.create_sheet(sh.name[:31])
        for r in range(sh.nrows):
            for c in range(sh.ncols):
                value = _xls_value(xlrd, book, sh.cell(r, c))
                if value is None:
                    continue
                cell = ws.cell(row=r + 1, column=c + 1, value=value)
                if isinstance(value, dt.datetime):
                    cell.number_format = "yyyy-mm-dd hh:mm:ss" if value.time() != dt.time(0) else "yyyy-mm-dd"
        if sh.name == meta.sheet:
            target = ws
    book.release_resources()
    return wb, target
