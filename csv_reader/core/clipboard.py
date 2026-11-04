"""Testo degli appunti in formato «tabulazioni» compatibile con Excel (e LibreOffice, Numbers, Google Fogli).

Le celle sono separate da TAB e le righe da a capo. Una cella che contiene TAB, a capo o virgolette viene scritta
tra virgolette (con le virgolette interne raddoppiate), esattamente come fa Excel."""
from __future__ import annotations

_SPECIAL = ("\t", "\n", "\r", '"')


def quote_cell(text: str) -> str:
    if any(ch in text for ch in _SPECIAL):
        return '"' + text.replace('"', '""') + '"'
    return text


def format_tsv(rows: list[list[str]]) -> str:
    return "\n".join("\t".join(quote_cell(c) for c in row) for row in rows)


def parse_tsv(text: str) -> list[list[str]]:
    """Inverso di format_tsv. Accetta anche gli appunti di Excel (CRLF, a capo finale, celle tra virgolette)."""
    rows: list[list[str]] = []
    row: list[str] = []
    cell: list[str] = []
    i, n = 0, len(text)
    at_start = True            # all'inizio di una cella: un'eventuale virgoletta la apre
    while i < n:
        ch = text[i]
        if at_start and ch == '"':
            j = i + 1
            buf: list[str] = []
            closed = False
            while j < n:
                if text[j] == '"':
                    if j + 1 < n and text[j + 1] == '"':
                        buf.append('"')
                        j += 2
                        continue
                    closed = True
                    j += 1
                    break
                buf.append(text[j])
                j += 1
            if closed and (j >= n or text[j] in "\t\r\n"):
                cell = buf
                i = j
                at_start = False
                continue
            # virgolette non chiuse correttamente: si trattano come testo normale
            cell.append('"')
            i += 1
            at_start = False
            continue
        if ch == "\t":
            row.append("".join(cell))
            cell = []
            at_start = True
            i += 1
        elif ch in "\r\n":
            row.append("".join(cell))
            rows.append(row)
            row, cell = [], []
            at_start = True
            i += 2 if (ch == "\r" and i + 1 < n and text[i + 1] == "\n") else 1
        else:
            cell.append(ch)
            at_start = False
            i += 1
    if cell or row or not at_start:
        row.append("".join(cell))
        rows.append(row)
    return rows
