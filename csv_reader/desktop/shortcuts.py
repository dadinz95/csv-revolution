"""Registro centrale delle scorciatoie da tastiera.

Un'unica tabella descrive tutte le scorciatoie: la usano i menu (testo dei suggerimenti), la tastiera della tabella
(riconoscimento dei tasti) e la finestra «Scorciatoie da tastiera». «Ctrl» è il tasto Controllo su Windows e Linux e il
tasto Comando (⌘) su macOS: Qt lo traduce da solo, quindi il codice usa sempre Qt.ControlModifier."""
from __future__ import annotations

import sys

from PySide6.QtCore import Qt
from PySide6.QtGui import QKeySequence
from PySide6.QtWidgets import QDialog, QHBoxLayout, QLabel, QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout, QHeaderView, QAbstractItemView

IS_MAC = sys.platform == "darwin"

# (gruppo, azione, sequenza portabile (più alternative separate da " / "), descrizione)
SHORTCUTS = [
    ("File", "Salva", "Ctrl+S", "Salva nel file corrente (un Excel originale non viene mai sovrascritto)"),
    ("File", "Salva con nome…", "Ctrl+Shift+S", "Scegli cartella, nome e formato (CSV o XLSX)"),
    ("File", "File precedente / successivo", "Ctrl+PgUp / Ctrl+PgDown", "Passa da una scheda all'altra"),
    ("Modifica", "Annulla", "Ctrl+Z", "Annulla l'ultima modifica (celle, sostituzioni, incolla, colonne, righe). "
                                     "Nei campi di testo e nell'editor SQL vale l'annulla del campo"),
    ("Modifica", "Copia", "Ctrl+C", "Copia le celle selezionate come testo separato da tabulazioni (compatibile con Excel)"),
    ("Modifica", "Incolla", "Ctrl+V", "Incolla da Excel o da altro testo, a partire dalla cella attiva"),
    ("Modifica", "Trova e sostituisci", "Ctrl+F / Ctrl+H", "Apre la finestra di ricerca"),
    ("Modifica", "Trova successivo", "F3", "Va alla prossima occorrenza"),
    ("Modifica", "Conferma la cella e vai sotto", "Enter", "Con una cella in modifica: salva il valore e passa alla cella sotto (Esc annulla)"),
    ("Navigazione", "Estendi la selezione di una cella", "Shift+Freccia", "Come in Excel"),
    ("Navigazione", "Vai al bordo dei dati", "Ctrl+Freccia", "Salta all'ultima cella piena del blocco o alla prossima cella piena; "
                                                            "cambia pagina da solo"),
    ("Navigazione", "Estendi la selezione fino al bordo", "Ctrl+Shift+Freccia", "Come sopra, estendendo la selezione "
                                                                               "(limitata alla pagina visibile)"),
    ("Navigazione", "Prima / ultima cella", "Ctrl+Home / Ctrl+End", "Prima riga e prima colonna / ultima riga e ultima colonna"),
    ("Navigazione", "Seleziona tutto", "Ctrl+A", "Seleziona le celle della pagina"),
    ("Navigazione", "Dettaglio record", "Tab", "Con una cella selezionata nella tabella (non in modifica): apre il dettaglio dei record selezionati "
                                            "(fino a 200). Tab non sposta quindi alla cella a destra: usa le frecce"),
    ("Aiuto", "Scorciatoie da tastiera", "F1", "Apre questo elenco"),
]


def native(sequence: str) -> str:
    """Testo della scorciatoia come lo scrive il sistema (⌘ su macOS, Ctrl altrove)."""
    parts = []
    for alt in sequence.split(" / "):
        if alt.endswith("Freccia"):
            base = alt[: -len("Freccia")]
            seq = QKeySequence(base + "Up").toString(QKeySequence.NativeText)[: -len("Up")] if base else ""
            parts.append(seq + "Frecce" if not IS_MAC else seq + "←↑→↓")
        else:
            text = QKeySequence(alt.replace("PgUp", "PgUp")).toString(QKeySequence.NativeText)
            parts.append(text or alt)
    return " / ".join(parts)


def hint(name: str) -> str:
    for _g, action, seq, _d in SHORTCUTS:
        if action == name:
            return native(seq)
    return ""


NAV_KEYS = {Qt.Key_Up: (-1, 0), Qt.Key_Down: (1, 0), Qt.Key_Left: (0, -1), Qt.Key_Right: (0, 1)}


def table_command(event) -> tuple | None:
    """Riconosce i comandi da tastiera della tabella dati. Ritorna ("paste",) / ("jump", dr, dc, extend) / ("corner", end, extend)."""
    mods = event.modifiers() & ~Qt.KeypadModifier
    ctrl = bool(mods & Qt.ControlModifier)
    shift = bool(mods & Qt.ShiftModifier)
    if mods & (Qt.AltModifier | Qt.MetaModifier) and not IS_MAC:
        return None
    if event.matches(QKeySequence.Paste):
        return ("paste",)
    if ctrl and event.key() in NAV_KEYS and mods in (Qt.ControlModifier, Qt.ControlModifier | Qt.ShiftModifier):
        dr, dc = NAV_KEYS[event.key()]
        return ("jump", dr, dc, shift)
    if ctrl and event.key() in (Qt.Key_Home, Qt.Key_End) and mods in (Qt.ControlModifier, Qt.ControlModifier | Qt.ShiftModifier):
        return ("corner", event.key() == Qt.Key_End, shift)
    return None


class ShortcutsDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("shortcutsDialog")
        self.setWindowTitle("Scorciatoie da tastiera")
        self.resize(760, 520)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(18, 16, 18, 14)
        note = QLabel("Su macOS il tasto Ctrl dell'elenco corrisponde al tasto Comando (⌘). "
                      "Le scorciatoie non agiscono mentre si scrive in un campo di testo o nell'editor SQL: lì valgono quelle del campo.")
        note.setObjectName("hint")
        note.setWordWrap(True)
        lay.addWidget(note)
        self.table = QTableWidget(len(SHORTCUTS), 3)
        self.table.setHorizontalHeaderLabels(["Azione", "Scorciatoia", "Descrizione"])
        self.table.verticalHeader().hide()
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionMode(QAbstractItemView.NoSelection)
        self.table.setWordWrap(True)
        for r, (group, action, seq, desc) in enumerate(SHORTCUTS):
            for c, text in enumerate((f"{action}", native(seq), desc)):
                item = QTableWidgetItem(text)
                item.setToolTip(group)
                self.table.setItem(r, c, item)
        h = self.table.horizontalHeader()
        h.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        h.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        h.setSectionResizeMode(2, QHeaderView.Stretch)
        self.table.resizeRowsToContents()
        lay.addWidget(self.table, 1)
        row = QHBoxLayout()
        row.addStretch(1)
        close = QPushButton("Chiudi")
        close.clicked.connect(self.accept)
        row.addWidget(close)
        lay.addLayout(row)
