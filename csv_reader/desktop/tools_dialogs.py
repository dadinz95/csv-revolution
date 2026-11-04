"""Finestre della Fase 2: formattazione condizionale e trova/sostituisci.

Sono finestre «mute»: raccolgono le scelte dell'utente ed emettono segnali; la logica sta in CsvTab e nel motore."""
from __future__ import annotations

import os
import uuid

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QSpinBox, QFileDialog, QFormLayout, QStyle, QStyledItemDelegate, QAbstractItemView, QCheckBox, QColorDialog, QComboBox, QDialog, QHBoxLayout, QHeaderView, QLabel, QLineEdit,
    QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from csv_reader.core.coltypes import LABELS as TYPE_LABELS, TYPES
from csv_reader.core.engine import DELIMITERS, ENCODINGS
from csv_reader.core.conditions import (
    CONDITIONS, CONDITION_LABELS, NO_VALUE_OPS, describe_condition, validate_condition,
)

#: colori di sfondo suggeriti: di luminosità media, leggibili con il testo chiaro e con quello scuro
PRESET_COLORS = ["#F4B183", "#A9D18E", "#9DC3E6", "#FFD966", "#FF8F8F", "#C9A7E8", "#8FD6C9", "#BFBFBF"]


class BackgroundDelegate(QStyledItemDelegate):
    """Disegna lo sfondo assegnato alla cella (formattazione condizionale): il foglio di stile dell'applicazione
    altrimenti lo ignorerebbe. Le celle selezionate mantengono il colore di selezione."""

    def paint(self, painter, option, index):
        brush = index.data(Qt.BackgroundRole)
        if brush is not None and not (option.state & QStyle.State_Selected):
            painter.fillRect(option.rect, brush)
        super().paint(painter, option, index)
        # linea divisoria verticale sottile (disegnata qui: resta visibile durante lo scorrimento)
        from csv_reader.desktop.app import COLORS as c
        line = QColor(c.get("border", "#888888"))
        text = QColor(c.get("text", "#000000"))
        line = QColor((line.red() * 7 + text.red()) // 8, (line.green() * 7 + text.green()) // 8, (line.blue() * 7 + text.blue()) // 8)
        painter.save()
        painter.setPen(line)
        x = option.rect.right()
        painter.drawLine(x, option.rect.top(), x, option.rect.bottom())
        painter.restore()


class ColorButton(QPushButton):
    """Pulsante che mostra il colore scelto e apre il selettore di colore."""

    changed = Signal(str)

    def __init__(self, color: str = PRESET_COLORS[0]):
        super().__init__("")
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedWidth(64)
        self.setToolTip("Scegli il colore di sfondo")
        self._color = color
        self.clicked.connect(self._pick)
        self._paint()

    def color(self) -> str:
        return self._color

    def set_color(self, color: str) -> None:
        self._color = color
        self._paint()
        self.changed.emit(color)

    def _paint(self) -> None:
        self.setStyleSheet(f"QPushButton {{ background-color: {self._color}; border: 1px solid #888; border-radius: 7px; }}")

    def _pick(self) -> None:
        chosen = QColorDialog.getColor(QColor(self._color), self, "Colore di sfondo")
        if chosen.isValid():
            self.set_color(chosen.name())


class CfRuleDialog(QDialog):
    """Crea o modifica una regola: colonna + condizione + valore + colore di sfondo."""

    def __init__(self, parent, columns: list[str], dtypes: dict[str, str], rule: dict | None = None):
        super().__init__(parent)
        self.setWindowTitle("Regola di formattazione" if rule is None else "Modifica regola")
        self.setMinimumWidth(460)
        self.dtypes = dtypes
        self.result_rule: dict | None = None
        self._id = (rule or {}).get("id") or uuid.uuid4().hex[:8]
        self._enabled = (rule or {}).get("enabled", True)
        rule = rule or {}

        lay = QVBoxLayout(self)
        lay.setContentsMargins(18, 16, 18, 14)
        lay.setSpacing(10)
        note = QLabel("Colora lo sfondo delle celle della colonna quando la condizione è vera. "
                      "Testo, carattere e valori non vengono modificati.")
        note.setObjectName("hint")
        note.setWordWrap(True)
        lay.addWidget(note)

        row = QHBoxLayout()
        row.setSpacing(8)
        self.column = QComboBox()
        for c in columns:
            self.column.addItem(c, c)
        if rule.get("column") in columns:
            self.column.setCurrentIndex(columns.index(rule["column"]))
        self.condition = QComboBox()
        for key, label in CONDITIONS:
            self.condition.addItem(label, key)
        self.condition.setCurrentIndex(max(0, self.condition.findData(rule.get("op", "contains"))))
        self.value = QLineEdit(rule.get("value", ""))
        self.value.setPlaceholderText("Valore")
        row.addWidget(self.column, 1)
        row.addWidget(self.condition)
        row.addWidget(self.value, 1)
        lay.addLayout(row)
        self.case_box = QCheckBox("Maiuscole/minuscole")
        self.case_box.setChecked(bool(rule.get("case")))
        lay.addWidget(self.case_box)

        color_row = QHBoxLayout()
        color_row.setSpacing(6)
        color_row.addWidget(QLabel("Colore di sfondo"))
        self.color = ColorButton(rule.get("color", PRESET_COLORS[0]))
        color_row.addWidget(self.color)
        for preset in PRESET_COLORS:
            b = QPushButton("")
            b.setFixedSize(22, 22)
            b.setCursor(Qt.PointingHandCursor)
            b.setStyleSheet(f"QPushButton {{ background-color: {preset}; border: 1px solid #888; border-radius: 11px; }}")
            b.clicked.connect(lambda _=False, c=preset: self.color.set_color(c))
            color_row.addWidget(b)
        color_row.addStretch(1)
        lay.addLayout(color_row)

        self.error = QLabel("")
        self.error.setObjectName("error")
        self.error.setWordWrap(True)
        self.error.hide()
        lay.addWidget(self.error)

        footer = QHBoxLayout()
        footer.addStretch(1)
        cancel = QPushButton("Annulla")
        cancel.clicked.connect(self.reject)
        ok = QPushButton("Salva regola")
        ok.setObjectName("primary")
        ok.setDefault(True)
        ok.clicked.connect(self._save)
        footer.addWidget(cancel)
        footer.addWidget(ok)
        lay.addLayout(footer)
        self.condition.currentIndexChanged.connect(self._condition_changed)
        self.column.currentIndexChanged.connect(lambda _i: self.error.hide())
        self._condition_changed()

    def _condition_changed(self) -> None:
        op = self.condition.currentData()
        self.value.setEnabled(op not in NO_VALUE_OPS)
        self.case_box.setEnabled(op not in NO_VALUE_OPS and op not in ("gt", "lt"))
        self.error.hide()

    def _save(self) -> None:
        op, value, column = self.condition.currentData(), self.value.text(), self.column.currentData()
        problem = validate_condition(self.dtypes.get(column), op, value)
        if column is None:
            problem = "Scegli una colonna."
        if problem:
            self.error.setText(problem)
            self.error.show()
            return
        self.result_rule = {"id": self._id, "enabled": self._enabled, "column": column, "op": op,
                            "value": value if op not in NO_VALUE_OPS else "", "case": self.case_box.isChecked(),
                            "color": self.color.color()}
        self.accept()


def describe_rule(rule: dict) -> str:
    return describe_condition(rule["op"], rule.get("value", ""), bool(rule.get("case")))


class CfDialog(QDialog):
    """Elenco delle regole di formattazione condizionale: aggiungi, modifica, disattiva, elimina, cambia precedenza."""

    def __init__(self, parent, rules: list[dict], columns: list[str], dtypes: dict[str, str]):
        super().__init__(parent)
        self.setWindowTitle("Formattazione condizionale")
        self.resize(640, 420)
        self.columns, self.dtypes = columns, dtypes
        self.rules: list[dict] = [dict(r) for r in rules]
        self.result_rules: list[dict] | None = None

        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 14, 16, 12)
        lay.setSpacing(8)
        note = QLabel("Le regole colorano soltanto lo sfondo delle celle e non toccano i dati. "
                      "L'ordine dell'elenco è la precedenza: se più regole valgono per la stessa cella, vince quella più in alto.")
        note.setObjectName("hint")
        note.setWordWrap(True)
        lay.addWidget(note)

        body = QHBoxLayout()
        self.table = QTableWidget(0, 4)
        self.table.setItemDelegate(BackgroundDelegate(self.table))
        self.table.setHorizontalHeaderLabels(["Attiva", "Colonna", "Condizione", "Colore"])
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        h = self.table.horizontalHeader()
        h.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        h.setSectionResizeMode(1, QHeaderView.Stretch)
        h.setSectionResizeMode(2, QHeaderView.Stretch)
        h.setSectionResizeMode(3, QHeaderView.ResizeToContents)
        self.table.itemChanged.connect(self._toggled)
        self.table.doubleClicked.connect(lambda _i: self.edit())
        body.addWidget(self.table, 1)

        side = QVBoxLayout()
        side.setSpacing(6)
        self.add_button = QPushButton("Aggiungi…")
        self.edit_button = QPushButton("Modifica…")
        self.delete_button = QPushButton("Elimina")
        self.up_button = QPushButton("Su")
        self.down_button = QPushButton("Giù")
        for b in (self.add_button, self.edit_button, self.delete_button, self.up_button, self.down_button):
            side.addWidget(b)
        side.addStretch(1)
        body.addLayout(side)
        lay.addLayout(body, 1)
        self.add_button.clicked.connect(self.add)
        self.edit_button.clicked.connect(self.edit)
        self.delete_button.clicked.connect(self.delete)
        self.up_button.clicked.connect(lambda: self.move(-1))
        self.down_button.clicked.connect(lambda: self.move(+1))
        self.table.itemSelectionChanged.connect(self._buttons)

        footer = QHBoxLayout()
        footer.addStretch(1)
        cancel = QPushButton("Annulla")
        cancel.clicked.connect(self.reject)
        self.apply_button = QPushButton("Applica")
        self.apply_button.setObjectName("primary")
        self.apply_button.setDefault(True)
        self.apply_button.clicked.connect(self._apply)
        footer.addWidget(cancel)
        footer.addWidget(self.apply_button)
        lay.addLayout(footer)
        self._fill()

    def _fill(self, select: int | None = None) -> None:
        self.table.blockSignals(True)
        self.table.setRowCount(len(self.rules))
        for r, rule in enumerate(self.rules):
            check = QTableWidgetItem("")
            check.setFlags(Qt.ItemIsUserCheckable | Qt.ItemIsEnabled | Qt.ItemIsSelectable)
            check.setCheckState(Qt.Checked if rule.get("enabled", True) else Qt.Unchecked)
            self.table.setItem(r, 0, check)
            self.table.setItem(r, 1, QTableWidgetItem(rule["column"]))
            self.table.setItem(r, 2, QTableWidgetItem(describe_rule(rule)))
            swatch = QTableWidgetItem("      ")
            swatch.setBackground(QColor(rule["color"]))
            swatch.setToolTip(rule["color"])
            self.table.setItem(r, 3, swatch)
        self.table.blockSignals(False)
        if select is not None and 0 <= select < len(self.rules):
            self.table.selectRow(select)
        self._buttons()

    def _current(self) -> int:
        rows = self.table.selectionModel().selectedRows()
        return rows[0].row() if rows else -1

    def _buttons(self) -> None:
        r = self._current()
        has = r >= 0
        self.edit_button.setEnabled(has)
        self.delete_button.setEnabled(has)
        self.up_button.setEnabled(has and r > 0)
        self.down_button.setEnabled(has and r < len(self.rules) - 1)

    def _toggled(self, item) -> None:
        if item.column() == 0 and 0 <= item.row() < len(self.rules):
            self.rules[item.row()]["enabled"] = item.checkState() == Qt.Checked

    def add(self) -> None:
        dialog = CfRuleDialog(self, self.columns, self.dtypes)
        if dialog.exec() == QDialog.Accepted and dialog.result_rule:
            self.rules.append(dialog.result_rule)
            self._fill(len(self.rules) - 1)

    def edit(self) -> None:
        r = self._current()
        if r < 0:
            return
        dialog = CfRuleDialog(self, self.columns, self.dtypes, self.rules[r])
        if dialog.exec() == QDialog.Accepted and dialog.result_rule:
            self.rules[r] = dialog.result_rule
            self._fill(r)

    def delete(self) -> None:
        r = self._current()
        if r >= 0:
            del self.rules[r]
            self._fill(min(r, len(self.rules) - 1))

    def move(self, delta: int) -> None:
        r = self._current()
        t = r + delta
        if r >= 0 and 0 <= t < len(self.rules):
            self.rules[r], self.rules[t] = self.rules[t], self.rules[r]
            self._fill(t)

    def _apply(self) -> None:
        self.result_rules = [dict(r) for r in self.rules]
        self.accept()


