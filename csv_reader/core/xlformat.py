"""Testo visualizzato di una cella Excel (valore + formato numero), senza conversioni.

L'importazione di un foglio Excel tratta ogni cella come TESTO: il testo è quello che Excel mostrerebbe con il
formato della cella (``00123`` con formato ``00000``, ``1.50`` con ``0.00``, la data con il suo formato…).
Il valore sottostante non viene mai modificato nel file originale. Il formato è interpretato in modo approssimato
ma prevedibile; cosa non viene riconosciuto ricade sul testo «generale» (data ISO, numero con al massimo 15 cifre).

Convenzioni: separatore decimale «.» e delle migliaia «,» (il file non contiene le impostazioni regionali);
nomi di mesi e giorni in italiano.
"""
from __future__ import annotations

import datetime as dt
import re
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

MONTHS = ["gennaio", "febbraio", "marzo", "aprile", "maggio", "giugno", "luglio", "agosto", "settembre",
          "ottobre", "novembre", "dicembre"]
DAYS = ["lunedì", "martedì", "mercoledì", "giovedì", "venerdì", "sabato", "domenica"]

_TOKEN = re.compile(
    r'"(?P<q>[^"]*)"|\\(?P<esc>.)|_(?P<pad>.)|\*(?P<fill>.)|\[(?P<br>[^\]]*)\]|'
    r'(?P<ampm>AM/PM|A/P)|(?P<y>y{1,4})|(?P<mo>m{1,5})|(?P<d>d{1,4})|(?P<h>h{1,2})|(?P<s>s{1,2})|(?P<other>.)',
    re.IGNORECASE)


def general(value) -> str:
    """Formato «Generale»."""
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        if value != value or value in (float("inf"), float("-inf")):
            return str(value)
        if value.is_integer() and abs(value) < 1e15:
            return str(int(value))
        return format(value, ".15g")
    return str(value)


def _sections(fmt: str) -> list[str]:
    out, cur, quoted = [], [], False
    for ch in fmt:
        if ch == '"':
            quoted = not quoted
        if ch == ";" and not quoted:
            out.append("".join(cur))
            cur = []
        else:
            cur.append(ch)
    out.append("".join(cur))
    return out


def _bracket_literal(text: str) -> str:
    """[$€-410] -> «€»; condizioni e colori -> nulla."""
    if text.startswith("$"):
        return text[1:].split("-")[0]
    return ""


def _is_date_section(section: str) -> bool:
    cleaned = re.sub(r'"[^"]*"|\\.|\[[^\]]*\]', "", section)
    return bool(re.search(r"[ymdhs]", cleaned, re.IGNORECASE)) and not re.search(r"[0#]", cleaned)


# --------------------------------------------------------------------------- numeri

def format_number(value, fmt: str | None) -> str:
    if isinstance(value, bool):
        return general(value)
    if not fmt or fmt.lower() == "general" or fmt == "@":
        return general(value)
    parts = _sections(fmt)
    number = value
    negative = number < 0
    if negative and len(parts) >= 2 and parts[1].strip():
        section, sign = parts[1], ""
        number = -number
    elif number == 0 and len(parts) >= 3 and parts[2].strip():
        section, sign = parts[2], ""
    else:
        section, sign = parts[0], "-" if negative else ""
        number = abs(number)
    if not section.strip() or "/" in re.sub(r'"[^"]*"|\\.|\[[^\]]*\]', "", section):
        return general(value)
    try:
        return sign + _render_number(float(number) if not isinstance(number, int) else number, section)
    except (InvalidOperation, ValueError, OverflowError):
        return general(value)


def _render_number(number, section: str) -> str:
    plain = re.sub(r'"[^"]*"|\\.|\[[^\]]*\]|_.|\*.', "", section)
    if not re.search(r"[0#?]", plain) and "General" not in section:
        raise ValueError("nessun segnaposto numerico")
    percent = "%" in plain
    scientific = re.search(r"E[+-]", plain, re.IGNORECASE) is not None
    if percent:
        number = Decimal(str(number)) * 100
    out: list[str] = []
    i, done = 0, False
    while i < len(section):
        ch = section[i]
        if ch == '"':
            j = section.find('"', i + 1)
            j = len(section) if j < 0 else j
            out.append(section[i + 1:j])
            i = j + 1
        elif ch == "\\" and i + 1 < len(section):
            out.append(section[i + 1])
            i += 2
        elif ch == "_":
            out.append(" ")
            i += 2
        elif ch == "*":
            i += 2
        elif ch == "[":
            j = section.find("]", i)
            j = len(section) if j < 0 else j
            out.append(_bracket_literal(section[i + 1:j]))
            i = j + 1
        elif section[i:i + 7].lower() == "general":
            out.append(general(float(number) if not isinstance(number, int) else number))
            i += 7
        elif ch in "0#?.,":
            j = i
            while j < len(section) and section[j] in "0#?.,":
                j += 1
            run = section[i:j]
            i = j
            if done:
                continue
            done = True
            exp_digits = 0
            if scientific:
                m = re.match(r"[Ee][+-](0+)", section[i:])
                if m:
                    exp_digits = len(m.group(1))
                    i += len(m.group(0))
            out.append(_render_run(number, run, exp_digits))
        elif ch == "%":
            out.append("%")
            i += 1
        elif ch in "Ee" and False:        # pragma: no cover
            i += 1
        else:
            out.append(ch)
            i += 1
    return "".join(out)


