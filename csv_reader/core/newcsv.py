"""Creazione di un nuovo file CSV vuoto (intestazioni «Colonna1», «Colonna2», … e celle vuote).

Il file viene scritto a blocchi, quindi non si costruisce mai in memoria un dataset grande; la scrittura è atomica
(file temporaneo accanto alla destinazione, poi sostituzione)."""
from __future__ import annotations

import os
import tempfile

from csv_reader.core.engine import DELIMITERS, ENCODINGS, ImportProblem, _write_problem

MAX_COLUMNS = 5_000
MAX_ROWS = 10_000_000
MAX_CELLS = 200_000_000
_INVALID_NAME_CHARS = '<>:"/\\|?*'
_RESERVED = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}


def parse_count(text: str, label: str, minimum: int, maximum: int) -> int:
    """Numero intero ≥ minimum e ≤ maximum, con messaggi chiari per testo vuoto, non numerico o troppo grande."""
    raw = "".join((text or "").split())      # spazi (anche sottili) ignorati
    if not raw:
        raise ImportProblem(f"Indica {label}.", fixable=False)
    if not (raw.isdigit() or (raw[0] in "+-" and raw[1:].isdigit())):
        raise ImportProblem(f"{label[0].upper() + label[1:]} deve essere un numero intero (hai scritto «{text.strip()}»).", fixable=False)
    value = int(raw)
    if value < minimum:
        raise ImportProblem(f"{label[0].upper() + label[1:]} deve essere almeno {minimum:,}.".replace(",", "."), fixable=False)
    if value > maximum:
        raise ImportProblem(f"{label[0].upper() + label[1:]} non può superare {maximum:,} (hai scritto {value:,}).".replace(",", "."),
                            fixable=False)
    return value


def clean_file_name(name: str) -> str:
    name = (name or "").strip()
    if not name:
        raise ImportProblem("Indica il nome del file.", fixable=False)
    if any(ch in name for ch in _INVALID_NAME_CHARS) or any(ord(ch) < 32 for ch in name):
        raise ImportProblem(f"Il nome del file non può contenere questi caratteri: {_INVALID_NAME_CHARS}", fixable=False)
    if not name.lower().endswith(".csv"):
        name += ".csv"
    stem = name[:-4].strip(". ")
    if not stem:
        raise ImportProblem("Il nome del file non è valido.", fixable=False)
    if stem.split(".")[0].upper() in _RESERVED:
        raise ImportProblem("Questo nome è riservato dal sistema operativo: scegline un altro.", fixable=False)
    if len(name) > 200:
        raise ImportProblem("Il nome del file è troppo lungo.", fixable=False)
    return name


def validate(columns_text: str, rows_text: str, folder: str, name: str, encoding: str, delimiter: str) -> dict:
    """Valida i dati inseriti e ritorna {"columns", "rows", "path", "encoding", "delimiter"}; solleva ImportProblem."""
    columns = parse_count(columns_text, "il numero di colonne", 1, MAX_COLUMNS)
    rows = parse_count(rows_text, "il numero di righe di dati", 0, MAX_ROWS)
    if columns * max(rows, 1) > MAX_CELLS:
        raise ImportProblem(f"{columns:,} colonne per {rows:,} righe sono {columns * rows:,} celle: il massimo è {MAX_CELLS:,}."
                            .replace(",", "."), fixable=False)
    folder = (folder or "").strip()
    if not folder:
        raise ImportProblem("Scegli la cartella di destinazione.", fixable=False)
    if not os.path.isdir(folder):
        raise ImportProblem("La cartella di destinazione non esiste.", fixable=False)
    if not os.access(folder, os.W_OK):
        raise ImportProblem("Non hai il permesso di scrivere nella cartella di destinazione.", fixable=False)
    if encoding not in ENCODINGS:
        raise ImportProblem("Codifica non supportata.", fixable=False)
    if delimiter not in DELIMITERS:
        raise ImportProblem("Delimitatore non supportato.", fixable=False)
    path = os.path.join(folder, clean_file_name(name))
    if os.path.isdir(path):
        raise ImportProblem("Esiste già una cartella con questo nome.", fixable=False)
    return {"columns": columns, "rows": rows, "path": path, "encoding": encoding, "delimiter": delimiter}


def create_csv(path: str, columns: int, rows: int, delimiter: str = ",", encoding: str = "utf-8") -> dict:
    """Scrive il file. Con una sola colonna le righe vuote sono scritte come \"\" (una riga completamente vuota
    verrebbe ignorata dai lettori CSV). Ritorna {"path", "columns", "rows"}."""
    folder = os.path.dirname(os.path.abspath(path))
    fd, tmp = tempfile.mkstemp(prefix=".csvreader_", suffix=".tmp", dir=folder)
    os.close(fd)
    try:
        with open(tmp, "w", encoding=encoding, newline="") as fh:
            fh.write(delimiter.join(f"Colonna{i}" for i in range(1, columns + 1)) + "\n")
            line = ('""' if columns == 1 else delimiter * (columns - 1)) + "\n"
            block = line * max(1, min(rows, (1 << 20) // len(line)))
            left = rows
            per_block = block.count("\n")
            while left > 0:
                if left >= per_block:
                    fh.write(block)
                    left -= per_block
                else:
                    fh.write(line * left)
                    left = 0
        os.replace(tmp, path)
    except UnicodeEncodeError as exc:                               # non può accadere con intestazioni ASCII, ma si resta cauti
        raise ImportProblem("La codifica scelta non può rappresentare le intestazioni.", fixable=False) from exc
    except PermissionError as exc:
        raise ImportProblem("Impossibile scrivere il file: è aperto in un'altra applicazione o la cartella è protetta.",
                            fixable=False) from exc
    except OSError as exc:
        raise _write_problem(exc) from exc
    finally:
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass
    return {"path": path, "columns": columns, "rows": rows}