class FindReplaceDialog(QWidget):
    """Trova e sostituisci (finestra non modale). Emette segnali; CsvTab esegue le operazioni."""

    findNextRequested = Signal()
    replaceRequested = Signal()
    replaceAllRequested = Signal()
    closed = Signal()

    def __init__(self, parent):
        super().__init__(parent, Qt.Dialog)
        self.setWindowTitle("Trova e sostituisci")
        self.setObjectName("findReplace")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setAutoFillBackground(True)
        self.setMinimumWidth(460)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 14, 16, 12)
        lay.setSpacing(8)

        def row(label: str, edit: QLineEdit) -> QHBoxLayout:
            r = QHBoxLayout()
            lab = QLabel(label)
            lab.setFixedWidth(104)
            r.addWidget(lab)
            r.addWidget(edit, 1)
            return r

        self.find_edit = QLineEdit()
        self.find_edit.setPlaceholderText("Testo da trovare")
        self.find_edit.setClearButtonEnabled(True)
        self.replace_edit = QLineEdit()
        self.replace_edit.setPlaceholderText("Testo sostitutivo (vuoto = cancella il testo trovato)")
        lay.addLayout(row("Trova", self.find_edit))
        lay.addLayout(row("Sostituisci con", self.replace_edit))
        self.case_box = QCheckBox("Maiuscole/minuscole")
        self.exact_box = QCheckBox("Corrispondenza esatta")
        self.exact_box.setToolTip("La cella deve coincidere per intero con il testo cercato. "
                                  "Con «Trova» vuoto cerca le celle vuote.")
        self.filtered_box = QCheckBox("Solo record filtrati")
        self.filtered_box.setToolTip("Limita «Sostituisci tutto» ai record che soddisfano ricerca e filtri correnti "
                                     "(vale senza selezione o con colonne selezionate).")
        opts = QHBoxLayout()
        for b in (self.case_box, self.exact_box, self.filtered_box):
            opts.addWidget(b)
        opts.addStretch(1)
        lay.addLayout(opts)

        self.scope_label = QLabel("")
        self.scope_label.setObjectName("hint")
        self.scope_label.setWordWrap(True)
        lay.addWidget(self.scope_label)
        self.status = QLabel("")
        self.status.setWordWrap(True)
        lay.addWidget(self.status)

        buttons = QHBoxLayout()
        self.next_button = QPushButton("Trova successivo")
        self.next_button.setObjectName("primary")
        self.replace_button = QPushButton("Sostituisci")
        self.replace_all_button = QPushButton("Sostituisci tutto")
        self.close_button = QPushButton("Chiudi")
        for b in (self.next_button, self.replace_button, self.replace_all_button):
            buttons.addWidget(b)
        buttons.addStretch(1)
        buttons.addWidget(self.close_button)
        lay.addLayout(buttons)
        self.next_button.clicked.connect(self.findNextRequested.emit)
        self.replace_button.clicked.connect(self.replaceRequested.emit)
        self.replace_all_button.clicked.connect(self.replaceAllRequested.emit)
        self.close_button.clicked.connect(self.close)
        self.find_edit.returnPressed.connect(self.findNextRequested.emit)
        self.replace_edit.returnPressed.connect(self.replaceRequested.emit)
        self.find_edit.textChanged.connect(lambda _t: self.status.setText(""))

    def set_scope_text(self, text: str) -> None:
        self.scope_label.setText(text)

    def set_status(self, text: str, error: bool = False) -> None:
        self.status.setObjectName("error" if error else "")
        self.status.setStyleSheet("" if not error else "")
        self.status.setText(text)
        self.status.style().unpolish(self.status)
        self.status.style().polish(self.status)

    def set_busy(self, busy: bool) -> None:
        for b in (self.next_button, self.replace_button, self.replace_all_button):
            b.setEnabled(not busy)

    def options(self) -> dict:
        return {"find": self.find_edit.text(), "replace": self.replace_edit.text(), "case": self.case_box.isChecked(),
                "exact": self.exact_box.isChecked(), "filtered": self.filtered_box.isChecked()}

    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key_Escape:
            self.close()
            return
        super().keyPressEvent(event)

    def closeEvent(self, event) -> None:
        self.closed.emit()
        super().closeEvent(event)