def _render_run(number, run: str, exp_digits: int) -> str:
    int_pat, _, dec_pat = run.partition(".")
    decimals = len([c for c in dec_pat if c in "0#?"])
    min_decimals = dec_pat.count("0")
    trailing = len(int_pat) - len(int_pat.rstrip(","))           # virgole finali: divide per 1000 ciascuna
    int_pat = int_pat.rstrip(",")
    thousands = "," in int_pat
    min_int = int_pat.count("0")
    value = Decimal(str(number))
    if trailing:
        value /= Decimal(1000) ** trailing
    if exp_digits:
        digits = max(1, decimals)
        text = f"{float(value):.{decimals}E}"
        mant, _, exp = text.partition("E")
        sign = exp[0]
        exp = exp[1:].lstrip("0").rjust(exp_digits, "0")
        del digits
        return f"{mant}E{sign}{exp}"
    quant = Decimal(1).scaleb(-decimals)
    value = value.quantize(quant, rounding=ROUND_HALF_UP)
    text = f"{value:f}"
    whole, _, frac = text.partition(".")
    whole = whole.lstrip("0")
    if len(whole) < min_int or (not whole and not min_int and decimals == 0):
        whole = whole.rjust(max(min_int, 1 if decimals == 0 else 0), "0")
    if thousands and whole:
        whole = f"{int(whole):,}".rjust(len(whole), "0") if whole.isdigit() else whole
    frac = frac.ljust(decimals, "0")
    while len(frac) > min_decimals and frac.endswith("0"):
        frac = frac[:-1]
    return f"{whole}.{frac}" if frac else whole


# --------------------------------------------------------------------------- date e ore

def format_datetime(value, fmt: str | None) -> str:
    if isinstance(value, dt.timedelta):
        total = int(round(value.total_seconds()))
        sign = "-" if total < 0 else ""
        total = abs(total)
        hours, rest = divmod(total, 3600)
        minutes, seconds = divmod(rest, 60)
        if fmt and "[h" in fmt.lower():
            return f"{sign}{hours}:{minutes:02d}:{seconds:02d}"
        value = dt.datetime(1899, 12, 31) + dt.timedelta(seconds=total)
    if isinstance(value, dt.time):
        value = dt.datetime.combine(dt.date(1900, 1, 1), value)
    elif isinstance(value, dt.date) and not isinstance(value, dt.datetime):
        value = dt.datetime(value.year, value.month, value.day)
    if not fmt or fmt.lower() == "general":
        return _iso(value)
    section = _sections(fmt)[0]
    tokens = [(m.lastgroup, m.group(0), m) for m in _TOKEN.finditer(section)]
    kinds = [k for k, _, _ in tokens]
    if not any(k in ("y", "mo", "d", "h", "s") for k in kinds):
        return _iso(value)
    ampm = "ampm" in kinds
    # «m» = minuti se segue un'ora o precede i secondi, altrimenti mese
    significant = [i for i, k in enumerate(kinds) if k in ("y", "mo", "d", "h", "s")]
    out: list[str] = []
    for pos, (kind, text, match) in enumerate(tokens):
        low = text.lower()
        if kind == "q":
            out.append(match.group("q"))
        elif kind == "esc":
            out.append(match.group("esc"))
        elif kind == "pad":
            out.append(" ")
        elif kind in ("fill",):
            continue
        elif kind == "br":
            inner = match.group("br").lower()
            if inner and inner[0] in "hms" and set(inner) <= {"h", "m", "s"}:       # [h] ore trascorse
                out.append(f"{value.hour:02d}" if len(inner) > 1 else str(value.hour))
            else:
                out.append(_bracket_literal(match.group("br")))
        elif kind == "ampm":
            out.append(("AM" if value.hour < 12 else "PM") if low == "am/pm" else ("A" if value.hour < 12 else "P"))
        elif kind == "y":
            out.append(f"{value.year:04d}" if len(low) > 2 else f"{value.year % 100:02d}")
        elif kind == "mo":
            idx = significant.index(pos)
            prev_h = idx > 0 and kinds[significant[idx - 1]] == "h"
            next_s = idx + 1 < len(significant) and kinds[significant[idx + 1]] == "s"
            if (prev_h or next_s) and len(low) <= 2:
                out.append(f"{value.minute:02d}" if len(low) == 2 else str(value.minute))
            else:
                n = len(low)
                out.append(str(value.month) if n == 1 else f"{value.month:02d}" if n == 2 else MONTHS[value.month - 1][:3]
                           if n == 3 else MONTHS[value.month - 1] if n == 4 else MONTHS[value.month - 1][0])
        elif kind == "d":
            n = len(low)
            out.append(str(value.day) if n == 1 else f"{value.day:02d}" if n == 2 else DAYS[value.weekday()][:3]
                       if n == 3 else DAYS[value.weekday()])
        elif kind == "h":
            hour = value.hour % 12 or 12 if ampm else value.hour
            out.append(f"{hour:02d}" if len(low) == 2 else str(hour))
        elif kind == "s":
            out.append(f"{value.second:02d}" if len(low) == 2 else str(value.second))
        else:
            out.append(text)
    return "".join(out)


def _iso(value: dt.datetime) -> str:
    if value.time() == dt.time(0):
        return value.date().isoformat()
    return value.isoformat(sep=" ", timespec="seconds" if not value.microsecond else "microseconds")


def display_text(value, fmt: str | None) -> str:
    """Testo che Excel mostra per ``value`` con il formato numero ``fmt`` (None -> cella vuota)."""
    if value is None:
        return ""
    if isinstance(value, (dt.datetime, dt.date, dt.time, dt.timedelta)):
        return format_datetime(value, fmt)
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if fmt and _is_date_section(_sections(fmt)[0]):
            return general(value)
        return format_number(value, fmt)
    if isinstance(value, bool):
        return general(value)
    return str(value)