class NameDialog(QDialog):
    """Chiede il nome di una colonna (nuova o rinominata): rifiuta nomi vuoti e duplicati con un messaggio chiaro."""

    def __init__(self, parent, title: str, label: str, columns: list[str], initial: str = "",
                 ignore: str | None = None, ok_text: str = "OK", note: str = ""):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setMinimumWidth(400)
        self.columns, self.ignore = columns, ignore
        self.result_name: str | None = None
        lay = QVBoxLayout(self)
        lay.setContentsMargins(18, 16, 18, 14)
        lay.setSpacing(10)
        lay.addWidget(QLabel(label))
        self.edit = QLineEdit(initial)
        self.edit.selectAll()
        lay.addWidget(self.edit)
        if note:
            hint = QLabel(note)
            hint.setObjectName("hint")
            hint.setWordWrap(True)
            lay.addWidget(hint)
        self.error = QLabel("")
        self.error.setObjectName("error")
        self.error.setWordWrap(True)
        self.error.hide()
        lay.addWidget(self.error)
        footer = QHBoxLayout()
        footer.addStretch(1)
        cancel = QPushButton("Annulla")
        cancel.clicked.connect(self.reject)
        self.ok = QPushButton(ok_text)
        self.ok.setObjectName("primary")
        self.ok.setDefault(True)
        self.ok.clicked.connect(self._accept)
        footer.addWidget(cancel)
        footer.addWidget(self.ok)
        lay.addLayout(footer)
        self.edit.textChanged.connect(lambda _t: self.error.hide())
        self.edit.returnPressed.connect(self._accept)

    def _accept(self) -> None:
        name = self.edit.text().strip()
        if not name:
            problem = "Il nome non può essere vuoto."
        else:
            clash = next((c for c in self.columns if c != self.ignore and c.lower() == name.lower()), None)
            problem = f"Esiste già una colonna chiamata «{clash}». Scegli un altro nome." if clash else None
        if problem:
            self.error.setText(problem)
            self.error.show()
            return
        self.result_name = name
        self.accept()


CSV_EXTENSIONS = (".csv", ".tsv", ".txt")
XLSX_EXTENSIONS = (".xlsx",)
FORMAT_LABELS = {"csv": "CSV (.csv)", "xlsx": "Excel moderno (.xlsx)"}


def normalize_extension(path: str, fmt: str) -> str:
    """L'estensione deve corrispondere al formato prodotto: si sostituisce quella di un altro formato, si aggiunge se manca."""
    base, ext = os.path.splitext(path)
    own = CSV_EXTENSIONS if fmt == "csv" else XLSX_EXTENSIONS
    wanted = ".csv" if fmt == "csv" else ".xlsx"
    if ext.lower() in own:
        return path
    if ext.lower() in (".csv", ".tsv", ".txt", ".xlsx", ".xls", ".xlsm"):
        return base + wanted
    return path + wanted


class SaveDialog(QDialog):
    """Scelta di formato, destinazione e opzioni CSV per «Salva» e «Salva con nome».

    ``request`` (dopo l'accettazione): {"format", "path", "delimiter", "encoding", "decimal", "also_current"}."""

    def __init__(self, parent, title: str, *, mode: str, working_path: str, working_format: str,
                 protected_path: str | None, default_format: str, defaults: dict, notes: dict,
                 also_current: str | None = None, ok_text: str = "Salva"):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setMinimumWidth(520)
        self.mode, self.working_path, self.working_format = mode, working_path, working_format
        self.protected_path, self.notes = protected_path, notes
        self.request: dict | None = None
        lay = QVBoxLayout(self)
        lay.setContentsMargins(18, 16, 18, 14)
        lay.setSpacing(10)
        self.intro = QLabel({
            "save": (("Salva le modifiche in una nuova copia: un file Excel originale non viene mai sovrascritto."
                      if protected_path else
                      "Salva le modifiche nel file corrente. Scegli un altro formato per creare invece una copia.")),
            "save_as": ("Scegli cartella, nome, formato (CSV o XLSX) e opzioni CSV. Se il formato è diverso dall'originale "
                        "si crea una nuova copia: il file originale non viene toccato."),
        }.get(mode, ""))
        self.intro.setObjectName("hint")
        self.intro.setWordWrap(True)
        lay.addWidget(self.intro)
        form = QFormLayout()
        form.setHorizontalSpacing(12)
        form.setVerticalSpacing(8)
        self.format_box = QComboBox()
        for key, label in FORMAT_LABELS.items():
            self.format_box.addItem(label, key)
        self.format_box.setCurrentIndex(self.format_box.findData(default_format))
        form.addRow("Formato", self.format_box)
        path_row = QHBoxLayout()
        self.path_edit = QLineEdit()
        self.browse_button = QPushButton("Sfoglia…")
        self.browse_button.clicked.connect(self._browse)
        path_row.addWidget(self.path_edit, 1)
        path_row.addWidget(self.browse_button)
        form.addRow("Destinazione", path_row)
        self.delim_box = QComboBox()
        for key, label in DELIMITERS.items():
            self.delim_box.addItem(label, key)
        self.delim_box.setCurrentIndex(max(0, self.delim_box.findData(defaults.get("delimiter", ","))))
        self.enc_box = QComboBox()
        for key, label in ENCODINGS.items():
            self.enc_box.addItem(label, key)
        self.enc_box.setCurrentIndex(max(0, self.enc_box.findData(defaults.get("encoding", "utf-8"))))
        self.dec_box = QComboBox()
        self.dec_box.addItem("Punto ( 1.5 )", ".")
        self.dec_box.addItem("Virgola ( 1,5 )", ",")
        self.dec_box.setCurrentIndex(max(0, self.dec_box.findData(defaults.get("decimal", "."))))
        self.csv_labels = []
        for label, box in (("Delimitatore", self.delim_box), ("Codifica", self.enc_box), ("Separatore decimale", self.dec_box)):
            form.addRow(label, box)
            self.csv_labels.append((form.labelForField(box), box))
        lay.addLayout(form)
        self.note = QLabel("")
        self.note.setObjectName("hint")
        self.note.setWordWrap(True)
        lay.addWidget(self.note)
        self.also_box = QCheckBox(also_current) if also_current else None
        if self.also_box:
            lay.addWidget(self.also_box)
        self.error = QLabel("")
        self.error.setObjectName("error")
        self.error.setWordWrap(True)
        self.error.hide()
        lay.addWidget(self.error)
        footer = QHBoxLayout()
        footer.addStretch(1)
        cancel = QPushButton("Annulla")
        cancel.clicked.connect(self.reject)
        self.ok = QPushButton(ok_text)
        self.ok.setObjectName("primary")
        self.ok.setDefault(True)
        self.ok.clicked.connect(self._accept)
        footer.addWidget(cancel)
        footer.addWidget(self.ok)
        lay.addLayout(footer)
        self.format_box.currentIndexChanged.connect(self._format_changed)
        self.path_edit.textChanged.connect(lambda _t: self.error.hide())
        self._format_changed()

    # ---- logica dei campi
    def fmt(self) -> str:
        return self.format_box.currentData()

    def in_place(self) -> bool:
        """«Salva» nel formato del file di lavoro: la destinazione è il file stesso (se non è un Excel protetto)."""
        return self.mode == "save" and self.fmt() == self.working_format and not self._is_protected(self.working_path) \
            and os.path.isdir(os.path.dirname(os.path.abspath(self.working_path)))

    def _is_protected(self, path: str) -> bool:
        if not self.protected_path or not path:
            return False
        a, b = os.path.abspath(path), os.path.abspath(self.protected_path)
        if os.path.normcase(a) == os.path.normcase(b):
            return True
        return os.path.exists(a) and os.path.exists(b) and os.path.samefile(a, b)

    def suggest(self, fmt: str) -> str:
        current = self.path_edit.text().strip() or self.working_path
        folder = os.path.dirname(os.path.abspath(current)) if current else os.getcwd()
        stem = os.path.splitext(os.path.basename(self.working_path or "dati"))[0]
        ext = ".csv" if fmt == "csv" else ".xlsx"
        name = stem + ext
        if os.path.normcase(os.path.join(folder, name)) == os.path.normcase(os.path.abspath(self.working_path or "")) \
                or self._is_protected(os.path.join(folder, name)):
            name = stem + "_modificato" + ext
        return os.path.join(folder, name)

    def _format_changed(self) -> None:
        fmt = self.fmt()
        for label, box in self.csv_labels:
            label.setVisible(fmt == "csv")
            box.setVisible(fmt == "csv")
        if self.in_place():
            self.path_edit.setText(self.working_path)
        else:
            self.path_edit.setText(self.suggest(fmt))
        locked = self.in_place()
        self.path_edit.setReadOnly(locked)
        self.browse_button.setVisible(not locked)
        text = self.notes.get(fmt, "")
        if locked:
            text = (f"«{os.path.basename(self.working_path)}» verrà sostituito con le modifiche (scrittura sicura: "
                    "il file precedente resta intatto finché la nuova versione non è completa). " + text)
        self.note.setText(text.strip())
        self.error.hide()

    def _browse(self) -> None:
        fmt = self.fmt()
        flt = "CSV (*.csv *.tsv *.txt)" if fmt == "csv" else "Cartella di lavoro Excel (*.xlsx)"
        path, _ = QFileDialog.getSaveFileName(self, "Destinazione", self.path_edit.text() or self.suggest(fmt), flt,
                                              options=QFileDialog.DontConfirmOverwrite)
        if path:
            self.path_edit.setText(normalize_extension(path, fmt))

    def _accept(self) -> None:
        fmt = self.fmt()
        raw = self.path_edit.text().strip()
        problem = None
        if not raw:
            problem = "Indica il nome del file di destinazione."
        path = normalize_extension(os.path.abspath(os.path.expanduser(raw)), fmt) if raw else ""
        if not problem and not os.path.isdir(os.path.dirname(path)):
            problem = "La cartella di destinazione non esiste."
        if not problem and os.path.isdir(path):
            problem = "La destinazione è una cartella: indica il nome di un file."
        if not problem and self._is_protected(path):
            problem = (f"Il file Excel originale «{os.path.basename(self.protected_path)}» non viene mai sovrascritto: "
                       "scegli un altro nome.")
        if not problem and fmt == "csv" and self.delim_box.currentData() == self.dec_box.currentData():
            problem = "Delimitatore e separatore decimale non possono essere lo stesso carattere."
        if problem:
            self.error.setText(problem)
            self.error.show()
            return
        self.request = {"format": fmt, "path": path, "delimiter": self.delim_box.currentData(),
                        "encoding": self.enc_box.currentData(), "decimal": self.dec_box.currentData(),
                        "also_current": bool(self.also_box and self.also_box.isChecked())}
        self.accept()


class NewCsvDialog(QDialog):
    """Procedura guidata per creare un nuovo CSV: colonne, righe, cartella, nome, codifica e delimitatore.

    Dopo l'accettazione ``spec`` contiene i valori validati (vedi csv_reader.core.newcsv.validate)."""

    def __init__(self, parent=None, folder: str = ""):
        super().__init__(parent)
        from csv_reader.core import newcsv
        self._newcsv = newcsv
        self.setObjectName("newCsvDialog")
        self.setWindowTitle("Nuovo CSV")
        self.setMinimumWidth(520)
        self.spec: dict | None = None
        lay = QVBoxLayout(self)
        lay.setContentsMargins(18, 16, 18, 14)
        lay.setSpacing(10)
        intro = QLabel("Crea un file CSV con intestazioni «Colonna1», «Colonna2», … e celle vuote. "
                       "Si apre subito in una nuova scheda, pronto da compilare.")
        intro.setObjectName("hint")
        intro.setWordWrap(True)
        lay.addWidget(intro)
        form = QFormLayout()
        form.setHorizontalSpacing(12)
        form.setVerticalSpacing(8)
        self.columns_edit = QLineEdit("3")
        self.rows_edit = QLineEdit("10")
        self.rows_edit.setToolTip("0 crea un file con la sola riga di intestazione")
        form.addRow("Numero di colonne", self.columns_edit)
        form.addRow("Righe di dati", self.rows_edit)
        folder_row = QHBoxLayout()
        self.folder_edit = QLineEdit(folder or os.path.expanduser("~"))
        browse = QPushButton("Sfoglia…")
        browse.clicked.connect(self._browse)
        folder_row.addWidget(self.folder_edit, 1)
        folder_row.addWidget(browse)
        form.addRow("Cartella", folder_row)
        self.name_edit = QLineEdit("nuovo.csv")
        self.name_edit.setToolTip("L'estensione .csv viene aggiunta se manca")
        form.addRow("Nome del file", self.name_edit)
        self.enc_box = QComboBox()
        for key, label in ENCODINGS.items():
            self.enc_box.addItem(label, key)
        form.addRow("Codifica", self.enc_box)
        self.delim_box = QComboBox()
        for key, label in DELIMITERS.items():
            self.delim_box.addItem(label, key)
        self.delim_box.setCurrentIndex(self.delim_box.findData(","))
        form.addRow("Delimitatore", self.delim_box)
        lay.addLayout(form)
        self.error = QLabel("")
        self.error.setObjectName("error")
        self.error.setWordWrap(True)
        self.error.hide()
        lay.addWidget(self.error)
        footer = QHBoxLayout()
        footer.addStretch(1)
        cancel = QPushButton("Annulla")
        cancel.clicked.connect(self.reject)
        self.ok = QPushButton("Crea")
        self.ok.setObjectName("primary")
        self.ok.setDefault(True)
        self.ok.clicked.connect(self._accept)
        footer.addWidget(cancel)
        footer.addWidget(self.ok)
        lay.addLayout(footer)
        for edit in (self.columns_edit, self.rows_edit, self.folder_edit, self.name_edit):
            edit.textChanged.connect(lambda _t: self.error.hide())

    def _browse(self):
        folder = QFileDialog.getExistingDirectory(self, "Cartella di destinazione", self.folder_edit.text())
        if folder:
            self.folder_edit.setText(folder)

    def _accept(self):
        from csv_reader.core.engine import ImportProblem
        try:
            spec = self._newcsv.validate(self.columns_edit.text(), self.rows_edit.text(), self.folder_edit.text(),
                                         self.name_edit.text(), self.enc_box.currentData(), self.delim_box.currentData())
        except ImportProblem as exc:
            self.error.setText(str(exc))
            self.error.show()
            return
        self.spec = spec
        self.accept()


class TypesDialog(QDialog):
    """Riconoscimento e assegnazione ESPLICITA del tipo di dato delle colonne.

    Mostra tipo attuale e tipo riconosciuto (calcolato su richiesta, senza modificare nulla) e lascia scegliere a mano
    il tipo di ogni colonna. Dopo «Applica» ``changes`` contiene [(colonna, tipo, cifre decimali)]; la conversione vera
    e propria (annullabile) la esegue CsvTab.

    ``analyze(colonne) -> [dict]`` va eseguita fuori dal thread grafico: la finestra la affida a ``runner(fn, callback)``."""

    COLS = ["Colonna", "Tipo attuale", "Tipo riconosciuto", "Esito del riconoscimento", "Imposta come", "Decimali"]

    def __init__(self, parent, columns: list[str], kinds: dict[str, str], analyze, runner, initial: str | None = None,
                 auto_detect: bool = False):
        super().__init__(parent)
        self.setWindowTitle("Tipi di dati")
        self.resize(980, 520)
        self.columns, self.kinds = list(columns), dict(kinds)
        self._analyze, self._runner = analyze, runner
        self.analysis: dict[str, dict] = {}
        self.changes: list[tuple[str, str, int | None]] = []
        self._closed = False
        lay = QVBoxLayout(self)
        lay.setContentsMargins(18, 16, 18, 14)
        lay.setSpacing(10)
        intro = QLabel("All'apertura tutti i valori sono testo, esattamente come nel file. Qui puoi riconoscere il tipo "
                       "delle colonne e assegnarlo: la conversione è esplicita e si annulla con Annulla (Ctrl+Z). "
                       "I valori non convertibili diventano vuoti (te lo diciamo prima, con degli esempi).")
        intro.setObjectName("hint")
        intro.setWordWrap(True)
        lay.addWidget(intro)
        bar = QHBoxLayout()
        self.detect_button = QPushButton("Riconosci i tipi")
        self.detect_button.setToolTip("Analizza i valori di tutte le colonne (non modifica nulla)")
        self.detect_button.clicked.connect(self.detect)
        self.use_button = QPushButton("Usa i tipi riconosciuti")
        self.use_button.setToolTip("Imposta la scelta sul tipo riconosciuto per le colonne in cui tutti i valori sono validi")
        self.use_button.clicked.connect(self.use_detected)
        self.use_button.setEnabled(False)
        self.state = QLabel("")
        self.state.setObjectName("hint")
        bar.addWidget(self.detect_button)
        bar.addWidget(self.use_button)
        bar.addWidget(self.state, 1)
        lay.addLayout(bar)

        self.table = QTableWidget(len(self.columns), len(self.COLS))
        self.table.setHorizontalHeaderLabels(self.COLS)
        self.table.verticalHeader().hide()
        self.table.setSelectionMode(QAbstractItemView.NoSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setShowGrid(False)
        self.table.setAlternatingRowColors(True)
        hdr = self.table.horizontalHeader()
        hdr.setSectionResizeMode(QHeaderView.Interactive)
        hdr.setStretchLastSection(False)
        hdr.setSectionResizeMode(3, QHeaderView.Stretch)
        for i, w in enumerate((160, 110, 140, 0, 175, 90)):
            if w:
                self.table.setColumnWidth(i, w)
        self.combos: list[QComboBox] = []
        self.spins: list[QSpinBox] = []
        for r, name in enumerate(self.columns):
            item = QTableWidgetItem(name)
            item.setToolTip(name)
            self.table.setItem(r, 0, item)
            self.table.setItem(r, 1, QTableWidgetItem(TYPE_LABELS[self.kinds.get(name, "text")]))
            self.table.setItem(r, 2, QTableWidgetItem("—"))
            self.table.setItem(r, 3, QTableWidgetItem(""))
            combo = QComboBox()
            combo.addItem("Non cambiare", None)
            for key, label, _ in TYPES:
                combo.addItem(label, key)
            combo.currentIndexChanged.connect(lambda _i, row=r: self._sync_row(row))
            self.table.setCellWidget(r, 4, combo)
            spin = QSpinBox()
            spin.setRange(0, 15)
            spin.setValue(2)
            spin.setVisible(False)                 # compare solo scegliendo «Numero decimale»
            spin.setToolTip("Cifre decimali del numero: i valori sono mostrati e salvati con questo numero di decimali "
                            "(non meno di quelli presenti nei dati: nessun valore viene arrotondato)")
            self.table.setCellWidget(r, 5, spin)
            self.combos.append(combo)
            self.spins.append(spin)
        lay.addWidget(self.table, 1)

        footer = QHBoxLayout()
        footer.addStretch(1)
        close = QPushButton("Chiudi")
        close.clicked.connect(self.reject)
        self.apply_button = QPushButton("Applica")
        self.apply_button.setObjectName("primary")
        self.apply_button.clicked.connect(self._apply)
        footer.addWidget(close)
        footer.addWidget(self.apply_button)
        lay.addLayout(footer)
        if initial in self.columns:
            self.table.selectRow(self.columns.index(initial))
            self.table.scrollToItem(self.table.item(self.columns.index(initial), 0))
        self._auto = auto_detect

    def showEvent(self, event):
        super().showEvent(event)
        if self._auto:
            self._auto = False
            self.detect()

    def done(self, code):
        self._closed = True
        super().done(code)

    # ---- riconoscimento
    def detect(self):
        self.detect_button.setEnabled(False)
        self.use_button.setEnabled(False)
        self.state.setText("Analisi dei valori in corso…")
        cols = list(self.columns)
        self._runner(lambda: self._analyze(cols), self._detected, self._detect_failed)

    def _detect_failed(self, _error=None):
        if not self._closed:
            self.detect_button.setEnabled(True)
            self.state.setText("Analisi non riuscita.")

    def _detected(self, results):
        if self._closed:
            return
        self.analysis = {r["column"]: r for r in results}
        for row, name in enumerate(self.columns):
            info = self.analysis.get(name)
            if not info:
                continue
            label = TYPE_LABELS[info["kind"]] + (" (parziale)" if info["partial"] else "")
            self.table.item(row, 2).setText(label)
            reason = info["reason"]
            if info["examples"]:
                reason += " Es.: " + ", ".join(f"«{x}»" for x in info["examples"][:3])
            self.table.item(row, 3).setText(reason)
            self.table.item(row, 3).setToolTip(reason)
            if info["kind"] == "decimal" or info["min_decimals"]:
                self.spins[row].setMinimum(info["min_decimals"])
                self.spins[row].setValue(max(info["min_decimals"], 2))
        self.detect_button.setEnabled(True)
        self.use_button.setEnabled(True)
        done = sum(1 for r in results if r["kind"] != r["current"] and not r["partial"])
        self.state.setText(f"Analisi completata: {done} colonne con un tipo diverso da quello attuale.")

    def use_detected(self):
        for row, name in enumerate(self.columns):
            info = self.analysis.get(name)
            if info and not info["partial"] and info["kind"] != info["current"]:
                self.combos[row].setCurrentIndex(self.combos[row].findData(info["kind"]))

    def _sync_row(self, row: int):
        self.spins[row].setVisible(self.combos[row].currentData() == "decimal")

    # ---- applicazione
    def _apply(self):
        changes = []
        for row, name in enumerate(self.columns):
            kind = self.combos[row].currentData()
            if kind and kind != self.kinds.get(name, "text"):
                changes.append((name, kind, self.spins[row].value() if kind == "decimal" else None))
            elif kind == "decimal" and kind == self.kinds.get(name):
                changes.append((name, kind, self.spins[row].value()))
        if not changes:
            self.state.setText("Nessuna modifica: scegli il tipo di almeno una colonna in «Imposta come».")
            return
        self.changes = changes
        self.accept()
