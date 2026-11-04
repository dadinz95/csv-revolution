from __future__ import annotations

import os
import re
import sys
import logging
import logging.handlers
import tempfile

from PySide6.QtCore import QElapsedTimer, QEvent, QItemSelection, QItemSelectionModel, QPoint, QPointF, QRect, QThread, QTimer, Signal, Qt
from PySide6.QtGui import QBrush, QColor, QFontMetrics, QGuiApplication, QKeySequence, QPainter, QPen, QPolygonF, QShortcut
from PySide6.QtWidgets import QToolTip
from csv_reader.core.clipboard import format_tsv, parse_tsv
from csv_reader.core.newcsv import create_csv
from csv_reader.desktop import shortcuts as SC
from PySide6.QtWidgets import (
    QAbstractItemDelegate, QAbstractItemView, QApplication, QCheckBox, QComboBox, QDialog, QFormLayout, QFrame,
    QHBoxLayout, QHeaderView, QLabel, QLineEdit,
    QListWidget, QListWidgetItem, QMainWindow, QMenu, QMessageBox, QFileDialog,
    QPlainTextEdit, QProgressBar, QPushButton, QSpinBox, QSplitter, QStackedWidget,
    QTabBar, QTableWidget, QTableWidgetItem, QTabWidget, QToolButton, QVBoxLayout,
    QAbstractSpinBox, QSizePolicy, QTextEdit, QWidget, QWidgetAction,
)

from csv_reader.desktop.tools_dialogs import (
    BackgroundDelegate, CfDialog, FindReplaceDialog, NameDialog, NewCsvDialog, SaveDialog, TypesDialog,
)
from csv_reader.core import coltypes

from csv_reader.core.conditions import (
    CONDITIONS, CONDITION_LABELS, NO_VALUE_OPS, build_conditions, build_where, describe_condition, describe_filter, filter_count,
    inline_params, normalize_spec, spec_is_active, validate_condition,
)
from csv_reader.core.engine import (
    ENCODINGS, CsvEngine, GlobalEngine, ImportOptions, ImportProblem, Scope, validate_options,
)
from csv_reader.core.excel import ExcelProblem, excel_kind, list_sheets
from csv_reader.desktop.single_instance import SingleInstance
from csv_reader.desktop.theme import LIGHT, apply_theme, install_translations
from csv_reader.desktop.icon import app_icon

RESULT_LIMIT = 5000
COLORS: dict = dict(LIGHT)   # colori del tema corrente (usati dai pulsanti disegnati a mano)


def set_colors(colors: dict) -> None:
    COLORS.clear()
    COLORS.update(colors)

EMPTY_LABEL = "(vuoto)"


# --------------------------------------------------------------------------- utilità

def sql_string(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def sql_ident(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def fmt_int(n: int) -> str:
    return f"{n:,}".replace(",", ".")


LOG = logging.getLogger("csv_revolution")


def log_file_path() -> str:
    return os.path.join(tempfile.gettempdir(), "csv_revolution.log")


def setup_logging() -> str:
    """Log tecnico (traceback compresi) su file, separato da ciò che vede l'utente."""
    path = log_file_path()
    if not any(isinstance(h, logging.handlers.RotatingFileHandler) for h in LOG.handlers):
        try:
            handler = logging.handlers.RotatingFileHandler(path, maxBytes=1_000_000, backupCount=2, encoding="utf-8")
            handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
            LOG.addHandler(handler)
            LOG.setLevel(logging.DEBUG)
        except OSError:
            pass
    return path


class Worker(QThread):
    finished_data = Signal(object)
    failed = Signal(str)

    def __init__(self, fn):
        super().__init__()
        self.fn = fn
        self.exc = None

    def run(self):
        try:
            self.finished_data.emit(self.fn())
        except Exception as exc:
            self.exc = exc   # permette a chi ascolta di distinguere errori comprensibili da errori imprevisti
            # all'utente arriva solo il messaggio dell'eccezione (per DuckDB: errore, suggerimenti e posizione);
            # il traceback va nel log tecnico, mai nella finestra
            LOG.error("Operazione in thread non riuscita: %s", exc, exc_info=True)
            self.failed.emit(str(exc).strip() or type(exc).__name__)


class AsyncMixin:
    """Esegue le operazioni lente in un thread, con barra di avanzamento."""

    def _async_init(self):
        self.worker = None
        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        # lo spazio resta riservato anche quando è nascosta: comparendo/scomparendo non deve spostare il resto dell'interfaccia
        policy = self.progress.sizePolicy()
        policy.setRetainSizeWhenHidden(True)
        self.progress.setSizePolicy(policy)
        self.progress.hide()

    def is_busy(self) -> bool:
        return bool(self.worker and self.worker.isRunning())

    def run_worker(self, fn, callback):
        if self.worker and self.worker.isRunning():
            self.worker.wait(100)      # il thread precedente ha già consegnato il risultato: sta solo terminando
        if self.is_busy():
            QMessageBox.information(self, "Operazione in corso", "Attendi il completamento dell'operazione corrente.")
            return
        self.progress.show()
        self.worker = Worker(fn)
        self.worker.finished_data.connect(lambda result: self._worker_done(result, callback))
        self.worker.failed.connect(self._worker_failed)
        self.worker.finished.connect(self._worker_finished)
        self.worker.start()

    def _worker_done(self, result, callback):
        self.progress.hide()
        callback(result)

    def _worker_failed(self, error):
        self.progress.hide()
        QMessageBox.critical(self, "Errore", error)

    def _worker_finished(self):
        """Il thread è terminato: ci si può aggiungere altro lavoro (CsvTab esegue le operazioni in coda)."""


def _export(engine, req: dict) -> dict:
    if req["format"] == "csv":
        return engine.save_csv(req["path"], req["delimiter"], req["encoding"], req["decimal"])
    return engine.save_xlsx(req["path"])


def _problem_text(exc: Exception) -> str:
    if isinstance(exc, (ImportProblem, ExcelProblem)):
        return getattr(exc, "user_message", None) or str(exc)
    return f"Errore imprevisto: {exc}"


def ask_unsaved(parent, title: str, text: str) -> str:
    """Avviso di modifiche non salvate: ritorna «save», «discard» o «cancel»."""
    box = QMessageBox(parent)
    box.setIcon(QMessageBox.Warning)
    box.setWindowTitle(title)
    box.setText(text)
    save = box.addButton("Salva", QMessageBox.AcceptRole)
    discard = box.addButton("Non salvare", QMessageBox.DestructiveRole)
    cancel = box.addButton("Annulla", QMessageBox.RejectRole)
    box.setDefaultButton(save)
    box.setEscapeButton(cancel)
    box.exec()
    clicked = box.clickedButton()
    return "save" if clicked is save else "discard" if clicked is discard else "cancel"


class ElidedLabel(QLabel):
    """Etichetta che, se non c'è spazio, accorcia il testo al centro con «…» e mostra il testo intero nel suggerimento.
    ``text()`` restituisce sempre il testo completo."""

    def setText(self, text):
        super().setText(text)
        self._sync_tooltip()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._sync_tooltip()

    def _sync_tooltip(self):
        fits = self.fontMetrics().horizontalAdvance(self.text()) <= self.contentsRect().width()
        self.setToolTip("" if fits else self.text())

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setPen(self.palette().color(self.foregroundRole()))
        rect = self.contentsRect()
        painter.drawText(rect, int(self.alignment()), self.fontMetrics().elidedText(self.text(), Qt.ElideMiddle, rect.width()))


def confirm(parent, title: str, text: str, informative: str = "", yes: str = "Sì", no: str = "Annulla",
            icon=QMessageBox.Warning) -> bool:
    """Conferma con pulsanti dal testo esplicito («Elimina colonna», «Annulla»…); «No» è il pulsante predefinito."""
    box = QMessageBox(parent)
    box.setIcon(icon)
    box.setWindowTitle(title)
    box.setText(text)
    if informative:
        box.setInformativeText(informative)
    box.setStandardButtons(QMessageBox.Yes | QMessageBox.No)
    box.button(QMessageBox.Yes).setText(yes)
    box.button(QMessageBox.No).setText(no)
    box.setDefaultButton(QMessageBox.No)
    return box.exec() == QMessageBox.Yes


# --------------------------------------------------------------------------- widget di base

class LogoLabel(QLabel):
    """Scritta «CSV Revolution» in alto a sinistra. Si comporta come una normale etichetta; in più conta i clic sulla
    prima «o» di «Revolution» e al settimo emette ``secretActivated`` (i giochi nascosti stanno in ``easter_eggs/``)."""

    secretActivated = Signal()
    CLICKS = 7
    MAX_GAP_MS = 2500           # tra un clic e il successivo: oltre questo tempo il conteggio riparte
    LETTER_INDEX = 7            # «CSV Revolution»: C0 S1 V2 ␣3 R4 e5 v6 o7

    def __init__(self, text: str):
        super().__init__(text)
        self._clicks = 0
        self._last = QElapsedTimer()

    def letter_rect(self) -> QRect:
        """Rettangolo (in coordinate dell'etichetta) occupato dal corpo della lettera «o»."""
        self.ensurePolished()
        fm = self.fontMetrics()
        text = self.text()
        i = self.LETTER_INDEX
        left = self.contentsRect().left() + self.margin() + max(self.indent(), 0)
        x0 = left + fm.horizontalAdvance(text[:i])
        x1 = left + fm.horizontalAdvance(text[:i + 1])
        top = self.contentsRect().top() + (self.contentsRect().height() - fm.height()) // 2
        y0 = top + fm.ascent() - fm.xHeight()
        return QRect(x0, y0, x1 - x0, fm.xHeight() + 1)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton and self.letter_rect().contains(event.position().toPoint()):
            if self._clicks and self._last.isValid() and self._last.elapsed() > self.MAX_GAP_MS:
                self._clicks = 0
            self._clicks += 1
            self._last.start()
            if self._clicks >= self.CLICKS:
                self._clicks = 0                       # il contatore riparte da zero dopo l'attivazione
                self.secretActivated.emit()
        super().mousePressEvent(event)


class GroupResizeHeader(QHeaderView):
    """Intestazione che segnala i ridimensionamenti fatti dall'utente trascinando un bordo.

    Distingue il trascinamento manuale dai ridimensionamenti automatici (adattamento al contenuto,
    caricamento di una pagina) controllando che il pulsante del mouse sia premuto."""

    userResized = Signal(int, int)  # indice logico, nuova dimensione
    funnelClicked = Signal(int)     # clic sull'imbuto di una colonna

    FUNNEL_W = 22        # larghezza dell'area cliccabile dell'imbuto
    FUNNEL_GAP = 6       # distanza dal bordo destro: lascia libera la zona di ridimensionamento
    FUNNEL_MIN_SECTION = 56

    def __init__(self, orientation, parent=None):
        super().__init__(orientation, parent)
        self._mouse_down = False
        self._funnel_press = -1
        self.funnel_enabled = False            # solo l'intestazione colonne della tabella principale
        self.active_columns: set[int] = set()  # colonne con filtro o ordine attivo (imbuto pieno)
        self.sectionResized.connect(self._on_resized)

    # ---- imbuto
    def _funnel_rect(self, logical: int) -> QRect:
        x, w = self.sectionViewportPosition(logical), self.sectionSize(logical)
        return QRect(x + w - self.FUNNEL_W - self.FUNNEL_GAP, 0, self.FUNNEL_W, self.height())

    def _funnel_at(self, pos) -> int:
        if not self.funnel_enabled or self.orientation() != Qt.Horizontal:
            return -1
        logical = self.logicalIndexAt(pos)
        if logical < 0 or self.sectionSize(logical) < self.FUNNEL_MIN_SECTION:
            return -1
        return logical if self._funnel_rect(logical).contains(pos) else -1

    def paintSection(self, painter, rect, logical):
        super().paintSection(painter, rect, logical)
        if not (self.funnel_enabled and self.orientation() == Qt.Horizontal) or rect.width() < self.FUNNEL_MIN_SECTION:
            return
        active = logical in self.active_columns
        cx = rect.right() - self.FUNNEL_GAP - self.FUNNEL_W / 2
        cy = rect.center().y()
        c = QColor(COLORS["accent"] if active else COLORS["muted"])
        painter.save()
        painter.setRenderHint(QPainter.Antialiasing)
        shape = QPolygonF([QPointF(cx - 6, cy - 5), QPointF(cx + 6, cy - 5), QPointF(cx + 1.2, cy + 0.8),
                           QPointF(cx + 1.2, cy + 5.5), QPointF(cx - 1.2, cy + 4), QPointF(cx - 1.2, cy + 0.8)])
        painter.setPen(QPen(c, 1.3, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        painter.setBrush(QBrush(c) if active else Qt.NoBrush)
        painter.drawPolygon(shape)
        painter.restore()

    def viewportEvent(self, event):
        if event.type() == QEvent.ToolTip and self._funnel_at(event.pos()) >= 0:
            QToolTip.showText(event.globalPos(), "Filtra e ordina questa colonna", self)
            return True
        return super().viewportEvent(event)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton and self._funnel_at(event.position().toPoint()) >= 0:
            self._funnel_press = self._funnel_at(event.position().toPoint())
            event.accept()                       # l'imbuto non seleziona la colonna
            return
        self._funnel_press = -1
        self._mouse_down = True
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._funnel_press >= 0:
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if self._funnel_press >= 0:
            pressed, self._funnel_press = self._funnel_press, -1
            if self._funnel_at(event.position().toPoint()) == pressed:
                self.funnelClicked.emit(pressed)
            return
        super().mouseReleaseEvent(event)
        self._mouse_down = False

    def _on_resized(self, logical, old, new):
        if self._mouse_down:
            self.userResized.emit(logical, new)


class CopyTable(QTableWidget):
    """Tabella con copia (Ctrl+C), ridimensionamento di gruppo e adattamento al contenuto.

    * Trascinando il bordo di una colonna (riga) inclusa in una selezione di più colonne (righe),
      tutte le colonne (righe) selezionate prendono la stessa larghezza (altezza).
    * Doppio clic sul bordo: ogni colonna selezionata si adatta al proprio contenuto; per le righe
      vale lo stesso. L'adattamento delle colonne misura al massimo FIT_SAMPLE righe distribuite
      uniformemente nella pagina, non l'intero dataset."""

    FIT_SAMPLE = 2000
    FIT_MIN, FIT_MAX = 60, 900

    def __init__(self, *args):
        super().__init__(*args)
        self._group_busy = False
        self.setItemDelegate(BackgroundDelegate(self))
        self.sel_kind: str | None = None      # "col"/"row" se la selezione parte da un'intestazione, altrimenti None
        h = GroupResizeHeader(Qt.Horizontal, self)
        v = GroupResizeHeader(Qt.Vertical, self)
        self.setHorizontalHeader(h)
        self.setVerticalHeader(v)
        # un QHeaderView creato a mano nasce NON cliccabile (quello predefinito della tabella sì):
        # senza questo non si apre il filtro/ordine e non si selezionano colonne e righe dall'intestazione
        h.setSectionsClickable(True)
        v.setSectionsClickable(True)
        h.sectionPressed.connect(lambda _i: setattr(self, "sel_kind", "col"))
        v.sectionPressed.connect(lambda _i: setattr(self, "sel_kind", "row"))
        h.userResized.connect(lambda i, s: self._group_resize(True, i, s))
        v.userResized.connect(lambda i, s: self._group_resize(False, i, s))
        # collegati dopo quelli interni di Qt: il nostro adattamento è l'ultimo ad essere applicato
        h.sectionHandleDoubleClicked.connect(self._autofit_columns)
        v.sectionHandleDoubleClicked.connect(self._autofit_rows)

    # ---- selezione per colonne/righe (calcolata sugli intervalli, senza enumerare le celle)
    def selected_columns(self) -> list[int]:
        cols: set[int] = set()
        for rng in self.selectionModel().selection():
            cols.update(range(rng.left(), rng.right() + 1))
        return sorted(c for c in cols if not self.isColumnHidden(c))

    def selected_rows(self) -> list[int]:
        rows: set[int] = set()
        for rng in self.selectionModel().selection():
            rows.update(range(rng.top(), rng.bottom() + 1))
        return sorted(r for r in rows if not self.isRowHidden(r))

    def _group_resize(self, horizontal: bool, index: int, size: int):
        if self._group_busy:
            return
        targets = self.selected_columns() if horizontal else self.selected_rows()
        if len(targets) < 2 or index not in targets:
            return
        self._group_busy = True
        try:
            self.setUpdatesEnabled(False)
            for i in targets:
                if i != index:
                    (self.setColumnWidth if horizontal else self.setRowHeight)(i, size)
        finally:
            self.setUpdatesEnabled(True)
            self._group_busy = False

    def fit_width_of(self, column: int, maximum: int | None = None, header_extra: int = 28) -> int:
        header_item = self.horizontalHeaderItem(column)
        header_font = self.horizontalHeader().font()
        header_font.setBold(True)
        width = QFontMetrics(header_font).horizontalAdvance(header_item.text() if header_item else "") + header_extra
        fm = self.fontMetrics()
        rows = self.rowCount()
        step = max(1, rows // self.FIT_SAMPLE)
        for r in range(0, rows, step):
            item = self.item(r, column)
            if item is not None:
                width = max(width, fm.horizontalAdvance(item.text()[:200]) + 24)
        return max(self.FIT_MIN, min(maximum or self.FIT_MAX, width))

    def fit_all_columns(self, maximum: int, header_extra: int = 28):
        """Adatta ogni colonna visibile a intestazione e valori, con un massimo (comando «Adatta larghezza al contenuto»)."""
        for c in range(self.columnCount()):
            if not self.isColumnHidden(c):
                self.setColumnWidth(c, self.fit_width_of(c, maximum, header_extra))

    def _autofit_columns(self, index: int):
        selected = self.selected_columns()
        targets = selected if (len(selected) >= 2 and index in selected) else [index]
        for c in targets:
            self.setColumnWidth(c, self.fit_width_of(c))

    def _autofit_rows(self, index: int):
        selected = self.selected_rows()
        targets = selected if (len(selected) >= 2 and index in selected) else [index]
        for r in targets:
            self.resizeRowToContents(r)

    # ---- editor aperto: conferma il valore prima di chiudere una finestra
    def commit_open_editor(self):
        widget = QApplication.focusWidget()
        if widget is not None and widget is not self and self.isAncestorOf(widget):
            self.commitData(widget)
            self.closeEditor(widget, QAbstractItemDelegate.NoHint)

    # ---- copia
    def copy_selection(self):
        indexes = self.selectedIndexes()
        if not indexes:
            return
        selected = {(i.row(), i.column()) for i in indexes}
        rows = [r for r in sorted({r for r, _ in selected}) if not self.isRowHidden(r)]
        cols = [c for c in sorted({c for _, c in selected}) if not self.isColumnHidden(c)]
        grid = []
        for r in rows:
            grid.append([(self.item(r, c).text() if self.item(r, c) and (r, c) in selected else "") for c in cols])
        QGuiApplication.clipboard().setText(format_tsv(grid))

    def mousePressEvent(self, event):
        self.sel_kind = None                    # selezione fatta sulle celle, non sulle intestazioni
        super().mousePressEvent(event)

    def selectAll(self):
        self.sel_kind = "col"                   # Ctrl+A = tutte le colonne
        super().selectAll()

    def keyPressEvent(self, event):
        if event.matches(QKeySequence.Copy):
            self.copy_selection()
            event.accept()
            return
        if event.key() in (Qt.Key_Up, Qt.Key_Down, Qt.Key_Left, Qt.Key_Right, Qt.Key_PageUp, Qt.Key_PageDown,
                           Qt.Key_Home, Qt.Key_End) and not event.matches(QKeySequence.SelectAll):
            self.sel_kind = None
        super().keyPressEvent(event)


class DataTableWidget(CopyTable):
    tabPressed = Signal()
    commandRequested = Signal(object)          # incolla / salti con Ctrl+Frecce (vedi shortcuts.table_command)
    enterCommitted = Signal(int, int)          # Invio dentro una cella: valore confermato (riga, colonna)

    def closeEditor(self, editor, hint):
        # Invio conferma il valore (lo salva la modifica della cella) e porta alla cella sotto, come in Excel;
        # Esc annulla e Tab/Maiusc+Tab mantengono il comportamento di Qt
        cur = self.currentIndex()
        super().closeEditor(editor, hint)
        if hint == QAbstractItemDelegate.SubmitModelCache and cur.isValid():
            self.enterCommitted.emit(cur.row(), cur.column())

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Tab:
            self.tabPressed.emit()
            event.accept()
            return
        command = SC.table_command(event)
        if command is not None:
            self.commandRequested.emit(command)
            event.accept()
            return
        super().keyPressEvent(event)


def style_table(table: QTableWidget, row_height: int = 30):
    table.setAlternatingRowColors(True)
    table.setShowGrid(True)             # linee di delimitazione di righe e colonne (colore: gridline-color del tema)
    table.setHorizontalScrollMode(QAbstractItemView.ScrollPerPixel)
    table.setVerticalScrollMode(QAbstractItemView.ScrollPerPixel)
    table.horizontalScrollBar().setSingleStep(24)
    table.verticalScrollBar().setSingleStep(row_height)
    table.verticalHeader().setDefaultSectionSize(row_height)
    table.horizontalHeader().setHighlightSections(False)
    table.horizontalHeader().setDefaultAlignment(Qt.AlignLeft | Qt.AlignVCenter)


def fit_columns(table: QTableWidget, minimum: int = 80, maximum: int = 380):
    table.resizeColumnsToContents()
    for c in range(table.columnCount()):
        table.setColumnWidth(c, max(minimum, min(maximum, table.columnWidth(c))))


class TabCloseButton(QToolButton):
    """Pulsante di chiusura scheda: croce disegnata, cerchio al passaggio del mouse."""

    def __init__(self, color: str, hover_bg: str, hover_fg: str):
        super().__init__()
        self.setObjectName("tabClose")
        self.setFixedSize(18, 18)
        self.setCursor(Qt.PointingHandCursor)
        self.setToolTip("Chiudi")
        self._c, self._bg, self._fg = QColor(color), QColor(hover_bg), QColor(hover_fg)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        hovered = self.underMouse()
        if hovered:
            p.setPen(Qt.NoPen)
            p.setBrush(self._bg)
            p.drawEllipse(self.rect())
        pen = QPen(self._fg if hovered else self._c)
        pen.setWidthF(1.5)
        pen.setCapStyle(Qt.RoundCap)
        p.setPen(pen)
        m = 6
        p.drawLine(m, m, self.width() - m, self.height() - m)
        p.drawLine(self.width() - m, m, m, self.height() - m)


class ColumnsButton(QToolButton):
    """Menu a tendina per mostrare/nascondere colonne (solo visualizzazione)."""

    changed = Signal()
    fit_requested = Signal()          # «Adatta larghezza al contenuto»
    reset_widths_requested = Signal()  # «Larghezza standard»

    def __init__(self, label: str = "Colonne", width_actions: bool = False):
        super().__init__()
        self.base_label = label
        self.setText(label)
        self.setPopupMode(QToolButton.InstantPopup)
        self.setCursor(Qt.PointingHandCursor)
        self.columns: list[str] = []
        self.hidden: set[str] = set()

        self.menu_ = QMenu(self)
        panel = QWidget()
        panel.setObjectName("menuPanel")
        box = QVBoxLayout(panel)
        box.setContentsMargins(4, 4, 4, 4)
        box.setSpacing(6)
        row = QHBoxLayout()
        show_all = QPushButton("Mostra")
        hide_all = QPushButton("Nascondi")
        show_all.setToolTip("Mostra tutte le colonne (o quelle trovate dalla ricerca)")
        hide_all.setToolTip("Nascondi tutte le colonne (o quelle trovate dalla ricerca)")
        show_all.clicked.connect(lambda: self.set_all(True))
        hide_all.clicked.connect(lambda: self.set_all(False))
        row.addWidget(show_all)
        row.addWidget(hide_all)
        box.addLayout(row)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Cerca colonna…")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self._filter_list)
        box.addWidget(self.search)
        self.list = QListWidget()
        self.list.setMinimumWidth(260)
        self.list.setMinimumHeight(120)
        self.list.setMaximumHeight(320)
        self.list.itemChanged.connect(self._toggled)
        box.addWidget(self.list)
        if width_actions:
            # larghezza delle colonne: solo qui, nel menu «Colonne» (nessun pulsante in più nella barra)
            line = QFrame()
            line.setFrameShape(QFrame.HLine)
            line.setObjectName("menuSeparator")
            box.addWidget(line)
            self.fit_button = QPushButton("Adatta larghezza al contenuto")
            self.fit_button.setToolTip("Adatta ogni colonna a intestazione e valori della pagina (con un massimo). "
                                       "Si può ripetere in qualsiasi momento, anche dopo ridimensionamenti manuali")
            self.fit_button.clicked.connect(lambda: (self.menu_.close(), self.fit_requested.emit()))
            self.reset_button = QPushButton("Larghezza standard")
            self.reset_button.setToolTip("Riporta tutte le colonne alla stessa larghezza iniziale")
            self.reset_button.clicked.connect(lambda: (self.menu_.close(), self.reset_widths_requested.emit()))
            box.addWidget(self.fit_button)
            box.addWidget(self.reset_button)
        action = QWidgetAction(self.menu_)
        action.setDefaultWidget(panel)
        self.menu_.addAction(action)
        self.menu_.aboutToShow.connect(self._on_show)
        self.setMenu(self.menu_)

    def _on_show(self):
        self.search.clear()
        self.search.setFocus()

    def _filter_list(self, text: str):
        needle = text.strip().lower()
        for i in range(self.list.count()):
            self.list.item(i).setHidden(bool(needle) and needle not in self.list.item(i).text().lower())

    def set_columns(self, columns, hidden: set):
        self.columns = list(columns)
        self.hidden = hidden  # insieme condiviso con chi lo possiede
        self.list.blockSignals(True)
        self.list.clear()
        for name in self.columns:
            item = QListWidgetItem(name)
            item.setFlags(Qt.ItemIsUserCheckable | Qt.ItemIsEnabled)
            item.setCheckState(Qt.Unchecked if name in hidden else Qt.Checked)
            self.list.addItem(item)
        self.list.blockSignals(False)
        self._refresh_label()

    def _refresh_label(self):
        if self.hidden:
            visible = len([c for c in self.columns if c not in self.hidden])
            self.setText(f"{self.base_label} ({visible}/{len(self.columns)})")
        else:
            self.setText(self.base_label)

    def _toggled(self, item):
        if item.checkState() == Qt.Checked:
            self.hidden.discard(item.text())
        else:
            self.hidden.add(item.text())
        self._refresh_label()
        self.changed.emit()

    def set_all(self, visible: bool):
        """Mostra/nasconde tutte le colonne; con una ricerca attiva, solo quelle trovate."""
        self.list.blockSignals(True)
        for i in range(self.list.count()):
            item = self.list.item(i)
            if item.isHidden():
                continue
            item.setCheckState(Qt.Checked if visible else Qt.Unchecked)
            if visible:
                self.hidden.discard(item.text())
            else:
                self.hidden.add(item.text())
        self.list.blockSignals(False)
        self._refresh_label()
        self.changed.emit()


class IconButton(QToolButton):
    """Pulsante con una freccia (chevron) disegnata: left, right, up, down."""

    def __init__(self, kind: str, tooltip: str = "", size: int = 28):
        super().__init__()
        self.kind = kind
        self.setObjectName("iconButton")
        self.setFixedSize(size, size)
        self.setCursor(Qt.PointingHandCursor)
        self.setToolTip(tooltip)
        self.setAutoRaise(True)

    def set_kind(self, kind: str):
        self.kind = kind
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        enabled = self.isEnabled()
        if enabled and (self.underMouse() or self.isDown()):
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(COLORS["accent_soft"]))
            p.drawRoundedRect(self.rect().adjusted(1, 1, -1, -1), 7, 7)
        color = QColor(COLORS["text"] if enabled else COLORS["border"])
        if enabled and not self.underMouse():
            color = QColor(COLORS["muted"])
        pen = QPen(color)
        pen.setWidthF(1.8)
        pen.setCapStyle(Qt.RoundCap)
        pen.setJoinStyle(Qt.RoundJoin)
        p.setPen(pen)
        p.translate(self.width() / 2, self.height() / 2)
        p.rotate({"left": 0, "up": 90, "right": 180, "down": 270}[self.kind])
        p.drawPolyline([QPointF(2.5, -5), QPointF(-2.5, 0), QPointF(2.5, 5)])


# --------------------------------------------------------------------------- importazione

DELIMITER_CHOICES = [
    (None, "Automatico"),
    (";", "Punto e virgola ( ; )"),
    (",", "Virgola ( , )"),
    ("\t", "Tabulazione"),
    ("|", "Pipe ( | )"),
    ("custom", "Personalizzato…"),
]
ENCODING_CHOICES = [(None, "Automatica")] + [(k, v) for k, v in ENCODINGS.items()]
HEADER_CHOICES = [(None, "Rilevamento automatico"), (True, "Prima riga come intestazione"), (False, "Prima riga come dato")]
DECIMAL_CHOICES = [(None, "Automatico"), (",", "Virgola ( , )"), (".", "Punto ( . )")]


def _combo(choices, current):
    box = QComboBox()
    for value, label in choices:
        box.addItem(label, value)
    index = next((i for i, (v, _) in enumerate(choices) if v == current), 0)
    box.setCurrentIndex(index)
    return box


def describe_detected(info: dict) -> str:
    if not info:
        return ""
    if info.get("sheet"):
        return f"Foglio «{info['sheet']}», intestazione: {'sì' if info.get('header') else 'no'}."
    delim = info.get("delimiter")
    delim_text = {"\t": "tabulazione", ";": "punto e virgola", ",": "virgola", "|": "pipe"}.get(delim, f"«{delim}»")
    enc = ENCODINGS.get(info.get("encoding"), info.get("encoding") or "?")
    enc = {"utf-16-le": "UTF-16", "utf-16-be": "UTF-16"}.get(enc, enc)
    dec = info.get("decimal") or "."
    return (f"Applicato: delimitatore {delim_text}, codifica {enc}"
            f"{' (rilevata)' if info.get('encoding_auto') else ''}, "
            f"intestazione {'sì' if info.get('header') else 'no'}, decimale «{dec}».")


class ImportDialog(QDialog):
    """Impostazioni di importazione: automatiche per default, con scelta manuale."""

    def __init__(self, parent, kind: str, options: ImportOptions, info: dict, sheets=None, sheet=None, message: str = ""):
        super().__init__(parent)
        self.kind = kind
        self.setWindowTitle("Impostazioni di importazione")
        self.setMinimumWidth(440)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(18, 16, 18, 14)
        lay.setSpacing(10)

        if message:
            warn = QLabel(message)
            warn.setObjectName("notice")
            warn.setWordWrap(True)
            lay.addWidget(warn)

        form = QFormLayout()
        form.setHorizontalSpacing(14)
        form.setVerticalSpacing(10)
        form.setLabelAlignment(Qt.AlignLeft)
        custom = options.delimiter if options.delimiter not in (None, ";", ",", "\t", "|") else ""
        self.delim = _combo(DELIMITER_CHOICES, options.delimiter if not custom else "custom")
        self.custom_delim = QLineEdit(custom)
        self.custom_delim.setMaxLength(4)
        self.custom_delim.setPlaceholderText("1-4 caratteri")
        self.custom_delim.setFixedWidth(110)
        self.custom_delim.setEnabled(bool(custom))
        self.delim.currentIndexChanged.connect(lambda: self.custom_delim.setEnabled(self.delim.currentData() == "custom"))
        drow = QHBoxLayout()
        drow.addWidget(self.delim, 1)
        drow.addWidget(self.custom_delim)
        self.encoding = _combo(ENCODING_CHOICES, options.encoding)
        self.header = _combo(HEADER_CHOICES, options.header)
        self.decimal = _combo(DECIMAL_CHOICES, options.decimal)
        self.sheet = QComboBox()
        if kind == "excel":
            for name, visible in (sheets or []):
                self.sheet.addItem(name if visible else f"{name} (nascosto)", name)
            if sheet is not None:
                self.sheet.setCurrentIndex(max(0, self.sheet.findData(sheet)))
            form.addRow("Foglio", self.sheet)
            form.addRow("Intestazione", self.header)
            self.header.setItemText(0, "Rilevamento automatico")
            self.header.removeItem(0)  # per Excel la scelta è sempre esplicita
            self.header.setCurrentIndex(0 if options.header in (None, True) else 1)
        else:
            form.addRow("Delimitatore", drow)
            form.addRow("Codifica", self.encoding)
            form.addRow("Intestazione", self.header)
            form.addRow("Separatore decimale", self.decimal)
        lay.addLayout(form)

        detected = describe_detected(info)
        if detected:
            note = QLabel(detected)
            note.setObjectName("hint")
            note.setWordWrap(True)
            lay.addWidget(note)
        for text in (info or {}).get("warnings", []):
            w = QLabel("• " + text)
            w.setObjectName("notice")
            w.setWordWrap(True)
            lay.addWidget(w)
        if kind != "excel":
            tip = QLabel("Con file molto grandi la ricarica rilegge l'intero file. Se cambi codifica da UTF-8 a Windows-1252 o UTF-16 "
                         "viene creata una copia temporanea convertita (si elimina alla chiusura).")
            tip.setObjectName("hint")
            tip.setWordWrap(True)
            lay.addWidget(tip)
        self.error = QLabel("")
        self.error.setObjectName("error")
        self.error.setWordWrap(True)
        self.error.hide()
        lay.addWidget(self.error)

        row = QHBoxLayout()
        reset = QPushButton("Ripristina automatico")
        reset.setObjectName("ghost")
        reset.clicked.connect(self._reset)
        cancel = QPushButton("Annulla")
        cancel.clicked.connect(self.reject)
        self.apply_button = QPushButton("Applica e ricarica")
        self.apply_button.setObjectName("primary")
        self.apply_button.setDefault(True)
        self.apply_button.clicked.connect(self._apply)
        row.addWidget(reset)
        row.addStretch(1)
        row.addWidget(cancel)
        row.addWidget(self.apply_button)
        lay.addLayout(row)
        self.result_options = options
        self.result_sheet = sheet

    def _reset(self):
        for box in (self.delim, self.encoding, self.header, self.decimal):
            box.setCurrentIndex(0)
        self.custom_delim.clear()

    def _apply(self):
        try:
            if self.kind == "excel":
                options = ImportOptions(header=bool(self.header.currentData()))
                self.result_sheet = self.sheet.currentData()
            else:
                delim = self.delim.currentData()
                if delim == "custom":
                    delim = self.custom_delim.text()
                    if not delim:
                        raise ImportProblem("Scrivi il delimitatore personalizzato.")
                options = ImportOptions(delimiter=delim, encoding=self.encoding.currentData(),
                                        header=self.header.currentData(), decimal=self.decimal.currentData())
                validate_options(options)
        except ImportProblem as exc:
            self.error.setText(exc.user_message)
            self.error.show()
            return
        self.result_options = options
        self.accept()


class SheetDialog(QDialog):
    """Scelta del foglio da importare da un file Excel con più fogli."""

    def __init__(self, parent, filename: str, sheets: list[tuple[str, bool]]):
        super().__init__(parent)
        self.setWindowTitle("Scegli il foglio")
        self.setMinimumWidth(380)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(18, 16, 18, 14)
        lay.setSpacing(10)
        title = QLabel(f"«{filename}» contiene {len(sheets)} fogli. Quale vuoi importare?")
        title.setWordWrap(True)
        lay.addWidget(title)
        self.list = QListWidget()
        for name, visible in sheets:
            item = QListWidgetItem(name if visible else f"{name} (nascosto)")
            item.setData(Qt.UserRole, name)
            self.list.addItem(item)
        self.list.setCurrentRow(0)
        self.list.itemDoubleClicked.connect(lambda _i: self.accept())
        self.list.setMinimumHeight(140)
        lay.addWidget(self.list)
        self.header_box = QCheckBox("La prima riga contiene le intestazioni")
        self.header_box.setChecked(True)
        lay.addWidget(self.header_box)
        note = QLabel("Vengono importati i valori delle celle; formule, macro, grafici e formattazione non vengono mantenuti nella vista.")
        note.setObjectName("hint")
        note.setWordWrap(True)
        lay.addWidget(note)
        row = QHBoxLayout()
        row.addStretch(1)
        cancel = QPushButton("Annulla")
        cancel.clicked.connect(self.reject)
        ok = QPushButton("Importa")
        ok.setObjectName("primary")
        ok.setDefault(True)
        ok.clicked.connect(self.accept)
        row.addWidget(cancel)
        row.addWidget(ok)
        lay.addLayout(row)

    def selected_sheet(self) -> str:
        return self.list.currentItem().data(Qt.UserRole)

    def header(self) -> bool:
        return self.header_box.isChecked()


# --------------------------------------------------------------------------- filtro di colonna

class FilterDialog(QDialog):
    """Filtro di una colonna: condizione + valore, oppure scelta tra i valori univoci. Ordinamento della colonna."""

    def __init__(self, parent, column: str, spec: dict | None, values, truncated: bool, sort: str | None = None,
                 dtype: str | None = None):
        super().__init__(parent)
        self.setWindowTitle(f"Filtro e ordine: {column}")
        self.resize(480, 640)
        self.result_spec: dict | None = None
        self.result_sort: str | None = sort   # None, "asc" o "desc" per questa colonna
        self.dtype = dtype
        self._values = [v for v, _ in values]
        spec = normalize_spec(spec)
        chosen_set = set(spec["values"])

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 14)
        layout.setSpacing(8)

        sort_row = QHBoxLayout()
        sort_row.setSpacing(8)
        self.sort_asc = QPushButton("Ordina crescente")
        self.sort_desc = QPushButton("Ordina decrescente")
        for button, kind in ((self.sort_asc, "asc"), (self.sort_desc, "desc")):
            button.setCheckable(True)
            button.setChecked(sort == kind)
            button.setToolTip("Numeri e date si ordinano come numeri e date; i valori vuoti restano in fondo. Clic di nuovo per togliere l'ordine.")
            sort_row.addWidget(button, 1)
        self.sort_asc.clicked.connect(lambda checked: self._pick_sort("asc", checked))
        self.sort_desc.clicked.connect(lambda checked: self._pick_sort("desc", checked))
        layout.addLayout(sort_row)
        layout.addSpacing(4)

        cond_title = QLabel("Mostra le righe in cui il valore…")
        cond_title.setObjectName("hint")
        layout.addWidget(cond_title)
        cond_row = QHBoxLayout()
        cond_row.setSpacing(8)
        self.condition = QComboBox()
        for key, label in CONDITIONS:
            self.condition.addItem(label, key)
        self.condition.setCurrentIndex(max(0, self.condition.findData(spec["op"])))
        self.value_edit = QLineEdit(spec["value"])
        self.value_edit.setPlaceholderText("Valore")
        self.value_edit.setClearButtonEnabled(True)
        cond_row.addWidget(self.condition, 0)
        cond_row.addWidget(self.value_edit, 1)
        layout.addLayout(cond_row)
        self.case_box = QCheckBox("Maiuscole/minuscole")
        self.case_box.setChecked(spec["case"])
        self.case_box.setToolTip("Se attivo, «Mario» e «mario» sono valori diversi (vale per le condizioni su testo).")
        layout.addWidget(self.case_box)
        self.error = QLabel("")
        self.error.setObjectName("error")
        self.error.setWordWrap(True)
        self.error.hide()
        layout.addWidget(self.error)
        self.condition.currentIndexChanged.connect(self._condition_changed)
        self.value_edit.textChanged.connect(lambda _t: self.error.hide())
        self.value_edit.returnPressed.connect(self._apply)

        layout.addSpacing(4)
        title = f"…oppure scegli tra i {fmt_int(len(values))} valori univoci" + (" (mostrati i primi)" if truncated else "")
        lbl2 = QLabel(title)
        lbl2.setObjectName("hint")
        layout.addWidget(lbl2)
        self.value_search = QLineEdit()
        self.value_search.setPlaceholderText("Cerca tra i valori…")
        self.value_search.setClearButtonEnabled(True)
        self.value_search.textChanged.connect(self._filter_list)
        layout.addWidget(self.value_search)

        self.list = QListWidget()
        for value, count in values:
            label = EMPTY_LABEL if value is None else (value if value != "" else EMPTY_LABEL)
            item = QListWidgetItem(f"{label}    ({fmt_int(count)})")
            item.setFlags(Qt.ItemIsUserCheckable | Qt.ItemIsEnabled)
            item.setCheckState(Qt.Checked if value in chosen_set else Qt.Unchecked)
            self.list.addItem(item)
        self.list.itemChanged.connect(self._update_counter)
        layout.addWidget(self.list, 1)

        row = QHBoxLayout()
        sel = QPushButton("Seleziona risultati")
        sel.clicked.connect(lambda: self._set_visible(True))
        desel = QPushButton("Deseleziona tutti")
        desel.clicked.connect(self._clear_all)
        self.counter = QLabel("")
        self.counter.setObjectName("hint")
        row.addWidget(sel)
        row.addWidget(desel)
        row.addWidget(self.counter, 1, Qt.AlignRight)
        layout.addLayout(row)

        footer = QHBoxLayout()
        remove = QPushButton("Rimuovi filtro")
        remove.setObjectName("ghost")
        remove.clicked.connect(self._remove)
        cancel = QPushButton("Annulla")
        cancel.clicked.connect(self.reject)
        self.apply_button = QPushButton("Applica")
        self.apply_button.setObjectName("primary")
        self.apply_button.setDefault(True)
        self.apply_button.clicked.connect(self._apply)
        footer.addWidget(remove)
        footer.addStretch(1)
        footer.addWidget(cancel)
        footer.addWidget(self.apply_button)
        layout.addLayout(footer)
        self._update_counter()
        self._condition_changed()
        self.value_edit.setFocus()

    def _condition_changed(self):
        needs_value = self.condition.currentData() not in NO_VALUE_OPS
        self.value_edit.setEnabled(needs_value)
        self.case_box.setEnabled(needs_value and self.condition.currentData() not in ("gt", "lt"))
        self.error.hide()

    def _filter_list(self, text: str):
        needle = text.strip().lower()
        for i in range(self.list.count()):
            self.list.item(i).setHidden(bool(needle) and needle not in self.list.item(i).text().lower())

    def _set_visible(self, checked: bool):
        self.list.blockSignals(True)
        for i in range(self.list.count()):
            item = self.list.item(i)
            if not item.isHidden():
                item.setCheckState(Qt.Checked if checked else Qt.Unchecked)
        self.list.blockSignals(False)
        self._update_counter()

    def _clear_all(self):
        self.list.blockSignals(True)
        for i in range(self.list.count()):
            self.list.item(i).setCheckState(Qt.Unchecked)
        self.list.blockSignals(False)
        self._update_counter()

    def _update_counter(self, *_):
        n = sum(1 for i in range(self.list.count()) if self.list.item(i).checkState() == Qt.Checked)
        self.counter.setText(f"{fmt_int(n)} selezionati")

    def _pick_sort(self, kind: str, checked: bool):
        self.result_sort = kind if checked else None
        self.sort_asc.setChecked(self.result_sort == "asc")
        self.sort_desc.setChecked(self.result_sort == "desc")

    def _remove(self):
        """Toglie il filtro di questa colonna (l'ordinamento si cambia con i pulsanti in alto)."""
        self.result_spec = None
        self.accept()

    def _apply(self):
        op = self.condition.currentData()
        value = self.value_edit.text()
        problem = validate_condition(self.dtype, op, value) if (op in NO_VALUE_OPS or value.strip()) else None
        if problem:
            self.error.setText(problem)
            self.error.show()
            return
        values = [self._values[i] for i in range(self.list.count())
                  if self.list.item(i).checkState() == Qt.Checked]
        spec = {"op": op, "value": value if op not in NO_VALUE_OPS else "", "case": self.case_box.isChecked(),
                "values": values}
        self.result_spec = spec if spec_is_active(spec) else None
        self.accept()


# --------------------------------------------------------------------------- dettaglio record

class RecordDialog(QDialog):
    """Dettaglio dei record selezionati: nomi delle colonne a sinistra, un valore per ogni record a destra.

    * Mostra sempre tutte le colonne del dataset, anche quelle nascoste nella tabella principale;
      il menu «Colonne» del dettaglio ha un proprio elenco di colonne nascoste.
    * TAB chiude il dettaglio (solo mentre è aperto e attivo), da qualunque controllo abbia il focus.
    * Le modifiche alle celle vengono scritte subito nella sessione della scheda: chiudere non le perde."""

    NAME_MAX_CHARS = 32
    STANDARD_WIDTH = 320     # larghezza iniziale di ogni colonna (doppia della tabella principale)
    MIN_WIDTH = 640

    def __init__(self, tab: "CsvTab", rows: list[int], cells: set[tuple[int, int]] | None = None,
                 current: tuple[int, int] | None = None):
        super().__init__(tab)
        self.tab = tab
        self.rows = rows
        # celle realmente selezionate nella tabella principale (riga, colonna); senza indicazioni: tutta la riga
        self.all_cells = set(cells) if cells is not None else {(r, c) for r in rows for c in range(len(tab.columns))}
        self._syncing = True                   # nessuna sincronizzazione verso la tabella principale durante l'apertura
        self.row_ids = [tab.visible_row_ids[r] for r in rows if r < len(tab.visible_row_ids)]
        n = len(self.row_ids)
        self.setWindowTitle("Dettaglio record" if n == 1 else f"Dettaglio di {n} record")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(6)
        bar = QHBoxLayout()
        bar.setSpacing(8)
        hint = QLabel("Doppio clic per modificare. TAB o Esc per chiudere.")
        hint.setObjectName("hint")
        self.columns_button = ColumnsButton(width_actions=True)
        self.columns_button.setFocusPolicy(Qt.NoFocus)          # SPAZIO non deve aprire il menu «Colonne»
        self.columns_button.set_columns(tab.columns, tab.detail_hidden_columns)
        self.columns_button.changed.connect(self._apply_hidden)
        self.columns_button.fit_requested.connect(self.fit_widths)
        self.columns_button.reset_widths_requested.connect(self.reset_widths)
        bar.addWidget(hint, 1)
        bar.addWidget(self.columns_button)
        layout.addLayout(bar)

        self.table = CopyTable()
        style_table(self.table, 26)
        self.table.setEditTriggers(
            QAbstractItemView.DoubleClicked | QAbstractItemView.EditKeyPressed | QAbstractItemView.AnyKeyPressed
        )
        self.table.setSelectionBehavior(QAbstractItemView.SelectItems)
        self.table.setColumnCount(n)
        self.table.setRowCount(len(tab.columns))
        self.table.setHorizontalHeaderLabels([f"Riga {rid + 1}" for rid in self.row_ids])
        for ri, name in enumerate(tab.columns):
            label = str(name) if len(str(name)) <= self.NAME_MAX_CHARS else str(name)[: self.NAME_MAX_CHARS - 1] + "…"
            header_item = QTableWidgetItem(label)
            header_item.setToolTip(str(name))
            self.table.setVerticalHeaderItem(ri, header_item)
        self.table.verticalHeader().setVisible(True)
        self.table.setWordWrap(True)
        self.table.setTextElideMode(Qt.ElideNone)
        for ci, r in enumerate(rows[:n]):
            for ri in range(len(tab.columns)):
                cell = tab.table.item(r, ri)
                item = QTableWidgetItem(cell.text() if cell else "")
                if cell is not None and cell.background().style() != Qt.NoBrush:
                    item.setBackground(cell.background())          # stesso sfondo della formattazione condizionale
                self.table.setItem(ri, ci, item)
        for c in range(n):                     # stessa larghezza standard uniforme della tabella principale
            self.table.setColumnWidth(c, self.STANDARD_WIDTH)
        if n == 1:
            self.table.horizontalHeader().setStretchLastSection(True)
        self._refit_rows()
        self._apply_hidden(initial=True)
        self.table.itemChanged.connect(self._edited)
        layout.addWidget(self.table, 1)
        self._fit_to_content()
        # con il testo a capo l'altezza delle righe dipende dalla larghezza: si ricalcola dopo ogni ridimensionamento
        self._rows_timer = QTimer(self)
        self._rows_timer.setSingleShot(True)
        self._rows_timer.setInterval(30)
        self._rows_timer.timeout.connect(self._refit_rows)
        self.table.horizontalHeader().sectionResized.connect(lambda *_: self._rows_timer.start())
        self._select_records(current)
        self.table.setFocus()
        self._syncing = False
        self.table.selectionModel().selectionChanged.connect(self._selection_changed)

    # ---- larghezza delle colonne (stessa logica della tabella principale: standard, adattamento con massimo, ripristino)
    def fit_widths(self):
        self.table.fit_all_columns(CsvTab.ADAPT_MAX_WIDTH)
        self._refit_rows()

    def reset_widths(self):
        for c in range(self.table.columnCount()):
            self.table.setColumnWidth(c, self.STANDARD_WIDTH)
        self._refit_rows()

    def _refit_rows(self):
        self.table.resizeRowsToContents()
        for ri in range(self.table.rowCount()):
            self.table.setRowHeight(ri, max(26, self.table.rowHeight(ri) + 2))

    # ---- selezione sincronizzata con la tabella principale
    def _to_detail(self, r: int, c: int) -> tuple[int, int] | None:
        """Cella (riga, colonna) della tabella principale -> cella del dettaglio (riga = campo, colonna = record)."""
        shown = self.rows[:len(self.row_ids)]
        if r not in shown or not (0 <= c < self.table.rowCount()):
            return None
        return c, shown.index(r)

    def _select_records(self, current: tuple[int, int] | None):
        """Seleziona nel dettaglio esattamente le celle selezionate nella tabella principale (nessuna riga o record
        intero) e porta in vista la cella attiva o, se non è selezionata, la prima selezionata."""
        t = self.table
        t.sel_kind = None
        sel = QItemSelection()
        mapped = []
        for r, c in sorted(self.all_cells):
            if c in self.tab.hidden_columns_idx():
                continue
            pos = self._to_detail(r, c)
            if pos is None or t.isRowHidden(pos[0]):
                continue
            mapped.append(pos)
            sel.select(t.model().index(*pos), t.model().index(*pos))
        t.selectionModel().select(sel, QItemSelectionModel.ClearAndSelect)
        cur = self._to_detail(*current) if current else None
        if cur is None or t.isRowHidden(cur[0]):
            cur = mapped[0] if mapped else (next((ri for ri in range(t.rowCount()) if not t.isRowHidden(ri)), 0), 0)
        t.selectionModel().setCurrentIndex(t.model().index(*cur), QItemSelectionModel.NoUpdate)
        t.scrollTo(t.model().index(*cur))

    def _selection_changed(self, *_):
        """La selezione fatta nel dettaglio si riflette sulle stesse celle della tabella principale (le celle dei
        record oltre il limite mostrato nel dettaglio restano selezionate)."""
        if self._syncing:
            return
        shown = self.rows[:len(self.row_ids)]
        picked = {(shown[i.column()], i.row()) for i in self.table.selectedIndexes() if i.column() < len(shown)}
        shown_set = set(shown)
        self.tab.select_cells(sorted(picked | {rc for rc in self.all_cells if rc[0] not in shown_set}))

    # ---- dimensioni
    def _fit_to_content(self):
        screen = (self.screen() or QGuiApplication.primaryScreen()).availableGeometry()
        t = self.table
        width = t.verticalHeader().sizeHint().width() + sum(t.columnWidth(c) for c in range(t.columnCount())) + 72
        height = t.horizontalHeader().height() + sum(t.rowHeight(r) for r in range(t.rowCount()) if not t.isRowHidden(r)) + 90
        self.resize(min(max(self.MIN_WIDTH, width), int(screen.width() * 0.92)),
                    min(max(260, height), int(screen.height() * 0.88)))

    def _apply_hidden(self, initial: bool = False):
        for ri, name in enumerate(self.tab.columns):
            self.table.setRowHidden(ri, name in self.tab.detail_hidden_columns)
        if not initial:
            self._fit_to_content()

    # ---- modifiche
    def _edited(self, item):
        if item.column() >= len(self.row_ids) or item.row() >= len(self.tab.columns):
            return
        self.tab.apply_edit(self.row_ids[item.column()], self.tab.columns[item.row()], item.text(),
                            table_row=self.rows[item.column()], table_col=item.row())

    # ---- TAB chiude il dettaglio
    def showEvent(self, event):
        super().showEvent(event)
        QApplication.instance().installEventFilter(self)

    def done(self, result):
        QApplication.instance().removeEventFilter(self)
        super().done(result)

    def eventFilter(self, obj, event):
        if (event.type() == QEvent.KeyPress and event.key() in (Qt.Key_Tab, Qt.Key_Space)
                and not (event.modifiers() & (Qt.ControlModifier | Qt.AltModifier | Qt.MetaModifier))
                and QApplication.activeWindow() is self and QApplication.activePopupWidget() is None):
            if event.key() == Qt.Key_Tab:
                self.close_detail()
                return True
            if self.table.state() != QAbstractItemView.EditingState:     # SPAZIO: nessun effetto (negli editor aperti resta uno spazio)
                return True
        return super().eventFilter(obj, event)

    def close_detail(self):
        self.table.commit_open_editor()  # non perde il valore che si stava scrivendo
        self.accept()


# --------------------------------------------------------------------------- scheda di un CSV

class CsvTab(AsyncMixin, QWidget):
    """Scheda di un file (CSV o foglio Excel): dati, filtri, SQL e modifiche restano separati per file."""

    MAX_DETAIL_RECORDS = 200

    titleChanged = Signal(object)
    openFailed = Signal(object)
    opened = Signal(object)
    dataChanged = Signal(object)
    toggleTopRequested = Signal()

    def __init__(self):
        super().__init__()
        self._async_init()
        self.engine = CsvEngine()
        self.table_name = ""
        self.path = ""
        self.kind = "csv"                      # "csv" | "excel"
        self.sheet: str | None = None
        self.import_options = ImportOptions()
        self.excel_header = True
        self._attempt = None                   # impostazioni in prova durante (ri)caricamento
        self._loading = False
        self.total_rows = 0
        self.columns: list[str] = []
        self.offset = 0
        self.page_size = 200
        self.column_filters: dict[str, dict] = {}
        self.sort: tuple[str, bool] | None = None      # (colonna, decrescente)
        self.hidden_columns: set[str] = set()          # nascoste nella tabella principale
        self.detail_hidden_columns: set[str] = set()   # nascoste solo nel dettaglio
        self.cf_rules: list[dict] = []                 # regole di formattazione condizionale (in ordine di precedenza)
        self.page_colors: list[dict] = []              # colori di sfondo della pagina: [{indice_colonna: "#rrggbb"}]
        self.col_widths: dict[str, int] = {}           # larghezze scelte dall'utente (o dall'adattamento), per nome colonna
        self._filling = False
        self._ops: list = []                           # operazioni sul dataset in attesa (una alla volta)
        self._current_fail = None
        self._restore = None                           # vista da ripristinare dopo il ricaricamento della pagina
        self._pending_select = None                    # (riga, colonna) da selezionare dopo il caricamento
        self._freeze_scope = 0
        self._scope_state: dict = {"kind": "all"}
        self.find_dialog: FindReplaceDialog | None = None
        self.saved_copy = ""
        self.original_path = ""                # file aperto all'inizio (un Excel originale non viene mai sovrascritto)
        self._work_format = "csv"              # formato del file di lavoro: "csv" | "xlsx"
        self._export_prefs: dict = {}          # ultime opzioni CSV scelte (delimitatore, codifica, decimale)
        self.visible_row_ids: list[int] = []
        self._rows_in_page = 0
        self._filtered_count = 0
        self.sql_dirty = False
        self._sql_programmatic = False
        self._ui_undo: dict[int, dict] = {}        # stato dell'interfaccia da ripristinare con «Annulla» (colonne eliminate)
        self._pending_anchor: tuple[int, int] | None = None
        self._pending_note = ""

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)

        # ---- parte superiore comprimibile (file, importazione, salvataggio)
        self.header_widget = QWidget()
        header = QHBoxLayout(self.header_widget)
        header.setContentsMargins(0, 12, 0, 0)
        title_box = QVBoxLayout()
        title_box.setSpacing(0)
        self.file_label = QLabel("")
        self.file_label.setObjectName("fileName")
        self.file_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.file_meta = QLabel("")
        self.file_meta.setObjectName("fileMeta")
        self.copy_label = QLabel("")
        self.copy_label.setObjectName("fileMeta")
        self.copy_label.hide()
        self.import_notice = QLabel("")
        self.import_notice.setObjectName("notice")
        self.import_notice.setWordWrap(True)
        self.import_notice.hide()
        for _lbl in (self.file_label, self.file_meta, self.copy_label):
            _lbl.setWordWrap(True)
        for _lbl in (self.file_label, self.file_meta, self.copy_label, self.import_notice):
            # il testo lungo non deve alzare la larghezza minima della finestra
            _lbl.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
            _lbl.setMinimumWidth(0)
        title_box.addWidget(self.file_label)
        title_box.addWidget(self.file_meta)
        title_box.addWidget(self.copy_label)
        title_box.addWidget(self.import_notice)
        self.import_button = QPushButton("Importazione…")
        self.import_button.setCursor(Qt.PointingHandCursor)
        self.import_button.setToolTip("Delimitatore, codifica, intestazione e separatore decimale (o foglio Excel). "
                                      "Ricarica il file con le nuove impostazioni.")
        self.import_button.clicked.connect(lambda: self.configure_import())
        # un solo pulsante «Salva» con menu a tendina: Salva / Salva con nome…
        self.save_button = QToolButton()
        self.save_button.setText("Salva")
        self.save_button.setObjectName("saveMenuButton")
        self.save_button.setCursor(Qt.PointingHandCursor)
        self.save_button.setPopupMode(QToolButton.InstantPopup)
        self.save_menu = QMenu(self.save_button)
        self.save_menu.setToolTipsVisible(True)
        self.save_action = self.save_menu.addAction("Salva", lambda: self.save())
        self.save_as_action = self.save_menu.addAction("Salva con nome…", lambda: self.save_as())
        # le scorciatoie sono gestite da shortcuts.py; qui compaiono solo come indicazione
        self.save_action.setText("Salva\t" + QKeySequence("Ctrl+S").toString(QKeySequence.NativeText))
        self.save_as_action.setText("Salva con nome…\t" + QKeySequence("Ctrl+Shift+S").toString(QKeySequence.NativeText))
        self.save_button.setMenu(self.save_menu)
        self.save_button.setEnabled(False)
        # stessa altezza per «Importazione…» e «Salva» (QPushButton e QToolButton hanno dimensioni naturali diverse)
        for b in (self.import_button, self.save_button):
            b.ensurePolished()
        height = max(self.import_button.sizeHint().height(), self.save_button.sizeHint().height())
        for b in (self.import_button, self.save_button):
            b.setFixedHeight(height)
        header.addLayout(title_box, 1)
        header.addWidget(self.import_button, 0, Qt.AlignTop)
        header.addWidget(self.save_button, 0, Qt.AlignTop)
        layout.addWidget(self.header_widget)

        self.inner = QTabWidget()
        layout.addWidget(self.inner, 1)
        self.inner.addTab(self._build_data_tab(), "Dati")
        self.inner.addTab(self._build_sql_tab(), "SQL")
        # il pulsante che nasconde la parte superiore sta fuori dall'area comprimibile
        self.toggle_button = IconButton("up", "Nascondi barra superiore")
        self.toggle_button.clicked.connect(self.toggleTopRequested.emit)
        corner = QWidget()                                   # margine a destra: la freccia non deve toccare il bordo
        corner_lay = QHBoxLayout(corner)
        corner_lay.setContentsMargins(0, 0, 8, 0)
        corner_lay.addWidget(self.toggle_button)
        self.inner.setCornerWidget(corner, Qt.TopRightCorner)
        layout.addWidget(self.progress)

        self.set_status_buttons(False)
        self.update_filter_sql()

    def _on_data_tab(self, action):
        """Ricerca e sostituzione riguardano la tabella: dalla scheda SQL le loro scorciatoie non fanno nulla."""
        if self.inner.currentIndex() == 0:
            action()

    def set_top_hidden(self, hidden: bool):
        self.header_widget.setVisible(not hidden)
        self.toggle_button.set_kind("down" if hidden else "up")
        self.toggle_button.setToolTip("Mostra barra superiore" if hidden else "Nascondi barra superiore")

    # ---- costruzione interfaccia
    def _build_data_tab(self) -> QWidget:
        data = QWidget()
        lay = QVBoxLayout(data)
        lay.setContentsMargins(0, 12, 0, 0)
        lay.setSpacing(10)
        controls = QHBoxLayout()
        controls.setSpacing(8)
        self.search_input = QLineEdit()
        self.search_input.setObjectName("search")
        self.search_input.setClearButtonEnabled(True)
        self.search_input.setPlaceholderText("Cerca in tutte le colonne…")
        self.search_input.returnPressed.connect(self.reset_and_load)
        self.search_button = QPushButton("Cerca")
        self.search_button.setObjectName("primary")
        self.search_button.clicked.connect(self.reset_and_load)
        self.clear_filters_button = QPushButton("Azzera filtri")
        self.clear_filters_button.setObjectName("ghost")
        self.clear_filters_button.clicked.connect(self.clear_filters)
        self.clear_filters_button.setEnabled(False)
        self.search_input.textChanged.connect(lambda _t: self._update_clear_button())
        self.columns_button = ColumnsButton(width_actions=True)
        self.columns_button.changed.connect(self.apply_hidden_columns)
        self.columns_button.fit_requested.connect(self.fit_column_widths)
        self.columns_button.reset_widths_requested.connect(self.reset_column_widths)
        self.columns_button.setEnabled(False)
        self.types_button = QToolButton()
        self.types_button.setText("Tipi")
        self.types_button.setToolTip("Riconosci e assegna il tipo di dato delle colonne (all'apertura tutto è testo)")
        self.types_button.setPopupMode(QToolButton.InstantPopup)
        self.types_menu = QMenu(self.types_button)
        self.types_menu.setToolTipsVisible(True)
        self.types_menu.addAction("Riconosci e formatta le colonne…", lambda: self.open_types(detect=True))
        self.types_menu.addAction("Scegli il tipo delle colonne…", lambda: self.open_types(detect=False))
        self.types_menu.addSeparator()
        self.type_current_action = self.types_menu.addAction("Tipo della colonna corrente")
        self.type_current_action.setEnabled(False)
        self.type_set_menu = self.types_menu.addMenu("Imposta il tipo della colonna corrente")
        self.type_set_menu.setToolTipsVisible(True)
        for key, label, _ in coltypes.TYPES:
            act = self.type_set_menu.addAction(label, lambda k=key: self.set_column_type(k))
            act.setCheckable(True)
            act.setData(key)
        self.types_menu.aboutToShow.connect(self._update_types_menu)
        self.types_button.setMenu(self.types_menu)
        self.edit_button = QToolButton()
        self.edit_button.setText("Menu")
        self.edit_button.setToolTip("Trova e sostituisci, formattazione, colonne e righe")
        self.edit_button.setPopupMode(QToolButton.InstantPopup)
        self.edit_menu = QMenu(self.edit_button)
        self.edit_menu.setToolTipsVisible(True)
        self.undo_action = self.edit_menu.addAction("Annulla", lambda: self.undo())
        self.copy_action = self.edit_menu.addAction("Copia\t" + SC.hint("Copia"), lambda: self.table.copy_selection())
        self.paste_action = self.edit_menu.addAction("Incolla\t" + SC.hint("Incolla"), lambda: self.paste())
        self.edit_menu.addSeparator()
        self.edit_menu.aboutToShow.connect(self._update_undo_action)
        self.find_button = self.edit_menu.addAction("Trova e sostituisci…", lambda: self.open_find())
        self.find_button.setToolTip("Trova e sostituisci valori (Ctrl+F)")
        self.cf_button = self.edit_menu.addAction("Formattazione condizionale…", self.open_cf)
        self.cf_button.setToolTip("Colori di sfondo delle celle in base al contenuto")
        self.edit_menu.addSeparator()
        self.edit_menu.addAction("Aggiungi colonna…", lambda: self.add_column_prompt())
        self.edit_menu.addAction("Rinomina colonna…", lambda: self.rename_column_prompt())
        self.edit_menu.addAction("Elimina colonna…", lambda: self.delete_column_prompt())
        self.edit_menu.addSeparator()
        self.edit_menu.addAction("Aggiungi riga dopo la selezione", self.add_row_prompt)
        self.edit_menu.addAction("Elimina righe selezionate…", self.delete_rows_prompt)
        self.edit_menu.addAction("Dettaglio record selezionati\t" + SC.hint("Dettaglio record"), self.show_selected_record)
        self.edit_menu.addSeparator()
        self.edit_menu.addAction("Scorciatoie da tastiera…", lambda: SC.ShortcutsDialog(self).exec())
        self.edit_button.setMenu(self.edit_menu)
        for b in (self.find_button, self.cf_button, self.edit_button, self.types_button):
            b.setEnabled(False)
        self.search_input.setMinimumWidth(120)
        controls.addWidget(self.search_input, 1)
        controls.addWidget(self.search_button)
        controls.addWidget(self.clear_filters_button)
        controls.addWidget(self.edit_button)
        controls.addWidget(self.types_button)
        controls.addWidget(self.columns_button)
        lay.addLayout(controls)

        # etichette dei filtri attivi: l'area compare solo quando c'è almeno un filtro
        self.chip_container = QWidget()
        self.chip_row = QHBoxLayout(self.chip_container)
        self.chip_row.setContentsMargins(0, 0, 0, 0)
        self.chip_row.setSpacing(6)
        self.chip_container.hide()
        lay.addWidget(self.chip_container)

        self.table = DataTableWidget()
        style_table(self.table)
        self.table.setEditTriggers(
            QAbstractItemView.DoubleClicked | QAbstractItemView.EditKeyPressed | QAbstractItemView.AnyKeyPressed
        )
        self.table.setSelectionBehavior(QAbstractItemView.SelectItems)
        self.table.verticalHeader().setVisible(True)   # numero di record del file; permette di ridimensionare le righe
        header = self.table.horizontalHeader()
        header.setStretchLastSection(False)      # tutte le colonne con la stessa larghezza standard
        header.sectionResized.connect(self._remember_width)
        header.setObjectName("dataHeader")       # spazio a destra per l'imbuto (vedi tema)
        header.funnel_enabled = True
        header.funnelClicked.connect(self.filter_by_header_click)   # il clic sul nome seleziona solo la colonna
        self.table.itemChanged.connect(self.on_cell_edited)
        self.table.tabPressed.connect(self.show_selected_record)
        self.table.selectionModel().selectionChanged.connect(self._selection_changed)
        self._setup_context_menus()
        for sequence in ("Ctrl+F", "Ctrl+H"):
            QShortcut(QKeySequence(sequence), self, activated=lambda: self._on_data_tab(self.open_find)).setContext(Qt.WidgetWithChildrenShortcut)
        QShortcut(QKeySequence.Undo, self, activated=self._undo_shortcut).setContext(Qt.WidgetWithChildrenShortcut)
        self.table.commandRequested.connect(self._table_command)
        self.table.enterCommitted.connect(self._enter_committed)
        QShortcut(QKeySequence("F3"), self, activated=lambda: self._on_data_tab(self.find_next)).setContext(Qt.WidgetWithChildrenShortcut)
        QShortcut(QKeySequence("Ctrl+S"), self, activated=lambda: self.save()).setContext(Qt.WidgetWithChildrenShortcut)
        QShortcut(QKeySequence("Ctrl+Shift+S"), self, activated=lambda: self.save_as()).setContext(Qt.WidgetWithChildrenShortcut)
        lay.addWidget(self.table, 1)

        nav = QHBoxLayout()
        nav.setSpacing(8)
        per_page = QLabel("Righe per pagina")
        per_page.setObjectName("hint")
        self.page_size_box = QSpinBox()
        self.page_size_box.setRange(50, 1_000_000)
        self.page_size_box.setStepType(QSpinBox.AdaptiveDecimalStepType)
        self.page_size_box.setGroupSeparatorShown(True)
        self.page_size_box.setKeyboardTracking(False)  # ricarica solo a Invio o quando perde il focus
        self.page_size_box.setToolTip("Da 50 a 1.000.000 righe. Pagine molto grandi richiedono più tempo e memoria.")
        self.page_size_box.setValue(self.page_size)
        self.page_size_box.valueChanged.connect(self.change_page_size)
        self.prev_button = QPushButton("← Precedente")
        self.prev_button.clicked.connect(self.previous_page)
        self.next_button = QPushButton("Successiva →")
        self.next_button.clicked.connect(self.next_page)
        self.status = ElidedLabel("Apertura del file…")
        self.status.setObjectName("statusText")
        self.status.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        nav.addWidget(per_page)
        nav.addWidget(self.page_size_box)
        nav.addSpacing(8)
        nav.addWidget(self.prev_button)
        nav.addWidget(self.next_button)
        nav.addWidget(self.status, 1, Qt.AlignRight)
        lay.addLayout(nav)
        return data

    def _build_sql_tab(self) -> QWidget:
        sql_tab = QWidget()
        lay = QVBoxLayout(sql_tab)
        lay.setContentsMargins(0, 12, 0, 0)
        lay.setSpacing(8)
        hint = QLabel("La query segue filtri, ordinamento e colonne visibili della scheda Dati finché non la modifichi. "
                      "La tabella si chiama csv_data.")
        hint.setObjectName("hint")
        hint.setWordWrap(True)
        lay.addWidget(hint)
        self.sql_notice = QLabel("")
        self.sql_notice.setObjectName("notice")
        self.sql_notice.setWordWrap(True)
        self.sql_notice.hide()
        self.sql_notice_button = QPushButton("Sostituisci con la query automatica")
        self.sql_notice_button.setToolTip("Aggiorna la query: il testo personalizzato attuale viene sostituito da quello generato dalla tabella Dati")
        self.sql_notice_button.clicked.connect(self.restore_sql)
        self.sql_notice_button.hide()
        notice_row = QHBoxLayout()
        notice_row.addWidget(self.sql_notice, 1)
        notice_row.addWidget(self.sql_notice_button, 0, Qt.AlignTop)
        lay.addLayout(notice_row)
        self.sql_mode_label = QLabel("")
        self.sql_mode_label.setObjectName("hint")

        splitter = QSplitter(Qt.Vertical)
        top = QWidget()
        top_lay = QVBoxLayout(top)
        top_lay.setContentsMargins(0, 0, 0, 0)
        top_lay.setSpacing(8)
        self.sql_input = QPlainTextEdit()
        self.sql_input.textChanged.connect(self._sql_edited)
        top_lay.addWidget(self.sql_input, 1)
        row = QHBoxLayout()
        row.setSpacing(8)
        self.run_sql_button = QPushButton("Esegui SQL")
        self.run_sql_button.setObjectName("primary")
        self.run_sql_button.clicked.connect(self.run_sql)
        self.export_button = QPushButton("Esporta risultato SQL…")
        self.export_button.clicked.connect(self.export_sql)
        self.restore_sql_button = QPushButton("Torna alla query automatica")
        self.restore_sql_button.setObjectName("ghost")
        self.restore_sql_button.setEnabled(False)
        self.restore_sql_button.clicked.connect(self.restore_sql)
        row.addWidget(self.run_sql_button)
        row.addWidget(self.export_button)
        row.addWidget(self.restore_sql_button)
        row.addWidget(self.sql_mode_label)
        row.addStretch(1)
        top_lay.addLayout(row)
        splitter.addWidget(top)

        self.sql_result = CopyTable()
        style_table(self.sql_result)
        self.sql_result.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.sql_result.horizontalHeader().setStretchLastSection(True)
        splitter.addWidget(self.sql_result)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 2)
        lay.addWidget(splitter, 1)
        return sql_tab

    # ---- apertura, ricarica, errori
    def open(self, path: str, options: ImportOptions | None = None, sheet: str | None = None, header: bool = True):
        self.path = path
        self.kind = "excel" if excel_kind(path) else "csv"
        self.sheet = sheet
        self.excel_header = header
        self.import_options = options or ImportOptions()
        self.file_label.setText(os.path.basename(path))
        self.file_label.setToolTip(path)
        self.file_meta.setText(os.path.dirname(path))
        self.original_path = path
        self._work_format = "xlsx" if self.kind == "excel" else "csv"      # un .xls si salva sempre come .xlsx
        self._start_load(self.import_options, sheet, header)

    def _start_load(self, options: ImportOptions, sheet: str | None, header: bool):
        path, kind = self.path, self.kind
        self._attempt = (options, sheet, header)
        self._loading = True
        self.status.setText("Apertura file e conteggio righe…")
        if kind == "excel":
            fn = lambda: self.engine.open_excel(path, sheet, header)  # noqa: E731
        else:
            fn = lambda: self.engine.open_csv(path, options)  # noqa: E731
        self.run_worker(fn, self._csv_opened)

    def _csv_opened(self, result):
        self._loading = False
        self._ui_undo.clear()
        options, sheet, header = self._attempt
        self.import_options, self.sheet, self.excel_header = options, sheet, header
        self.columns, self.total_rows = result
        # si conservano filtri e colonne nascoste ancora validi; le modifiche non si possono riportare su un'altra lettura
        self.column_filters = {c: s for c, s in self.column_filters.items() if c in self.columns}
        if self.sort and self.sort[0] not in self.columns:
            self.sort = None
        self.hidden_columns.intersection_update(self.columns)
        self.detail_hidden_columns.intersection_update(self.columns)
        self.cf_rules = [r for r in self.cf_rules if r["column"] in self.columns]
        self._ops.clear()
        self._update_save_label()
        self._update_cf_button()
        self.offset = 0
        self.save_button.setEnabled(True)
        self.columns_button.setEnabled(True)
        for b in (self.find_button, self.cf_button, self.edit_button, self.types_button):
            b.setEnabled(True)
        self.col_widths.clear()                # nuovo file: tutte le colonne alla larghezza standard
        self.columns_button.set_columns(self.columns, self.hidden_columns)
        self._refresh_meta()
        self.set_status_buttons(True)
        self.rebuild_chips()
        self.update_filter_sql()
        self.reset_and_load()
        self.opened.emit(self)

    def _refresh_meta(self):
        info = self.engine.info
        text = f"{fmt_int(self.total_rows)} righe, {len(self.columns)} colonne"
        if self.engine.source_kind == "excel":
            text = f"Foglio «{self.sheet}»: " + text
        else:
            delim = {"\t": "tabulazione", ";": "punto e virgola", ",": "virgola", "|": "pipe"}.get(info.get("delimiter"), f"«{info.get('delimiter')}»")
            enc = {"utf-16-le": "utf-16", "utf-16-be": "utf-16"}.get(info.get("encoding"), info.get("encoding"))
            text += f", delimitatore {delim}, codifica {ENCODINGS.get(enc, enc)}"
        self.file_meta.setText(text)
        warnings = info.get("warnings") or []
        if warnings:
            self.import_notice.setText(" ".join(warnings) + " Apri «Importazione…» per correggere.")
            self.import_notice.show()
        else:
            self.import_notice.hide()

    def configure_import(self, message: str = "") -> bool:
        """Apre le impostazioni di importazione e, se confermate, ricarica il file."""
        sheets = None
        if self.kind == "excel":
            try:
                sheets = list_sheets(self.path)
            except ExcelProblem as exc:
                QMessageBox.critical(self, "Impossibile leggere il file", str(exc))
                return False
        dialog = ImportDialog(self, self.kind, self.import_options, self.engine.info if self.columns else {},
                              sheets, self.sheet, message)
        if dialog.exec() != QDialog.Accepted:
            return False
        options, sheet = dialog.result_options, dialog.result_sheet
        if self.kind == "excel":
            return self.reload(options, sheet, bool(options.header))
        return self.reload(options, None, True)

    def reload(self, options: ImportOptions, sheet: str | None, header: bool) -> bool:
        """Ricarica il file con altre impostazioni. Con modifiche non salvate chiede Salva / Non salvare / Annulla.

        Ritorna False se l'utente annulla; True se il ricaricamento è partito o partirà dopo il salvataggio."""
        target_sheet = sheet if sheet is not None else self.sheet

        def go():
            self._start_load(options, target_sheet, header)

        if not self.has_unsaved():
            go()
            return True
        return self.guard_unsaved("ricaricare il file con nuove impostazioni", go)

    def guard_unsaved(self, action: str, proceed, closing: bool = False) -> bool:
        """Se ci sono modifiche non salvate chiede cosa fare. «proceed» parte solo dopo «Non salvare» o dopo un
        salvataggio riuscito. Ritorna False se l'utente annulla."""
        if not self.has_unsaved():
            proceed()
            return True
        name = os.path.basename(self.path)
        answer = ask_unsaved(
            self, "Modifiche non salvate",
            f"«{name}» ha {self.engine.change_count} modifiche non salvate.\n\nSe vuoi {action} senza salvarle andranno perse "
            "(il file originale non viene modificato).")
        if answer == "cancel":
            return False
        if answer == "discard":
            proceed()
            return True
        self.save(then=lambda ok: proceed() if ok else None, closing=closing)
        return True

    def _worker_failed(self, error):
        self.progress.hide()
        fail, self._current_fail = self._current_fail, None
        if fail is not None:
            QTimer.singleShot(0, fail)
        exc = getattr(self.worker, "exc", None)
        was_loading, self._loading = self._loading, False
        first_open = not self.columns
        if isinstance(exc, (ImportProblem, ExcelProblem)):
            message = getattr(exc, "user_message", None) or str(exc)
            fixable = getattr(exc, "fixable", False)
            if fixable and self.kind == "csv" and was_loading:
                self._offer_fix(message, first_open)
                return
            title = "Impossibile aprire il file" if first_open else "Operazione non riuscita"
            if not first_open and was_loading:
                message += "\n\nIl file resta aperto con le impostazioni precedenti."
            QMessageBox.critical(self, title, message)
        else:
            QMessageBox.critical(self, "Errore", error)
        if first_open:
            self.openFailed.emit(self)

    def _offer_fix(self, message: str, first_open: bool):
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Warning)
        box.setWindowTitle("Importazione non riuscita" if first_open else "Ricarica non riuscita")
        box.setText(message)
        if not first_open:
            box.setInformativeText("Il file resta aperto con le impostazioni precedenti.")
        fix = box.addButton("Correggi impostazioni…", QMessageBox.AcceptRole)
        box.addButton("Chiudi scheda" if first_open else "Annulla", QMessageBox.RejectRole)
        box.exec()
        if box.clickedButton() is fix:
            if self.configure_import(message):
                return
        if first_open:
            self.openFailed.emit(self)

    def _persist_note(self) -> str:
        """Quando una modifica arriva sul disco: dipende dal tipo di file (un Excel originale non si sovrascrive mai)."""
        if self.engine.protected_path():
            return ("La modifica diventa permanente solo salvando una copia: il file Excel originale non viene mai modificato. "
                    "Finché non salvi puoi annullarla.")
        return ("La modifica arriva nel file solo quando salvi: «Salva» sostituisce il file aperto, «Salva con nome» crea una copia. "
                "Finché non salvi puoi annullarla.")

    def has_unsaved(self) -> bool:
        return self.engine.has_changes()

    def shutdown(self):
        try:
            self.engine.close()
        except Exception:
            pass

    def set_status_buttons(self, enabled: bool):
        self.prev_button.setEnabled(enabled and self.offset > 0)
        self.next_button.setEnabled(enabled and self.offset + self.page_size < self._filtered_count)
        self.search_button.setEnabled(enabled)
        self.run_sql_button.setEnabled(enabled)
        self.export_button.setEnabled(enabled)

    # ---- filtri
    def current_column_filters(self) -> dict:
        return {c: normalize_spec(spec) for c, spec in self.column_filters.items()}

    def _update_clear_button(self):
        self.clear_filters_button.setEnabled(self.has_active_filters())

    def has_active_filters(self) -> bool:
        return bool(self.search_input.text().strip()) or bool(self.column_filters)

    CHIP_MAX = 4          # chip mostrati per intero; le altre colonne finiscono in «+N»

    @staticmethod
    def _short(text: str, limit: int = 18) -> str:
        text = str(text)
        return text if len(text) <= limit else text[: limit - 1] + "…"

    def rebuild_chips(self):
        """Riepilogo compatto: per ogni colonna filtrata solo quanti filtri ci sono (dettaglio nel tooltip)."""
        while self.chip_row.count():
            item = self.chip_row.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        entries = [(col, filter_count(spec), spec) for col, spec in self.column_filters.items()]
        for col, n, spec in entries[: self.CHIP_MAX]:
            chip = QPushButton(f"{self._short(col)}: {n} {'filtro' if n == 1 else 'filtri'}  ✕")
            chip.setObjectName("chip")
            chip.setCursor(Qt.PointingHandCursor)
            chip.setToolTip(f"{col}: {describe_filter(spec)}\nClic per rimuovere i filtri di questa colonna")
            chip.clicked.connect(lambda _=False, c=col: self.remove_filter(c))
            self.chip_row.addWidget(chip)
        rest = entries[self.CHIP_MAX:]
        if rest:
            more = QPushButton(f"+{len(rest)} {'colonna' if len(rest) == 1 else 'colonne'}")
            more.setObjectName("chip")
            more.setCursor(Qt.PointingHandCursor)
            more.setToolTip("\n".join(f"{c}: {n} {'filtro' if n == 1 else 'filtri'}" for c, n, _ in rest))
            menu = QMenu(more)
            for col, n, _spec in rest:
                menu.addAction(f"{self._short(col, 30)}: {n} {'filtro' if n == 1 else 'filtri'}  ✕",
                               lambda c=col: self.remove_filter(c))
            more.setMenu(menu)
            self.chip_row.addWidget(more)
        if self.sort:
            chip = QPushButton(f"Ordine: {self._short(self.sort[0])} {'↓' if self.sort[1] else '↑'}  ✕")
            chip.setObjectName("chip")
            chip.setCursor(Qt.PointingHandCursor)
            chip.setToolTip(f"Ordinato per {self.sort[0]}. Clic per rimuovere l'ordinamento")
            chip.clicked.connect(self.clear_sort)
            self.chip_row.addWidget(chip)
        self.chip_row.addStretch(1)
        self.chip_container.setVisible(bool(self.column_filters) or self.sort is not None)

    def clear_sort(self):
        self.sort = None
        self.filters_changed()

    def filters_changed(self):
        self._update_clear_button()
        self.update_filter_sql()
        self.rebuild_chips()
        self.reset_and_load()

    def remove_filter(self, column):
        self.column_filters.pop(column, None)
        self.filters_changed()

    def clear_filters(self):
        self.search_input.clear()
        self.column_filters.clear()
        self.filters_changed()

    def filter_by_header_click(self, logical_index):
        if logical_index < 0 or logical_index >= len(self.columns):
            return
        column = self.columns[logical_index]
        search = self.search_input.text()
        filters = self.current_column_filters()
        self.status.setText(f"Lettura dei valori di «{column}»…")
        self.run_worker(
            lambda: self.engine.distinct_values(column, search, filters),
            lambda res: self._show_filter_dialog(column, res),
        )

    def _show_filter_dialog(self, column, result):
        values, truncated = result
        self._refresh_status()
        current_sort = ("desc" if self.sort[1] else "asc") if self.sort and self.sort[0] == column else None
        dialog = FilterDialog(self, column, self.column_filters.get(column), values, truncated, current_sort,
                              self.engine.dtypes.get(column))
        if dialog.exec() != QDialog.Accepted:
            return
        if dialog.result_sort:
            self.sort = (column, dialog.result_sort == "desc")
        elif self.sort and self.sort[0] == column:
            self.sort = None
        if dialog.result_spec:
            self.column_filters[column] = dialog.result_spec
        else:
            self.column_filters.pop(column, None)
        self.filters_changed()

    NO_VISIBLE_SQL = ("-- Nessuna colonna visibile: mostra almeno una colonna dal menu «Colonne» "
                      "per generare la query automatica.")

    def build_filter_sql(self) -> str:
        """Query automatica = stato della tabella «Dati»: colonne visibili nell'ordine della tabella (mai la colonna
        grafica del numero di riga), filtri attivi, ordinamento solo se scelto. I valori dei filtri sono scritti come
        letterali già protetti (la query mostrata deve essere eseguibile così com'è)."""
        if not self.columns:
            return "-- Apri un file per generare la query."
        visible = [c for c in self.columns if c not in self.hidden_columns]
        if not visible:
            return self.NO_VISIBLE_SQL
        conditions = build_conditions(self.columns, self.engine.dtypes, self.search_input.text(), self.current_column_filters())
        select = ",\n       ".join(sql_ident(c) for c in visible)
        where = ("\nWHERE " + "\n  AND ".join(inline_params(sql, params) for sql, params in conditions)) if conditions else ""
        order = (f"\nORDER BY {sql_ident(self.sort[0])} {'DESC' if self.sort[1] else 'ASC'} NULLS LAST"
                 if self.sort else "")
        return f"SELECT {select}\nFROM csv_data" + where + order + "\nLIMIT 100;"

    # ---- editor SQL unico
    def _set_sql(self, text: str):
        self._sql_programmatic = True
        self.sql_input.setPlainText(text)
        self._sql_programmatic = False
        self.sql_dirty = False
        self.restore_sql_button.setEnabled(False)
        self._update_sql_mode()

    def _update_sql_mode(self):
        if self.sql_dirty:
            self.sql_mode_label.setText("Query personalizzata: non si aggiorna da sola.")
        else:
            self.sql_mode_label.setText("Query automatica: segue la tabella Dati.")

    def _show_sql_notice(self, text: str):
        self.sql_notice.setText(text)
        self.sql_notice.show()
        self.sql_notice_button.show()

    def _hide_sql_notice(self):
        self.sql_notice.hide()
        self.sql_notice_button.hide()

    def _sql_mentions(self, name: str) -> bool:
        """La query scritta a mano cita la colonna? (nome tra virgolette o come parola intera)."""
        if not self.sql_dirty:
            return False
        text = self.sql_input.toPlainText()
        return f'"{name}"' in text or re.search(r"(?<![\w\"])" + re.escape(name) + r"(?![\w\"])", text, re.IGNORECASE) is not None

    def _custom_sql_obsolete(self, message: str):
        """Segnala una query personalizzata resa obsoleta e propone (senza imporla) di sostituirla con quella automatica."""
        self._show_sql_notice(message + " La query non è stata modificata.")
        if confirm(self, "Query SQL da aggiornare", message,
                   "Vuoi sostituirla con la query automatica? Il testo che hai scritto andrà perso. "
                   "Se scegli «Mantieni» resta com'è e potrai aggiornarla quando vuoi.",
                   yes="Sostituisci", no="Mantieni", icon=QMessageBox.Question):
            self.restore_sql()

    def _sql_edited(self):
        if self._sql_programmatic:
            return
        self.sql_dirty = True
        self.restore_sql_button.setEnabled(True)
        self._hide_sql_notice()
        self._update_sql_mode()

    def update_filter_sql(self):
        if not self.sql_dirty:
            self._set_sql(self.build_filter_sql())

    def restore_sql(self):
        self._set_sql(self.build_filter_sql())
        self._hide_sql_notice()

    # ---- colonne nascoste (solo visualizzazione)
    def apply_hidden_columns(self):
        for c, name in enumerate(self.columns):
            self.table.setColumnHidden(c, name in self.hidden_columns)
        self.columns_button.set_columns(self.columns, self.hidden_columns)
        self.update_filter_sql()

    # ---- modifiche alle celle (scritte subito nel dataset gestito dall'applicazione)
    def on_cell_edited(self, item):
        if item is None or item.column() >= len(self.columns) or item.row() >= len(self.visible_row_ids):
            return
        self._submit_edit(self.visible_row_ids[item.row()], self.columns[item.column()], item.text())

    def _submit_edit(self, row_id, column, text):
        engine = self.engine
        self.run_op(lambda: engine.set_cell(row_id, column, text),
                    lambda result: self._edit_done(result, column), self._edit_failed)

    def _edit_done(self, result, column):
        self._update_save_label()
        if not result.get("changed"):
            return
        if result.get("converted"):
            self.status.setText(f"La colonna «{column}» ora contiene anche testo e viene trattata come testo.")
            self.refresh_view(refresh_count=True)
        elif self.cf_rules:
            self.refresh_view(refresh_count=True)          # i colori dipendono dal valore
        elif self.has_active_filters():
            self.refresh_count()
        self.dataChanged.emit(self)

    def _edit_failed(self):
        self.refresh_view(refresh_count=True)              # mostra i valori realmente presenti nel dataset

    def apply_edit(self, row_id, column, text, table_row, table_col):
        """Modifica proveniente dal dettaglio: aggiorna anche la tabella principale."""
        self._submit_edit(row_id, column, text)
        item = self.table.item(table_row, table_col)
        if item is not None:
            self.table.blockSignals(True)
            item.setText(text)
            self.table.blockSignals(False)

    def _update_save_label(self):
        n = self.engine.change_count
        self.save_button.setText(f"Salva ({n})" if n else "Salva")
        self.save_button.setToolTip(f"{fmt_int(n)} modifiche non salvate (celle, righe o colonne)" if n
                                    else "Nessuna modifica da salvare")
        self.titleChanged.emit(self)                       # il titolo della scheda mostra il marcatore «modificato»

    # ---- operazioni sul dataset: in coda, una alla volta
    def run_op(self, fn, callback, on_fail=None):
        if self.is_busy():
            self._ops.append((fn, callback, on_fail))
            return
        self._current_fail = on_fail
        self.run_worker(fn, callback)

    def _worker_finished(self):
        if self._ops:
            fn, callback, on_fail = self._ops.pop(0)
            self._current_fail = on_fail
            self.run_worker(fn, callback)

    # ---- vista: ripristino di selezione e scorrimento dopo un ricaricamento
    def _capture_view(self):
        t = self.table
        cur = t.currentIndex()
        return {"cur": (cur.row(), cur.column()) if cur.isValid() else None,
                "v": t.verticalScrollBar().value(), "h": t.horizontalScrollBar().value(), "kind": t.sel_kind,
                "sel": [(r.top(), r.left(), r.bottom(), r.right()) for r in t.selectionModel().selection()]}

    def _restore_view(self, st):
        t = self.table
        rows, cols = t.rowCount(), t.columnCount()
        sel = QItemSelection()
        for top, left, bottom, right in st["sel"]:
            if top < rows and left < cols:
                sel.select(t.model().index(top, left), t.model().index(min(bottom, rows - 1), min(right, cols - 1)))
        t.selectionModel().select(sel, QItemSelectionModel.ClearAndSelect)
        if st["cur"] and st["cur"][0] < rows and st["cur"][1] < cols:
            t.selectionModel().setCurrentIndex(t.model().index(*st["cur"]), QItemSelectionModel.NoUpdate)
        t.sel_kind = st["kind"]
        QTimer.singleShot(0, lambda: (t.verticalScrollBar().setValue(st["v"]), t.horizontalScrollBar().setValue(st["h"])))

    def refresh_view(self, refresh_count: bool = False):
        """Ricarica la pagina corrente mantenendo selezione e posizione."""
        if self.columns:
            self._restore = self._capture_view()
            self.load_page(refresh_count=refresh_count)

    def refresh_count(self):
        if not self.has_active_filters():
            self._filtered_count = self.total_rows
            self._refresh_status()
            return
        engine, search, filters = self.engine, self.search_input.text(), self.current_column_filters()
        self.run_op(lambda: engine.count_filtered(search, filters), self._count_loaded)

    def _count_loaded(self, n):
        self._filtered_count = n
        self._refresh_status()
        self.set_status_buttons(True)

    # ---- caricamento pagine
    def reset_and_load(self):
        self.offset = 0
        self.update_filter_sql()
        self.load_page(refresh_count=True)

    def change_page_size(self, value):
        first_row = self.offset
        self.page_size = value
        self.offset = (first_row // value) * value   # resta sulla pagina che contiene la prima riga visibile
        if self.columns:
            self.load_page(refresh_count=False)      # il conteggio non cambia: nessuna riscansione

    def load_page(self, refresh_count: bool = True):
        if not self.columns:
            return
        search = self.search_input.text()
        offset, limit = self.offset, self.page_size
        filters = self.current_column_filters()
        order = self.sort
        rules = [dict(r) for r in self.cf_rules]
        need_count = refresh_count and self.has_active_filters()
        self.status.setText("Caricamento righe…")
        self.run_op(
            lambda: (self.engine.preview_cf(offset, limit, search, filters, order, rules),
                     self.engine.count_filtered(search, filters) if need_count else None),
            self._page_loaded,
        )

    def _page_loaded(self, result):
        (columns, rows, row_ids, colors), filtered_count = result
        self.visible_row_ids = row_ids
        self.page_colors = colors
        self._rows_in_page = len(rows)
        if not self.has_active_filters():
            self._filtered_count = self.total_rows
        elif filtered_count is not None:
            self._filtered_count = filtered_count
        self._freeze_scope += 1                 # il riempimento della tabella non è una scelta dell'utente
        try:
            self.fill_table(self.table, columns, rows)
            if self._restore is not None:
                self._restore_view(self._restore)
            self._apply_pending_select()
        finally:
            self._restore = None
            self._freeze_scope -= 1
        self._refresh_status()
        self._show_pending_note()
        self.set_status_buttons(True)

    def _refresh_status(self):
        n = self._rows_in_page
        first = self.offset + 1 if n else 0
        if self.has_active_filters():
            self.status.setText(f"Righe {fmt_int(first)}–{fmt_int(self.offset + n)} di {fmt_int(self._filtered_count)} filtrate"
                                f" · Totale {fmt_int(self.total_rows)}")
        else:
            self.status.setText(f"Righe {fmt_int(first)}–{fmt_int(self.offset + n)} di {fmt_int(self.total_rows)}")

    def fill_table(self, table, columns, rows):
        main = table is self.table
        if main:
            self._filling = True               # i ridimensionamenti di questo ricaricamento non sono scelte dell'utente
        table.blockSignals(True)
        table.setUpdatesEnabled(False)          # un solo ridisegno alla fine, non uno per cella
        table.clear()
        table.setColumnCount(len(columns))
        def label(c):
            marks = ""
            if main and self.sort and self.sort[0] == c:
                marks += "  ▼" if self.sort[1] else "  ▲"
            return f"{c}{marks}"

        labels = [label(c) for c in columns]
        table.setHorizontalHeaderLabels(labels)
        if main:
            table.horizontalHeader().active_columns = {
                i for i, c in enumerate(columns)
                if c in self.column_filters or (self.sort and self.sort[0] == c)}
            table.horizontalHeader().viewport().update()
        table.setRowCount(len(rows))
        for r, row in enumerate(rows):
            for c, value in enumerate(row):
                item = QTableWidgetItem("" if value is None else str(value))
                if main and r < len(self.page_colors) and c in self.page_colors[r]:
                    item.setBackground(QBrush(QColor(self.page_colors[r][c])))   # solo lo sfondo, mai il testo
                table.setItem(r, c, item)
        if main:
            table.setVerticalHeaderLabels([str(rid + 1) for rid in self.visible_row_ids])
        if main:
            # larghezza standard uguale per tutte (mai calcolata sul contenuto): quelle scelte dall'utente restano
            for c, name in enumerate(columns):
                table.setColumnWidth(c, self.col_widths.get(name, self.STANDARD_WIDTH))
                head = table.horizontalHeaderItem(c)
                if head is not None:
                    head.setToolTip(f"{name} — {coltypes.LABELS[coltypes.kind_of(self.engine.dtypes.get(name))]}")
            for c, name in enumerate(columns):
                table.setColumnHidden(c, name in self.hidden_columns)
            self._filling = False
        else:
            fit_columns(table)
        table.setUpdatesEnabled(True)
        table.blockSignals(False)

    # ---- larghezza delle colonne
    STANDARD_WIDTH = 160           # larghezza iniziale uguale per tutte le colonne
    ADAPT_MAX_WIDTH = 420          # limite dell'adattamento al contenuto

    def _remember_width(self, logical: int, _old: int, new: int):
        """Ricorda la larghezza scelta (a mano o con l'adattamento) così resta anche cambiando pagina."""
        if self._filling or logical >= len(self.columns) or new <= 0 or self.table.isColumnHidden(logical):
            return
        self.col_widths[self.columns[logical]] = new

    def fit_column_widths(self):
        """Adatta ogni colonna visibile a intestazione e valori della pagina (massimo ADAPT_MAX_WIDTH)."""
        self.table.fit_all_columns(self.ADAPT_MAX_WIDTH, header_extra=64)       # 64: spazio dell'imbuto del filtro
        self.status.setText("Larghezza adattata al contenuto. Ripristino: Colonne › Larghezza standard.")

    def reset_column_widths(self):
        """Riporta tutte le colonne alla larghezza standard uniforme."""
        self.col_widths.clear()
        self._filling = True
        for c in range(self.table.columnCount()):
            self.table.setColumnWidth(c, self.STANDARD_WIDTH)
        self._filling = False
        self.status.setText("Larghezza standard ripristinata per tutte le colonne.")

    # ---- tipi di dato (riconoscimento e conversione solo su richiesta)
    def _update_types_menu(self):
        col = None
        cur = self.table.currentIndex()
        if self.table.sel_kind == "col" and len(self.table.selected_columns()) == 1:
            col = self.columns[self.table.selected_columns()[0]]
        elif cur.isValid() and cur.column() < len(self.columns):
            col = self.columns[cur.column()]
        self._types_column = col
        kind = self.engine.column_kind(col) if col else None
        self.type_current_action.setText(f"Colonna «{col}»: {coltypes.LABELS[kind]}" if col else "Nessuna colonna selezionata")
        self.type_set_menu.setEnabled(col is not None)
        for act in self.type_set_menu.actions():
            act.setChecked(act.data() == kind)

    def open_types(self, detect: bool = False):
        if not self.columns:
            return
        self.table.commit_open_editor()
        engine = self.engine
        kinds = {c: engine.column_kind(c) for c in self.columns}

        def analyze(cols):
            return [engine.analyze_column(c) for c in cols]

        dialog = TypesDialog(self, self.columns, kinds, analyze, self.run_op,
                             initial=getattr(self, "_types_column", None), auto_detect=detect)
        if dialog.exec() == QDialog.Accepted and dialog.changes:
            self.apply_type_changes(dialog.changes)

    def set_column_type(self, kind: str):
        column = getattr(self, "_types_column", None)
        if column is None:
            return
        self.apply_type_changes([(column, kind, None)])

    def apply_type_changes(self, changes):
        """Passo 1 (thread): quanti valori non si convertono. Passo 2: conferma se ce ne sono. Passo 3: conversione."""
        engine = self.engine

        def plan():
            return [(c, k, s, engine.preview_conversion(c, k, s)) for c, k, s in changes]

        self.run_op(plan, self._confirm_type_changes)

    def _confirm_type_changes(self, plans):
        notes = []
        for column, kind, _scale, info in plans:
            if info["invalid"]:
                shown = ", ".join(f"«{x}»" for x in info["examples"][:4])
                notes.append(f"• «{column}» → {coltypes.LABELS[kind]}: {fmt_int(info['invalid'])} valori non convertibili "
                             f"(es. {shown}) diventeranno vuoti.")
            if info["leading_zeros"]:
                notes.append(f"• «{column}» → {coltypes.LABELS[kind]}: {fmt_int(info['leading_zeros'])} valori con zeri "
                             "iniziali li perderanno (00123 → 123).")
        if notes:
            if not confirm(self, "Conferma la conversione", "Alcuni valori cambieranno nella conversione.",
                           "\n".join(notes) + "\n\nPuoi tornare indietro con Annulla (Ctrl+Z): i valori originali vengono ripristinati.",
                           yes="Converti", no="Annulla"):
                return
        engine = self.engine
        columns = [c for c, _k, _s, _i in plans]

        def work():
            done = [engine.convert_column(c, k, s, plan=i) for c, k, s, i in plans]
            return done, engine.count_total()

        def finished(payload):
            results, _total = payload
            for name in columns:
                self.column_filters.pop(name, None)       # i filtri sul vecchio tipo non hanno più lo stesso significato
            self._update_save_label()
            self.rebuild_chips()
            self.update_filter_sql()
            self.refresh_view(refresh_count=True)
            self.dataChanged.emit(self)
            changed = [r for r in results if r["changed"]]
            invalid = sum(r["invalid"] for r in changed)
            if changed:
                self._pending_note = (f"Tipo assegnato a {len(changed)} colonn{'a' if len(changed) == 1 else 'e'}"
                                      + (f"; {fmt_int(invalid)} valori non convertibili sono ora vuoti" if invalid else "")
                                      + ". Annulla con Ctrl+Z.")
            else:
                self._pending_note = "Nessuna colonna da convertire: il tipo era già quello scelto."

        self.run_op(work, finished)

    def previous_page(self):
        self.offset = max(0, self.offset - self.page_size)
        self.load_page(refresh_count=False)

    def next_page(self):
        self.offset += self.page_size
        self.load_page(refresh_count=False)

    # ---- ambito di «trova e sostituisci» (dalla selezione nella tabella)
    PRECEDENCE_NOTE = "Precedenza se la selezione è mista: colonne intere › righe intere › celle › intero dataset."

    def _selection_changed(self, *_):
        if self._freeze_scope:
            return                                   # selezione fatta dal programma (ricerca, ricaricamento pagina)
        self._scope_state = self.compute_scope()
        self._update_find_scope()

    def compute_scope(self) -> dict:
        t = self.table
        ranges = list(t.selectionModel().selection())
        if not ranges or not self.columns:
            return {"kind": "all"}
        rows_total = t.rowCount()
        ids = self.visible_row_ids
        if t.sel_kind == "col":
            cols = [self.columns[c] for c in t.selected_columns() if c < len(self.columns)]
            if cols:
                mixed = any(not (r.top() == 0 and r.bottom() == rows_total - 1) for r in ranges)
                return {"kind": "columns", "columns": cols, "mixed": mixed}
        if t.sel_kind == "row":
            rows = t.selected_rows()
            if rows:
                mixed = any(not (r.left() == 0 and r.right() == t.columnCount() - 1) for r in ranges)
                return {"kind": "rows", "rids": [ids[r] for r in rows if r < len(ids)], "mixed": mixed}
        cells: dict[str, set] = {}
        n = 0
        for rng in ranges:
            for c in range(rng.left(), rng.right() + 1):
                if c >= len(self.columns) or t.isColumnHidden(c):
                    continue
                bucket = cells.setdefault(self.columns[c], set())
                for r in range(rng.top(), min(rng.bottom(), len(ids) - 1) + 1):
                    bucket.add(ids[r])
        n = sum(len(v) for v in cells.values())
        if n >= 2:                                   # una sola cella selezionata = punto di partenza, non un ambito
            order = [c for c in self.columns if c in cells]
            return {"kind": "cells", "columns": order, "cells": {c: sorted(v) for c, v in cells.items()},
                    "range": len(ranges) == 1, "n": n}
        return {"kind": "all"}

    def scope_text(self, filtered: bool = False) -> str:
        st = self._scope_state
        kind = st["kind"]
        if kind == "columns":
            names = ", ".join(self._short(c, 24) for c in st["columns"][:4]) + ("…" if len(st["columns"]) > 4 else "")
            text = f"Ambito: {len(st['columns'])} colonne selezionate ({names}), tutti i record anche fuori pagina."
        elif kind == "rows":
            text = f"Ambito: {fmt_int(len(st['rids']))} righe selezionate, tutte le colonne (anche quelle nascoste)."
        elif kind == "cells":
            what = "intervallo selezionato" if st.get("range") else "celle selezionate"
            text = f"Ambito: {what} ({fmt_int(st['n'])} celle)."
        else:
            text = f"Ambito: intero dataset ({fmt_int(self.total_rows)} record, colonne nascoste incluse)."
        if filtered and kind in ("all", "columns") and self.has_active_filters():
            text += " Limitato ai record che soddisfano ricerca e filtri."
        if st.get("mixed"):
            text += " La selezione contiene anche altre celle: vale solo quella di " + (
                "colonne intere." if kind == "columns" else "righe intere.")
        return text + "\n" + self.PRECEDENCE_NOTE

    def _update_find_scope(self):
        if self.find_dialog is not None and self.find_dialog.isVisible():
            self.find_dialog.set_scope_text(self.scope_text(self.find_dialog.filtered_box.isChecked()))

    def _engine_scope(self, filtered: bool) -> Scope:
        st = self._scope_state
        kind = st["kind"]
        search, filters = self.search_input.text(), self.current_column_filters()
        if kind == "columns":
            return Scope("columns", list(st["columns"]), filtered=filtered, search=search, column_filters=filters)
        if kind == "rows":
            return Scope("rows", list(self.columns), rids=list(st["rids"]))
        if kind == "cells":
            return Scope("cells", list(st["columns"]), cells={c: list(v) for c, v in st["cells"].items()})
        return Scope("all", list(self.columns), filtered=filtered, search=search, column_filters=filters)

    def _nav_columns(self, scope: Scope) -> list[str]:
        """Colonne su cui «trova successivo» può fermarsi: quelle dell'ambito che sono visibili."""
        return [c for c in self.columns if c in scope.columns and c not in self.hidden_columns]

    @staticmethod
    def _cell_in_scope(scope: Scope, rid: int, column: str) -> bool:
        if column not in scope.columns:
            return False
        if scope.kind == "rows":
            return rid in set(scope.rids or [])
        if scope.kind == "cells":
            return rid in set((scope.cells or {}).get(column, []))
        return True

    # ---- trova e sostituisci
    def open_find(self):
        if not self.columns:
            return
        if self.find_dialog is None:
            d = FindReplaceDialog(self)
            d.findNextRequested.connect(self.find_next)
            d.replaceRequested.connect(self.replace_current)
            d.replaceAllRequested.connect(self.replace_all)
            d.filtered_box.toggled.connect(lambda _c: self._update_find_scope())
            d.filtered_box.setChecked(self.has_active_filters())
            self.find_dialog = d
        d = self.find_dialog
        self._scope_state = self.compute_scope()
        d.set_scope_text(self.scope_text(d.filtered_box.isChecked()))
        d.set_status("")
        d.show()
        d.raise_()
        d.activateWindow()
        d.find_edit.setFocus()
        d.find_edit.selectAll()

    def _find_args(self):
        d = self.find_dialog
        o = d.options()
        if o["find"] == "" and not o["exact"]:
            d.set_status("Scrivi il testo da trovare (con «Corrispondenza esatta» puoi cercare le celle vuote).", True)
            return None
        scope = self._engine_scope(o["filtered"])
        nav = self._nav_columns(scope)
        if not nav:
            d.set_status("L'ambito non contiene colonne visibili.", True)
            return None
        cur = self.table.currentIndex()
        after = None
        if cur.isValid() and cur.row() < len(self.visible_row_ids):
            ci = sum(1 for c in nav if self.columns.index(c) <= cur.column()) - 1
            after = (self.offset + cur.row() + 1, ci)
        return o, scope, nav, after

    def find_next(self):
        if self.find_dialog is None or not self.columns:
            self.open_find()
            return
        args = self._find_args()
        if args is None:
            return
        o, scope, nav, after = args
        engine, search, filters, order = self.engine, self.search_input.text(), self.current_column_filters(), self.sort
        self.find_dialog.set_busy(True)
        self.run_op(lambda: engine.find_next(o["find"], o["case"], o["exact"], scope, nav, search, filters, order, after),
                    self._find_done, lambda: self.find_dialog.set_busy(False))

    def _find_done(self, hit):
        d = self.find_dialog
        d.set_busy(False)
        if hit is None:
            d.set_status("Nessuna occorrenza trovata nell'ambito.")
            return
        self.goto_cell(hit["pos"], hit["column"])
        note = " Ricominciato dall'inizio." if hit["wrapped"] else ""
        d.set_status(f"Trovato in «{hit['column']}», record {hit['rid'] + 1} del file.{note}")

    def goto_cell(self, pos: int, column: str, force_reload: bool = False):
        """Porta in vista (cambiando pagina se serve) e seleziona la cella: pos = posizione 1-based nella vista."""
        page_offset = ((pos - 1) // self.page_size) * self.page_size
        self._pending_select = ((pos - 1) % self.page_size, self.columns.index(column))
        if page_offset != self.offset or force_reload:
            self.offset = page_offset
            self.load_page(refresh_count=force_reload)
        else:
            self._freeze_scope += 1
            try:
                self._apply_pending_select()
            finally:
                self._freeze_scope -= 1

    def _apply_pending_select(self):
        if self._pending_select is None:
            return
        row, col = self._pending_select
        self._pending_select = None
        anchor, self._pending_anchor = self._pending_anchor, None
        if row < self.table.rowCount() and col < self.table.columnCount():
            if anchor is not None:
                t = self.table
                arow = min(anchor[0], t.rowCount() - 1)
                sel = QItemSelection(t.model().index(min(arow, row), min(anchor[1], col)),
                                     t.model().index(max(arow, row), max(anchor[1], col)))
                t.selectionModel().select(sel, QItemSelectionModel.ClearAndSelect)
                t.selectionModel().setCurrentIndex(t.model().index(row, col), QItemSelectionModel.NoUpdate)
                t.sel_kind = None
            else:
                # flag espliciti: setCurrentCell() lascia a Qt la scelta «sostituisci/aggiungi» in base ai modificatori
                # premuti in quel momento (con Ctrl premuto la cella di partenza resterebbe selezionata)
                idx = self.table.model().index(row, col)
                self.table.selectionModel().setCurrentIndex(
                    idx, QItemSelectionModel.ClearAndSelect | QItemSelectionModel.Current)
                self.table.sel_kind = None
            item = self.table.item(row, col)
            if item is not None:
                self.table.scrollToItem(item, QAbstractItemView.PositionAtCenter)

    def replace_current(self):
        if self.find_dialog is None:
            return
        args = self._find_args()
        if args is None:
            return
        o, scope, nav, after = args
        cur = self.table.currentIndex()
        target = None
        if cur.isValid() and cur.row() < len(self.visible_row_ids):
            rid, column = self.visible_row_ids[cur.row()], self.columns[cur.column()]
            if self._cell_in_scope(scope, rid, column):
                target = (rid, column)
        engine, search, filters, order = self.engine, self.search_input.text(), self.current_column_filters(), self.sort

        def work():
            done = {"cells": 0, "converted": []}
            if target:
                done = engine.replace_cell(target[0], target[1], o["find"], o["replace"], o["case"], o["exact"])
            nxt = engine.find_next(o["find"], o["case"], o["exact"], scope, nav, search, filters, order, after)
            return done, nxt

        self.find_dialog.set_busy(True)
        self.run_op(work, self._replace_current_done, lambda: self.find_dialog.set_busy(False))

    def _replace_current_done(self, result):
        done, nxt = result
        d = self.find_dialog
        d.set_busy(False)
        self._update_save_label()
        replaced = bool(done["cells"])
        if nxt is None:
            if replaced:
                self.refresh_view(refresh_count=True)
            d.set_status("Sostituito. Nessun'altra occorrenza nell'ambito." if replaced else "Nessuna occorrenza trovata nell'ambito.")
        else:
            self.goto_cell(nxt["pos"], nxt["column"], force_reload=replaced)
            d.set_status(("Sostituito. " if replaced else "") + f"Prossima occorrenza in «{nxt['column']}», record {nxt['rid'] + 1}.")
        if replaced:
            self.dataChanged.emit(self)
        if done["converted"]:
            d.set_status(d.status.text() + " La colonna è diventata di testo.")

    def replace_all(self):
        if self.find_dialog is None:
            return
        d = self.find_dialog
        o = d.options()
        if o["find"] == "" and not o["exact"]:
            d.set_status("Scrivi il testo da trovare (con «Corrispondenza esatta» puoi cercare le celle vuote).", True)
            return
        scope = self._engine_scope(o["filtered"])
        engine = self.engine
        d.set_busy(True)
        self.run_op(lambda: engine.count_matches(o["find"], o["case"], o["exact"], scope, o["replace"]),
                    lambda counts: self._confirm_replace_all(counts, o, scope), lambda: d.set_busy(False))

    def _confirm_replace_all(self, counts, o, scope):
        d = self.find_dialog
        d.set_busy(False)
        total = sum(counts.values())
        if not total:
            d.set_status("Nessuna occorrenza da sostituire nell'ambito.")
            return
        top = sorted(counts.items(), key=lambda kv: -kv[1])
        detail = ", ".join(f"{self._short(c, 20)}: {fmt_int(n)}" for c, n in top[:6]) + ("…" if len(top) > 6 else "")
        what = f"«{o['find']}»" if o["find"] else "le celle vuote"
        if not confirm(
                self, "Sostituisci tutto", f"Sostituire {what} con «{o['replace']}» in {fmt_int(total)} celle?",
                f"{self.scope_text(o['filtered']).splitlines()[0]}\n\nPer colonna: {detail}\n\n"
                "Puoi annullare l'operazione con «Annulla» (Ctrl+Z). " + self._persist_note(),
                yes="Sostituisci tutto", icon=QMessageBox.Question):
            d.set_status("Sostituzione annullata.")
            return
        engine = self.engine
        d.set_busy(True)
        self.run_op(lambda: engine.replace_all(o["find"], o["replace"], o["case"], o["exact"], scope),
                    self._replace_all_done, lambda: d.set_busy(False))

    def _replace_all_done(self, report):
        d = self.find_dialog
        d.set_busy(False)
        text = f"{fmt_int(report['cells'])} celle modificate in {len(report['columns'])} colonne."
        if report["converted"]:
            text += " Colonne diventate di testo: " + ", ".join(report["converted"]) + "."
        d.set_status(text)
        self._update_save_label()
        QMessageBox.information(self, "Sostituzione completata", text)
        self.refresh_view(refresh_count=True)
        self.dataChanged.emit(self)

    # ---- formattazione condizionale
    def _update_cf_button(self):
        n = sum(1 for r in self.cf_rules if r.get("enabled", True))
        self.cf_button.setText(f"Formattazione condizionale… ({n})" if n else "Formattazione condizionale…")

    def open_cf(self):
        if not self.columns:
            return
        dialog = CfDialog(self, self.cf_rules, list(self.columns), dict(self.engine.dtypes))
        if dialog.exec() != QDialog.Accepted or dialog.result_rules is None:
            return
        self.cf_rules = dialog.result_rules
        self._update_cf_button()
        self.refresh_view()

    # ---- Annulla
    @staticmethod
    def _is_text_input(widget) -> bool:
        return isinstance(widget, (QLineEdit, QPlainTextEdit, QAbstractSpinBox, QTextEdit)) or (
            isinstance(widget, QComboBox) and widget.isEditable())

    def _update_undo_action(self):
        engine = self.engine
        can = bool(self.columns) and engine.can_undo()
        self.undo_action.setEnabled(can)
        self.undo_action.setText("Annulla " + engine.undo_label() + "\t" + SC.hint("Annulla") if can else "Annulla\t" + SC.hint("Annulla"))
        self.copy_action.setEnabled(bool(self.columns))
        self.paste_action.setEnabled(bool(self.columns))

    def _undo_shortcut(self):
        if self._is_text_input(QApplication.focusWidget()):
            return                                  # nei campi di testo vale l'annulla del campo
        self.undo()

    def undo(self):
        """Annulla l'ultima operazione sul dataset (celle, sostituzioni, incolla, colonne, righe)."""
        if not self.columns:
            return
        self.table.commit_open_editor()
        engine = self.engine
        if not engine.can_undo():
            self.status.setText("Non c'è nulla da annullare.")
            return

        def work():
            result = engine.undo()
            return result, engine.count_total()

        self.run_op(work, self._undo_done)

    def _undo_done(self, payload):
        result, total = payload
        self._update_save_label()
        if result is None:
            self.status.setText("Non c'è nulla da annullare.")
            return
        ui = self._ui_undo.pop(result["state"], None)
        kind = result["kind"]
        if kind == "rename_column":
            self._renamed(*result["renamed"], total)
        elif kind in ("add_column", "delete_column", "delete_rows", "add_row"):
            if ui and result["restored_column"]:
                col = result["restored_column"]
                if ui.get("filter") is not None:
                    self.column_filters[col] = ui["filter"]
                if ui.get("sort") is not None:
                    self.sort = ui["sort"]
                if ui.get("hidden"):
                    self.hidden_columns.add(col)
                if ui.get("detail_hidden"):
                    self.detail_hidden_columns.add(col)
                self.cf_rules.extend(ui.get("rules", []))
            self._structure_changed(total, reset_offset=kind in ("delete_rows", "add_row"))
            self.apply_hidden_columns()
        else:
            self.refresh_view(refresh_count=True)
            self.dataChanged.emit(self)
        self._pending_note = f"Annullato: {result['label']}."
        self._update_cf_button()

    # ---- Incolla
    MAX_PASTE_CELLS = 1_000_000
    MAX_PASTE_CHARS = 64 * 1024 * 1024

    def paste(self):
        """Incolla il testo degli appunti (celle separate da tabulazioni) a partire dalla cella attiva."""
        if not self.columns:
            return
        text = QGuiApplication.clipboard().text()
        if not text:
            self.status.setText("Incolla: gli appunti non contengono testo.")
            return
        if len(text) > self.MAX_PASTE_CHARS:
            QMessageBox.information(self, "Incolla", "Il contenuto degli appunti è troppo grande per essere incollato "
                                                     f"({fmt_int(len(text))} caratteri; massimo {fmt_int(self.MAX_PASTE_CHARS)}).")
            return
        grid = parse_tsv(text)
        cells = sum(len(r) for r in grid)
        if not cells:
            self.status.setText("Incolla: gli appunti non contengono celle.")
            return
        if cells > self.MAX_PASTE_CELLS:
            QMessageBox.information(self, "Incolla", f"Gli appunti contengono {fmt_int(cells)} celle: il massimo incollabile "
                                                     f"in una volta è {fmt_int(self.MAX_PASTE_CELLS)}.")
            return
        t = self.table
        cur = t.currentIndex()
        if not cur.isValid() or cur.row() >= len(self.visible_row_ids):
            QMessageBox.information(self, "Incolla", "Seleziona la cella da cui iniziare e riprova.")
            return
        self.table.commit_open_editor()
        engine = self.engine
        visible = [c for c in range(len(self.columns)) if not t.isColumnHidden(c)]
        selected = {(i.row(), i.column()) for i in t.selectedIndexes() if not t.isColumnHidden(i.column())}
        single = len(grid) == 1 and len(grid[0]) == 1
        if single and len(selected) > 1:
            # un solo valore e più celle selezionate: il valore riempie tutte le celle selezionate
            value = grid[0][0]
            fixed = [(self.visible_row_ids[r], self.columns[c], value) for r, c in sorted(selected) if r < len(self.visible_row_ids)]
            job = lambda: engine.set_cells(fixed, "incolla")
            n_cells = len(fixed)
        else:
            start = self.offset + cur.row()
            if cur.column() not in visible:
                QMessageBox.information(self, "Incolla", "La cella attiva è in una colonna nascosta.")
                return
            first = visible.index(cur.column())
            height, width = len(grid), max(len(r) for r in grid)
            if start + height > self._filtered_count:
                QMessageBox.information(
                    self, "Incolla non possibile",
                    f"Gli appunti hanno {fmt_int(height)} righe ma da questa cella ne restano solo "
                    f"{fmt_int(self._filtered_count - start)} nella tabella. Nulla è stato modificato: "
                    "aggiungi prima le righe necessarie o scegli una cella più in alto.")
                return
            if first + width > len(visible):
                QMessageBox.information(
                    self, "Incolla non possibile",
                    f"Gli appunti hanno {width} colonne ma da questa cella ne restano solo {len(visible) - first} "
                    "(le colonne nascoste sono saltate). Nulla è stato modificato.")
                return
            names = [self.columns[visible[first + j]] for j in range(width)]
            search, filters, order = self.search_input.text(), self.current_column_filters(), self.sort

            def job():
                rids = engine.view_rids(start, height, search, filters, order)
                if len(rids) < height:
                    raise ImportProblem("Le righe della tabella sono cambiate: riprova a incollare.", fixable=False)
                return engine.set_cells([(rids[i], names[j], v) for i, row in enumerate(grid) for j, v in enumerate(row)], "incolla")

            n_cells = cells
        self.status.setText("Incolla in corso…")
        self.run_op(job, lambda result: self._paste_done(result, n_cells), self._edit_failed)

    def _paste_done(self, result, n_cells):
        self._update_save_label()
        changed = result["changed"]
        text = f"Incollate {fmt_int(n_cells)} celle ({fmt_int(changed)} modificate)."
        if result["converted"]:
            text += " Colonne diventate di testo: " + ", ".join(result["converted"]) + "."
        self.refresh_view(refresh_count=True)
        if changed:
            self.dataChanged.emit(self)
        self._pending_note = text

    # ---- navigazione veloce stile Excel
    def _table_command(self, command):
        if not self.columns or not self.table.currentIndex().isValid():
            return
        if command[0] == "paste":
            self.paste()
        elif command[0] == "jump":
            self._jump(command[1], command[2], command[3])
        elif command[0] == "corner":
            self._corner(command[1], command[2])

    def _enter_committed(self, row: int, col: int):
        """Dopo Invio in una cella: passa alla cella sotto (anche nella pagina successiva); all'ultima riga resta ferma."""
        pos = self.offset + row + 1
        if pos < self._filtered_count and col in self._visible_columns():
            self._move_to(pos, col, False)

    def _visible_columns(self) -> list[int]:
        return [c for c in range(self.table.columnCount()) if not self.table.isColumnHidden(c)]

    def _selection_anchor(self) -> tuple[int, int]:
        """Angolo opposto alla cella attiva nella selezione (la cella attiva è l'angolo che si muove)."""
        t = self.table
        cur = t.currentIndex()
        ranges = list(t.selectionModel().selection())
        for rng in reversed(ranges):
            if rng.contains(cur):
                return (rng.bottom() if cur.row() == rng.top() else rng.top(),
                        rng.right() if cur.column() == rng.left() else rng.left())
        return cur.row(), cur.column()

    def _jump(self, dr: int, dc: int, extend: bool):
        t = self.table
        cur = t.currentIndex()
        row, col = cur.row(), cur.column()
        visible = self._visible_columns()
        if col not in visible:
            return
        if dc:
            def empty(c):
                item = t.item(row, c)
                return item is None or item.text() == ""
            i = visible.index(col)
            step = 1 if dc > 0 else -1
            last = len(visible) - 1 if dc > 0 else 0
            nb = i + step
            if i == last:
                target = i
            elif not empty(col) and not empty(visible[nb]):
                j = nb
                while j != last and not empty(visible[j + step]):
                    j += step
                target = j
            else:
                j = nb
                while j != last and empty(visible[j]):
                    j += step
                target = j
            self._move_to(self.offset + row, visible[target], extend)
            return
        engine, column = self.engine, self.columns[col]
        pos = self.offset + row
        search, filters, order = self.search_input.text(), self.current_column_filters(), self.sort
        self.run_op(lambda: engine.nav_vertical(column, pos, dr, search, filters, order),
                    lambda res: self._move_to(res["target"], col, extend))

    def _corner(self, end: bool, extend: bool):
        visible = self._visible_columns()
        if not visible:
            return
        pos = max(0, self._filtered_count - 1) if end else 0
        self._move_to(pos, visible[-1] if end else visible[0], extend)

    def _move_to(self, pos: int, col: int, extend: bool):
        """Porta la cella attiva alla posizione ``pos`` (0-based nella vista filtrata/ordinata), cambiando pagina se serve."""
        if not self.columns or pos < 0:
            return
        t = self.table
        size = self.page_size
        page_offset = (pos // size) * size
        row = pos - page_offset
        anchor = None
        note = ""
        if extend:
            arow, acol = self._selection_anchor()
            apos = self.offset + arow
            if page_offset == self.offset:
                anchor = (arow, acol)
            else:
                anchor = (0 if apos < page_offset else size - 1, acol)
                note = ("La selezione resta limitata alla pagina visibile: il salto ha attraversato più pagine "
                        f"(righe {fmt_int(page_offset + 1)}–{fmt_int(page_offset + size)}).")
        self._pending_anchor = anchor
        self._pending_note = note
        if page_offset != self.offset:
            self.offset = page_offset
            self._pending_select = (row, col)
            self.load_page(refresh_count=False)
        else:
            self._pending_select = (row, col)
            self._apply_pending_select()
            self._show_pending_note()

    def _show_pending_note(self):
        if self._pending_note:
            note, self._pending_note = self._pending_note, ""
            QTimer.singleShot(0, lambda: self.status.setText(note))

    # ---- colonne e righe: aggiungere, rinominare, eliminare
    def _structure_changed(self, total: int | None = None, reset_offset: bool = False):
        """Riallinea interfaccia e stato dopo una modifica a colonne o righe."""
        engine = self.engine
        if total is not None:
            self.total_rows = total
        self.columns = list(engine.columns)
        self.column_filters = {c: s for c, s in self.column_filters.items() if c in self.columns}
        if self.sort and self.sort[0] not in self.columns:
            self.sort = None
        self.hidden_columns.intersection_update(self.columns)
        self.detail_hidden_columns.intersection_update(self.columns)
        self.cf_rules = [r for r in self.cf_rules if r["column"] in self.columns]
        if reset_offset:
            self.offset = 0
        self.columns_button.set_columns(self.columns, self.hidden_columns)
        self._refresh_meta()
        self._update_save_label()
        self._update_cf_button()
        self.rebuild_chips()
        self.update_filter_sql()
        self._scope_state = {"kind": "all"}
        self.table.sel_kind = None
        self.load_page(refresh_count=True)
        self.dataChanged.emit(self)

    def _target_column(self, column: str | None) -> str | None:
        if column is not None:
            return column
        t = self.table
        if t.sel_kind == "col":
            cols = t.selected_columns()
            if len(cols) == 1:
                return self.columns[cols[0]]
        cur = t.currentIndex()
        if cur.isValid() and cur.column() < len(self.columns):
            return self.columns[cur.column()]
        QMessageBox.information(self, "Seleziona una colonna", "Clicca sull'intestazione di una colonna e riprova.")
        return None

    def _reference_column(self) -> str | None:
        """Colonna dopo cui inserire: l'ultima delle colonne selezionate, altrimenti quella della cella corrente."""
        t = self.table
        if t.sel_kind == "col" and t.selected_columns():
            index = max(t.selected_columns())
        else:
            cur = t.currentIndex()
            index = cur.column() if cur.isValid() else -1
        return self.columns[index] if 0 <= index < len(self.columns) else None

    def _reference_rid(self) -> int | None:
        """Record dopo cui inserire: l'ultima riga selezionata, altrimenti quella della cella corrente."""
        t = self.table
        rows = t.selected_rows() if t.sel_kind == "row" else []
        row = max(rows) if rows else (t.currentRow() if t.currentIndex().isValid() else -1)
        return self.visible_row_ids[row] if 0 <= row < len(self.visible_row_ids) else None

    def add_column_prompt(self):
        if not self.columns:
            return
        after = self._reference_column()
        where = f"dopo la colonna «{after}»" if after else "in fondo"
        dialog = NameDialog(self, "Aggiungi colonna", "Nome della nuova colonna", list(self.columns), ok_text="Aggiungi",
                            note=f"La colonna viene aggiunta {where}, con tutti i valori vuoti.")
        if dialog.exec() != QDialog.Accepted:
            return
        name, engine = dialog.result_name, self.engine
        self.run_op(lambda: (engine.add_column(name, after), engine.count_total())[1],
                    lambda total: self._structure_changed(total))

    def rename_column_prompt(self, column: str | None = None):
        column = self._target_column(column)
        if column is None:
            return
        dialog = NameDialog(self, "Rinomina colonna", f"Nuovo nome per «{column}»", list(self.columns), initial=column,
                            ignore=column, ok_text="Rinomina")
        if dialog.exec() != QDialog.Accepted or dialog.result_name == column:
            return
        new, engine = dialog.result_name, self.engine
        self.run_op(lambda: (engine.rename_column(column, new), engine.count_total())[1],
                    lambda total: self._renamed(column, new, total))

    def _renamed(self, old: str, new: str, total: int):
        self.column_filters = {(new if c == old else c): s for c, s in self.column_filters.items()}
        if self.sort and self.sort[0] == old:
            self.sort = (new, self.sort[1])
        for group in (self.hidden_columns, self.detail_hidden_columns):
            if old in group:
                group.discard(old)
                group.add(new)
        for rule in self.cf_rules:
            if rule["column"] == old:
                rule["column"] = new
        if old in self.col_widths:
            self.col_widths[new] = self.col_widths.pop(old)
        used = self._sql_mentions(old)
        self._structure_changed(total)
        if used:
            self._custom_sql_obsolete(f"La query SQL personalizzata usa il vecchio nome «{old}» della colonna, ora «{new}»: "
                                      "potrebbe essere necessario aggiornarla.")

    def delete_column_prompt(self, column: str | None = None):
        column = self._target_column(column)
        if column is None:
            return
        if len(self.columns) <= 1:
            QMessageBox.information(self, "Impossibile eliminare", "Non si può eliminare l'unica colonna del dataset.")
            return
        deps = []
        if column in self.column_filters:
            deps.append("il filtro")
        if self.sort and self.sort[0] == column:
            deps.append("l'ordinamento")
        n_rules = sum(1 for r in self.cf_rules if r["column"] == column)
        if n_rules:
            deps.append(f"{n_rules} regole di formattazione")
        extra = ("\n\nVerranno rimossi anche: " + ", ".join(deps) + ".") if deps else ""
        if not confirm(self, "Elimina colonna", f"Eliminare la colonna «{column}»?",
                       "La colonna viene eliminata dal dataset gestito dall'applicazione. " + self._persist_note() + extra,
                       yes="Elimina colonna"):
            return
        engine = self.engine
        used = self._sql_mentions(column)
        snapshot = {"filter": self.column_filters.get(column),
                    "sort": self.sort if (self.sort and self.sort[0] == column) else None,
                    "hidden": column in self.hidden_columns, "detail_hidden": column in self.detail_hidden_columns,
                    "rules": [dict(r) for r in self.cf_rules if r["column"] == column]}

        def done(payload):
            total, state = payload
            self._ui_undo[state] = snapshot
            if len(self._ui_undo) > 80:
                self._ui_undo.pop(next(iter(self._ui_undo)))
            self._structure_changed(total)
            if used:
                self._custom_sql_obsolete(f"La query SQL personalizzata usa la colonna «{column}», che è stata eliminata: "
                                          "la query non funzionerà finché non la aggiorni.")

        self.run_op(lambda: (engine.delete_column(column), (engine.count_total(), engine.undo_state()))[1], done)

    def _selected_rids(self) -> list[int]:
        t = self.table
        rows = {r for rng in t.selectionModel().selection() for r in range(rng.top(), rng.bottom() + 1)}
        return [self.visible_row_ids[r] for r in sorted(rows) if r < len(self.visible_row_ids)]

    def delete_rows_prompt(self):
        rids = self._selected_rids()
        if not rids:
            QMessageBox.information(self, "Seleziona le righe", "Seleziona una o più righe (clic sul numero di riga) e riprova.")
            return
        if not confirm(self, "Elimina righe", f"Eliminare {fmt_int(len(rids))} {'riga' if len(rids) == 1 else 'righe'}?",
                       "Le righe vengono eliminate dal dataset gestito dall'applicazione e le successive vengono rinumerate. "
                       + self._persist_note(), yes="Elimina righe" if len(rids) != 1 else "Elimina riga"):
            return
        engine = self.engine
        self.run_op(lambda: (engine.delete_rows(rids), engine.count_total())[1],
                    lambda total: self._structure_changed(total, reset_offset=True))

    def add_row_prompt(self):
        if not self.columns:
            return
        engine, after = self.engine, self._reference_rid()
        self.run_op(lambda: (engine.add_row(after), engine.count_total()), self._row_added)

    def _row_added(self, result):
        rid, total = result
        self._structure_changed(total)
        if not self.has_active_filters() and self.sort is None:
            self.goto_cell(rid + 1, self.columns[0], force_reload=True)
        else:
            self.status.setText(f"Riga aggiunta come record {rid + 1}. Rimuovi filtri e ordine per vederla.")

    # ---- menu contestuali
    def _setup_context_menus(self):
        t = self.table
        h, v = t.horizontalHeader(), t.verticalHeader()
        for w in (h, v, t):
            w.setContextMenuPolicy(Qt.CustomContextMenu)
        h.customContextMenuRequested.connect(lambda pos: self._header_menu(h.logicalIndexAt(pos), h.mapToGlobal(pos)))
        v.customContextMenuRequested.connect(lambda pos: self._rows_menu(v.mapToGlobal(pos), v.logicalIndexAt(pos)))
        t.customContextMenuRequested.connect(
            lambda pos: self._rows_menu(t.viewport().mapToGlobal(pos), t.rowAt(pos.y())))

    def _header_menu(self, index: int, global_pos):
        if index < 0 or index >= len(self.columns):
            return
        column = self.columns[index]
        t = self.table
        if index not in t.selected_columns() or t.sel_kind != "col":
            t.selectColumn(index)
            t.sel_kind = "col"
        menu = QMenu(self)
        menu.addAction("Filtra e ordina…", lambda: self.filter_by_header_click(index))
        menu.addSeparator()
        menu.addAction("Rinomina colonna…", lambda: self.rename_column_prompt(column))
        menu.addAction("Aggiungi colonna…", self.add_column_prompt)
        menu.addAction("Elimina colonna…", lambda: self.delete_column_prompt(column))
        menu.addSeparator()
        menu.addAction("Nascondi colonna", lambda: self._hide_column(column))
        menu.addAction("Trova e sostituisci nella colonna…", lambda: self.open_find())
        menu.exec(global_pos)

    def _hide_column(self, column: str):
        self.hidden_columns.add(column)
        self.apply_hidden_columns()

    def _rows_menu(self, global_pos, row: int):
        t = self.table
        if 0 <= row < t.rowCount() and not t.selectionModel().isRowSelected(row, t.rootIndex()):
            if t.selectionModel().selection().isEmpty() or t.sel_kind != "row":
                t.selectRow(row)
                t.sel_kind = "row"
        menu = QMenu(self)
        menu.addAction("Dettaglio record (TAB)", self.show_selected_record)
        menu.addAction("Copia", t.copy_selection)
        menu.addSeparator()
        menu.addAction("Aggiungi riga dopo la selezione", self.add_row_prompt)
        menu.addAction("Elimina righe selezionate…", self.delete_rows_prompt)
        menu.addSeparator()
        menu.addAction("Trova e sostituisci…", lambda: self.open_find())
        menu.exec(global_pos)

    # ---- dettaglio (TAB dalla tabella principale)
    def show_selected_record(self):
        if not self.columns or not self.table.currentItem():
            return
        current = self.table.currentIndex()
        cells = {(i.row(), i.column()) for i in self.table.selectedIndexes() if not self.table.isColumnHidden(i.column())}
        if not cells and current.isValid():
            cells = {(current.row(), current.column())}
        rows = sorted({r for r, _ in cells}) or [self.table.currentRow()]
        if len(rows) > self.MAX_DETAIL_RECORDS:
            QMessageBox.information(self, "Troppi record selezionati",
                                    f"Il dettaglio mostra i primi {self.MAX_DETAIL_RECORDS} record della selezione.")
            rows = rows[: self.MAX_DETAIL_RECORDS]
        RecordDialog(self, rows, cells, (current.row(), current.column()) if current.isValid() else None).exec()
        # la selezione resta quella della tabella principale (o quella fatta nel dettaglio); si riporta il focus
        self.table.setFocus()
        self.table.selectionModel().setCurrentIndex(current, QItemSelectionModel.NoUpdate)

    def hidden_columns_idx(self) -> set[int]:
        return {c for c in range(self.table.columnCount()) if self.table.isColumnHidden(c)}

    def select_cells(self, cells):
        """Seleziona esattamente le celle indicate (riga, colonna) della tabella principale e porta la prima in vista."""
        t = self.table
        sel = QItemSelection()
        for r, c in cells:
            if 0 <= r < t.rowCount() and 0 <= c < t.columnCount():
                sel.select(t.model().index(r, c), t.model().index(r, c))
        t.selectionModel().select(sel, QItemSelectionModel.ClearAndSelect)
        t.sel_kind = None
        first = next(((r, c) for r, c in cells if 0 <= r < t.rowCount() and 0 <= c < t.columnCount()), None)
        if first:
            t.scrollTo(t.model().index(*first))

    # ---- salvataggio, SQL, esportazione
    # ---- salvataggio
    def save_edits(self):
        self.save()

    def save(self, then=None, closing: bool = False):
        """«Salva»: nel file corrente (stesso formato); un Excel originale non si sovrascrive, si crea una copia."""
        self._save_flow("save", then, closing)

    def save_as(self, then=None):
        """«Salva con nome»: cartella, nome, formato e opzioni CSV a scelta."""
        self._save_flow("save_as", then, False)

    @staticmethod
    def _finish(then, ok: bool):
        if then is not None:
            then(ok)

    def _save_notes(self) -> dict:
        meta = self.engine.excel_meta
        src_excel = self.engine.source_kind == "excel"
        if src_excel:
            csv_note = (f"Si esporta solo il foglio «{self.sheet}» come valori (le formule diventano il loro ultimo valore; "
                        "gli altri fogli e la formattazione non fanno parte di un CSV).")
            if meta and meta.kind == "xls":
                xlsx_note = ("Il formato .xls non può essere scritto: la copia è un .xlsx con i valori di tutti i fogli. "
                             "Formule, macro, grafici e formattazione dell'originale non vengono mantenuti.")
            else:
                xlsx_note = ("La copia conserva gli altri fogli e le formule non modificate. Grafici, immagini, macro e parte della "
                             "formattazione avanzata potrebbero andare persi; le celle modificate sono scritte come valori. "
                             "Il file originale non viene toccato.")
            if self.engine.structure_changed:
                xlsx_note += (f" Hai aggiunto, eliminato o rinominato colonne o righe: il foglio «{self.sheet}» viene riscritto con "
                              "i valori attuali (formule e formattazione di quel foglio non si mantengono).")
        else:
            csv_note = "Tutte le righe e tutte le colonne (anche quelle nascoste nella tabella); la formattazione condizionale non è inclusa."
            xlsx_note = ("Nuovo file Excel con un solo foglio «Dati»: testi come testo (anche «00123» e testi che iniziano con «=»), "
                         "numeri, date e vuoti. Nessuna formattazione né formula. Massimo 1.048.575 righe.")
        return {"csv": csv_note, "xlsx": xlsx_note}

    def _save_flow(self, mode: str, then=None, closing: bool = False):
        if not self.columns:
            return self._finish(then, False)
        if mode == "save" and not self.engine.has_changes():
            QMessageBox.information(self, "Nessuna modifica", "Non ci sono modifiche da salvare.")
            return self._finish(then, True)
        engine = self.engine
        protected = engine.protected_path()
        working_exists = os.path.isdir(os.path.dirname(os.path.abspath(self.path)))
        defaults = engine.export_defaults()
        defaults.update(self._export_prefs)
        title = {"save": "Salva", "save_as": "Salva con nome"}[mode]
        dialog = SaveDialog(
            self, title, mode=mode, working_path=self.path, working_format=self._work_format, protected_path=protected,
            default_format=self._work_format, defaults=defaults, notes=self._save_notes(),
            also_current=None, ok_text="Salva")
        if dialog.exec() != QDialog.Accepted:
            return self._finish(then, False)
        req = dict(dialog.request)
        in_place = dialog.in_place() and os.path.abspath(req["path"]) == os.path.abspath(self.path)
        if os.path.exists(req["path"]) and not in_place:
            same_working = (req["format"] == self._work_format and os.path.exists(self.path)
                            and os.path.samefile(req["path"], self.path))
            if not same_working:
                answer = QMessageBox.question(self, "Sostituire il file?",
                                              f"«{os.path.basename(req['path'])}» esiste già. Vuoi sostituirlo?",
                                              QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
                if answer != QMessageBox.Yes:
                    return self._finish(then, False)
        if req["format"] == "csv":
            self._export_prefs = {k: req[k] for k in ("delimiter", "encoding", "decimal")}
        jobs = []
        if req.pop("also_current", False):
            cur = {"format": "csv", "path": self.path, **self._export_prefs_for_current()}
            jobs.append(("current", cur))
        jobs.append(("main", req))
        self.status.setText("Salvataggio in corso…")

        def job():
            out = []
            for role, r in jobs:
                try:
                    out.append((role, r, _export(engine, r), None))
                except Exception as exc:                       # un errore non deve far perdere l'altro risultato
                    out.append((role, r, None, _problem_text(exc)))
            return out

        self.run_op(job, lambda results: self._save_done(mode, results, then, closing))

    def _export_prefs_for_current(self) -> dict:
        d = self.engine.export_defaults()
        o = self.import_options
        return {"delimiter": o.delimiter or d["delimiter"], "encoding": o.encoding or d["encoding"],
                "decimal": o.decimal or d["decimal"]}

    def _same_file(self, path: str, fmt: str) -> bool:
        return fmt == self._work_format and os.path.exists(path) and os.path.exists(self.path) and os.path.samefile(path, self.path)

    def _save_done(self, mode: str, results, then, closing: bool):
        self.status.setText("")
        lines: list[str] = []
        failed = False
        main_ok = False
        main = None
        for role, req, res, err in results:
            label = "File corrente" if role == "current" else "Salvataggio"
            if err:
                failed = True
                lines.append(f"✗ {label} NON salvato ({os.path.basename(req['path'])}): {err}")
                continue
            extra = []
            if res.get("as_text"):
                extra.append(f"{res['as_text']} valori non compatibili con il tipo della colonna scritti come testo")
            if res.get("rewritten"):
                extra.append("foglio riscritto: i valori delle colonne a cui non hai assegnato un tipo sono scritti come testo")
            if res.get("skipped"):
                extra.append(f"{res['skipped']} celle in aree unite non modificate")
            if res.get("stripped"):
                extra.append(f"{res['stripped']} valori con caratteri di controllo non ammessi da Excel: caratteri rimossi")
            lines.append(f"✓ {label}: {req['path']}" + (" (" + "; ".join(extra) + ")" if extra else ""))
            if role == "current":
                self.engine.mark_saved(res["version"])
            else:
                main_ok, main = True, (req, res)
        protected = self.engine.protected_path()
        if protected:
            lines.append(f"Il file Excel originale «{os.path.basename(protected)}» non è stato modificato.")
        new_file = False
        if main_ok and mode in ("save", "save_as"):
            req, res = main
            if self._same_file(req["path"], req["format"]):
                self.engine.mark_saved(res["version"])
            else:
                new_file = True
        self._update_save_label()
        if failed:
            ok_names = [l for l in lines if l.startswith("✓")]
            title = "Salvataggio parziale" if ok_names else "Salvataggio non riuscito"
            lines.append("Le modifiche restano nel programma: nulla è andato perso.")
            QMessageBox.critical(self, title, "\n".join(lines))
            return self._finish(then, False)
        if new_file and not closing:
            req, res = main
            box = QMessageBox(self)
            box.setIcon(QMessageBox.Question)
            box.setWindowTitle("Copia salvata")
            box.setText("\n".join(lines))
            box.setInformativeText("Su quale file vuoi continuare a lavorare?")
            on_copy = box.addButton("Continua sulla copia", QMessageBox.AcceptRole)
            on_current = box.addButton("Continua sul file corrente", QMessageBox.RejectRole)
            box.setDefaultButton(on_current)
            box.exec()
            if box.clickedButton() is on_copy:
                self._rebase(req, res["version"])
            else:
                self._show_saved_copy(req["path"])
                lines.append("")
            return self._finish(then, True)
        if new_file:                                           # chiusura/ricarica: i dati sono stati scritti nella nuova copia
            self._show_saved_copy(main[0]["path"])
            return self._finish(then, True)
        QMessageBox.information(self, "Salvataggio completato", "\n".join(lines))
        return self._finish(then, True)

    def _rebase(self, req: dict, version: int):
        """L'utente prosegue sulla copia: diventa il file di lavoro. L'Excel originale resta protetto e distinto."""
        fmt = req["format"]
        self.path = req["path"]
        self._work_format = fmt
        if fmt == "csv":
            self.kind = "csv"
            self.import_options = ImportOptions(delimiter=req["delimiter"], encoding=req["encoding"], header=True,
                                                decimal=req["decimal"] if req["decimal"] == "," else None)
        else:
            if self.kind == "csv":
                self.sheet, self.excel_header = "Dati", True
            self.kind = "excel"
        self.engine.mark_saved(version)
        self.saved_copy = ""
        self.copy_label.hide()
        self.file_label.setText(os.path.basename(self.path))
        self.file_label.setToolTip(self.path)
        self.file_meta.setToolTip(self.path)
        if os.path.abspath(self.original_path) != os.path.abspath(self.path):
            self.copy_label.setText(f"File di lavoro: {os.path.basename(self.path)}. "
                                    f"Originale: {os.path.basename(self.original_path)} (non modificato).")
            self.copy_label.setToolTip(self.original_path)
            self.copy_label.show()
        self._refresh_meta()
        self._update_save_label()
        self.titleChanged.emit(self)

    def _show_saved_copy(self, path: str):
        self.saved_copy = path
        if self.engine.protected_path() or os.path.abspath(path) != os.path.abspath(self.path):
            self.copy_label.setText(f"Originale: {os.path.basename(self.original_path)} (non modificato). "
                                    f"Copia salvata: {os.path.basename(path)}")
        self.copy_label.setToolTip(path)
        self.copy_label.show()

    def run_sql(self):
        sql = self.sql_input.toPlainText().strip()
        if not re.sub(r"--[^\n]*", "", sql).strip():
            QMessageBox.information(self, "Nessuna query da eseguire",
                                    "L'editor non contiene istruzioni SQL. Se nessuna colonna è visibile, mostrane almeno una "
                                    "dal menu «Colonne».")
            return
        self.run_worker(lambda: self.engine.run_query(sql), self._sql_done)

    def _sql_done(self, result):
        rows, columns = result
        self.fill_table(self.sql_result, columns, rows[:RESULT_LIMIT])
        if len(rows) > RESULT_LIMIT:
            QMessageBox.information(self, "Risultato limitato",
                                    f"La tabella mostra le prime {fmt_int(RESULT_LIMIT)} righe. Puoi esportare il risultato completo in CSV.")

    def export_sql(self):
        sql = self.sql_input.toPlainText().strip()
        if not sql:
            return
        path, _ = QFileDialog.getSaveFileName(self, "Esporta risultato", "risultato.csv", "CSV (*.csv)")
        if not path:
            return
        if not path.lower().endswith(".csv"):
            path += ".csv"
        blocked = self._open_file_paths()              # mai sopra un file aperto: si perderebbe l'originale
        self.run_worker(lambda: (self.engine.export_query(sql, path, blocked), path)[1],
                        lambda p: QMessageBox.information(self, "Esportazione completata", f"File creato:\n{p}"))

    def _open_file_paths(self) -> list[str]:
        window = self.window()
        tabs = window.csv_tabs() if hasattr(window, "csv_tabs") else [self]
        return [t.path for t in tabs if getattr(t, "path", "")]


# --------------------------------------------------------------------------- SQL su più CSV

ALIAS_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,29}$")


class GlobalSqlTab(AsyncMixin, QWidget):
    """SQL su più file: ogni file è una tabella con un alias breve (t1, t2…) modificabile."""

    toggleTopRequested = Signal()

    def __init__(self, get_tabs):
        super().__init__()
        self._async_init()
        self.get_tabs = get_tabs
        self.engine = GlobalEngine()
        self._touched = False
        self._programmatic = False
        self._selected = None

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 12, 0, 0)
        lay.setSpacing(8)
        outer = QSplitter(Qt.Horizontal)

        # --- pannello tabelle
        side = QWidget()
        side_lay = QVBoxLayout(side)
        side_lay.setContentsMargins(0, 0, 8, 0)
        side_lay.setSpacing(6)
        title = QLabel("Tabelle disponibili")
        title.setObjectName("fileName")
        hint = QLabel("Cambia l'alias nel campo a sinistra. Doppio clic su una colonna per inserirla nella query.")
        hint.setObjectName("hint")
        hint.setWordWrap(True)
        self.table_list = QListWidget()
        self.table_list.setObjectName("tableList")
        self.table_list.setVerticalScrollMode(QAbstractItemView.ScrollPerPixel)
        self.table_list.setMaximumHeight(230)
        self.table_list.currentRowChanged.connect(self._show_columns)
        self.columns_title = QLabel("")
        self.columns_title.setObjectName("hint")
        self.column_list = QListWidget()
        self.column_list.setVerticalScrollMode(QAbstractItemView.ScrollPerPixel)
        self.column_list.itemDoubleClicked.connect(self._insert_column)
        self.toggle_button = IconButton("up", "Nascondi barra superiore")
        self.toggle_button.clicked.connect(self.toggleTopRequested.emit)
        title_row = QHBoxLayout()
        title_row.setContentsMargins(0, 0, 4, 0)
        title_row.addWidget(title)
        title_row.addStretch(1)
        title_row.addWidget(self.toggle_button)         # freccia a destra, sulla riga di «Tabelle disponibili»
        side_lay.addLayout(title_row)
        side_lay.addWidget(hint)
        side_lay.addWidget(self.table_list)
        side_lay.addWidget(self.columns_title)
        side_lay.addWidget(self.column_list, 1)
        outer.addWidget(side)

        # --- editor e risultato
        splitter = QSplitter(Qt.Vertical)
        top = QWidget()
        top_lay = QVBoxLayout(top)
        top_lay.setContentsMargins(8, 0, 0, 0)
        top_lay.setSpacing(8)
        self.sql_input = QPlainTextEdit()
        self.sql_input.textChanged.connect(self._edited)
        top_lay.addWidget(self.sql_input, 1)
        row = QHBoxLayout()
        row.setSpacing(8)
        run = QPushButton("Esegui SQL")
        run.setObjectName("primary")
        run.clicked.connect(self.run_sql)
        export = QPushButton("Esporta risultato SQL…")
        export.clicked.connect(self.export_sql)
        row.addWidget(run)
        row.addWidget(export)
        row.addStretch(1)
        top_lay.addLayout(row)
        splitter.addWidget(top)
        self.result = CopyTable()
        style_table(self.result)
        self.result.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.result.horizontalHeader().setStretchLastSection(True)
        wrap = QWidget()
        wrap_lay = QVBoxLayout(wrap)
        wrap_lay.setContentsMargins(8, 0, 0, 0)
        wrap_lay.addWidget(self.result)
        splitter.addWidget(wrap)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 2)
        outer.addWidget(splitter)
        outer.setStretchFactor(0, 0)
        outer.setStretchFactor(1, 1)
        outer.setSizes([300, 900])
        lay.addWidget(outer, 1)
        lay.addWidget(self.progress)
        self.refresh_tables()

    def set_top_hidden(self, hidden: bool):
        self.toggle_button.set_kind("down" if hidden else "up")
        self.toggle_button.setToolTip("Mostra barra superiore" if hidden else "Nascondi barra superiore")

    def _edited(self):
        if not self._programmatic:
            self._touched = True

    def tables(self) -> dict:
        """alias -> motore del file. L'espressione con cui leggerlo (modifiche incluse) è calcolata nel thread di lavoro."""
        return {t.table_name: t.engine for t in self.get_tabs() if t.engine.view_expr}

    def refresh_tables(self):
        tabs = [t for t in self.get_tabs() if t.engine.view_expr]
        current = self._selected
        self.table_list.blockSignals(True)
        self.table_list.clear()
        for tab in tabs:
            item = QListWidgetItem()
            widget = QWidget()
            box = QHBoxLayout(widget)
            box.setContentsMargins(6, 4, 6, 4)
            box.setSpacing(8)
            alias = QLineEdit(tab.table_name)
            alias.setObjectName("alias")
            alias.setFixedWidth(64)
            alias.setToolTip("Alias usato nelle query")
            alias.editingFinished.connect(lambda t=tab, e=alias: self._rename(t, e))
            info = QVBoxLayout()
            info.setSpacing(0)
            name = QLabel(os.path.basename(tab.path) + (f" · {tab.sheet}" if tab.sheet else ""))
            name.setObjectName("tableFile")
            meta = QLabel(f"{fmt_int(tab.total_rows)} righe, {len(tab.columns)} colonne")
            meta.setObjectName("tableInfo")
            info.addWidget(name)
            info.addWidget(meta)
            box.addWidget(alias)
            box.addLayout(info, 1)
            item.setData(Qt.UserRole, tab)
            item.setSizeHint(widget.sizeHint().expandedTo(widget.minimumSizeHint()))
            self.table_list.addItem(item)
            self.table_list.setItemWidget(item, widget)
        self.table_list.blockSignals(False)
        if tabs:
            index = next((i for i, t in enumerate(tabs) if t is current), 0)
            self.table_list.setCurrentRow(index)
            self._show_columns(index)
        self._update_default_query()

    def _rename(self, tab, edit: QLineEdit):
        new = edit.text().strip()
        taken = {t.table_name.lower() for t in self.get_tabs() if t is not tab}
        if not ALIAS_RE.match(new) or new.lower() in taken:
            edit.setText(tab.table_name)
            QMessageBox.information(
                self, "Alias non valido",
                "Usa lettere, numeri e _ (massimo 30 caratteri, senza iniziare con un numero) e un nome non già usato.",
            )
            return
        if new != tab.table_name:
            tab.table_name = new
            self._show_columns(self.table_list.currentRow())
            self._update_default_query()

    def _show_columns(self, row: int):
        item = self.table_list.item(row) if row >= 0 else None
        self.column_list.clear()
        if item is None:
            self._selected = None
            self.columns_title.setText("")
            return
        tab = item.data(Qt.UserRole)
        self._selected = tab
        self.columns_title.setText(f"Colonne di {tab.table_name}")
        for col in tab.columns:
            self.column_list.addItem(col)

    def _insert_column(self, item):
        if self._selected is None:
            return
        self.sql_input.insertPlainText(f"{self._selected.table_name}.{sql_ident(item.text())}")
        self.sql_input.setFocus()

    def _update_default_query(self):
        if self._touched:
            return
        tabs = [t for t in self.get_tabs() if t.engine.view_expr]
        self._programmatic = True
        self.sql_input.setPlainText(f"SELECT *\nFROM {tabs[0].table_name}\nLIMIT 100;" if tabs else "")
        self._programmatic = False

    def run_sql(self):
        sql = self.sql_input.toPlainText().strip()
        tables = self.tables()
        if sql:
            self.run_worker(lambda: self.engine.run_query(tables, sql), self._done)

    def _done(self, result):
        rows, columns = result
        shown = rows[:RESULT_LIMIT]
        self.result.blockSignals(True)
        self.result.clear()
        self.result.setColumnCount(len(columns))
        self.result.setHorizontalHeaderLabels([str(c) for c in columns])
        self.result.setRowCount(len(shown))
        for r, row in enumerate(shown):
            for c, value in enumerate(row):
                self.result.setItem(r, c, QTableWidgetItem("" if value is None else str(value)))
        fit_columns(self.result)
        self.result.blockSignals(False)
        if len(rows) > RESULT_LIMIT:
            QMessageBox.information(self, "Risultato limitato",
                                    f"La tabella mostra le prime {fmt_int(RESULT_LIMIT)} righe. Puoi esportare il risultato completo in CSV.")

    def export_sql(self):
        sql = self.sql_input.toPlainText().strip()
        if not sql:
            return
        path, _ = QFileDialog.getSaveFileName(self, "Esporta risultato", "risultato.csv", "CSV (*.csv)")
        if not path:
            return
        if not path.lower().endswith(".csv"):
            path += ".csv"
        tables = self.tables()
        blocked = [t.path for t in self.get_tabs() if getattr(t, "path", "")]      # mai sopra un file aperto
        self.run_worker(lambda: (self.engine.export_query(tables, sql, path, blocked), path)[1],
                        lambda p: QMessageBox.information(self, "Esportazione completata", f"File creato:\n{p}"))

    def shutdown(self):
        try:
            self.engine.close()
        except Exception:
            pass


# --------------------------------------------------------------------------- finestra principale

def build_empty_state(on_open) -> QFrame:
    frame = QFrame()
    frame.setObjectName("emptyState")
    box = QVBoxLayout(frame)
    box.setAlignment(Qt.AlignCenter)
    box.setSpacing(6)
    title = QLabel("Trascina qui uno o più file CSV o Excel")
    title.setObjectName("emptyTitle")
    title.setAlignment(Qt.AlignCenter)
    body = QLabel("oppure aprili dal pulsante. I dati restano sul tuo computer.")
    body.setObjectName("emptyBody")
    body.setAlignment(Qt.AlignCenter)
    btn = QPushButton("Apri file…")
    btn.setObjectName("primary")
    btn.setCursor(Qt.PointingHandCursor)
    btn.setMaximumWidth(160)
    btn.clicked.connect(on_open)
    box.addStretch(1)
    box.addWidget(title)
    box.addWidget(body)
    box.addSpacing(10)
    box.addWidget(btn, 0, Qt.AlignCenter)
    box.addStretch(1)
    return frame


class MainWindow(QMainWindow):
    def __init__(self, theme_colors: dict | None = None):
        super().__init__()
        if theme_colors:
            set_colors(theme_colors)
        self.setWindowTitle("CSV Revolution")
        self.resize(1280, 800)
        self.setAcceptDrops(True)
        self.global_tab: GlobalSqlTab | None = None
        self.top_hidden = False

        root = QWidget()
        root.setObjectName("root")
        layout = QVBoxLayout(root)
        layout.setContentsMargins(20, 16, 20, 12)
        layout.setSpacing(12)

        # ---- barra superiore (comprimibile)
        self.top_bar = QWidget()
        top = QHBoxLayout(self.top_bar)
        top.setContentsMargins(0, 0, 0, 0)
        top.setSpacing(6)
        title = LogoLabel("CSV Revolution")
        title.setObjectName("appTitle")
        title.secretActivated.connect(self._open_hidden_games)
        self.prev_file_button = IconButton("left", "File precedente (Ctrl+PagSu)")
        self.next_file_button = IconButton("right", "File successivo (Ctrl+PagGiù)")
        self.prev_file_button.clicked.connect(lambda: self.step_file(-1))
        self.next_file_button.clicked.connect(lambda: self.step_file(+1))
        self.file_position = QLabel("")
        self.file_position.setObjectName("hint")
        self.open_button = QPushButton("Apri file…")
        self.open_button.setObjectName("primary")
        self.open_button.setCursor(Qt.PointingHandCursor)
        self.open_button.setToolTip("Apri uno o più file CSV o Excel")
        self.open_button.clicked.connect(self.open_csv)
        top.addWidget(title)
        top.addSpacing(14)
        top.addWidget(self.prev_file_button)
        top.addWidget(self.next_file_button)
        top.addWidget(self.file_position)
        top.addStretch(1)
        self.new_button = QPushButton("+ Nuovo CSV")
        self.new_button.setCursor(Qt.PointingHandCursor)
        self.new_button.setToolTip("Crea un nuovo file CSV (Ctrl+N)")
        self.new_button.clicked.connect(self.new_csv)
        top.addWidget(self.new_button)
        top.addWidget(self.open_button)
        layout.addWidget(self.top_bar)

        self.stack = QStackedWidget()
        self.empty_state = build_empty_state(self.open_csv)
        self._new_csv_folder = ""
        self.docs = QTabWidget()
        self.docs.setObjectName("docs")
        self.docs.setMovable(True)
        self.docs.currentChanged.connect(lambda _i: self.update_nav())
        self.docs.tabBar().tabMoved.connect(lambda *_: (self._pin_global(), self.update_nav()))
        self.stack.addWidget(self.empty_state)
        self.stack.addWidget(self.docs)
        layout.addWidget(self.stack, 1)
        self.setCentralWidget(root)

        for sequence, step in (("Ctrl+PgUp", -1), ("Ctrl+PgDown", +1)):
            QShortcut(QKeySequence(sequence), self, activated=lambda s=step: self.step_file(s))
        QShortcut(QKeySequence.New, self, activated=self.new_csv)
        QShortcut(QKeySequence("F1"), self, activated=lambda: SC.ShortcutsDialog(self).exec())
        self.update_nav()

    # ---- navigazione tra i file aperti
    def csv_tabs(self) -> list[CsvTab]:
        return [self.docs.widget(i) for i in range(self.docs.count()) if isinstance(self.docs.widget(i), CsvTab)]

    def _nav_state(self):
        tabs = self.csv_tabs()
        current = self.docs.currentWidget()
        index = tabs.index(current) if current in tabs else -1
        return tabs, index

    def update_nav(self):
        tabs, index = self._nav_state()
        n = len(tabs)
        self.prev_file_button.setEnabled(n > 0 and index != 0)
        self.next_file_button.setEnabled(n > 0 and 0 <= index < n - 1)
        if n == 0:
            self.file_position.setText("")
        elif index < 0:
            self.file_position.setText(f"SQL globale ({n} file)")
        else:
            self.file_position.setText(f"{index + 1} di {n}")

    def _open_hidden_games(self):
        """Finestra di attivazione dei giochi nascosti: il codice dei giochi non fa parte dell'applicazione."""
        try:
            from easter_eggs import open_activation
            self._hidden_dialog = open_activation(self)
        except Exception:                                  # se la cartella manca l'applicazione funziona come sempre
            LOG.exception("Giochi nascosti non disponibili")

    def step_file(self, delta: int):
        tabs, index = self._nav_state()
        if not tabs:
            return
        if index < 0:                       # dalla scheda SQL globale: «precedente» torna all'ultimo file
            target = len(tabs) - 1 if delta < 0 else None
        else:
            target = index + delta
        if target is not None and 0 <= target < len(tabs):
            self.docs.setCurrentWidget(tabs[target])

    # ---- barra superiore nascondibile
    def toggle_top(self):
        self.set_top_hidden(not self.top_hidden)

    def set_top_hidden(self, hidden: bool):
        self.top_hidden = hidden
        self.top_bar.setVisible(not hidden)
        self.docs.tabBar().setVisible(not hidden)
        for tab in self.csv_tabs():
            tab.set_top_hidden(hidden)
        if self.global_tab is not None:
            self.global_tab.set_top_hidden(hidden)

    # ---- apertura
    def open_csv(self):
        paths, _ = QFileDialog.getOpenFileNames(
            self, "Apri file", "",
            "File supportati (*.csv *.txt *.xlsx *.xls);;CSV (*.csv *.txt);;Excel (*.xlsx *.xls);;Tutti i file (*)",
        )
        for path in paths:
            self.open_path(path)

    def next_alias(self) -> str:
        taken = {t.table_name.lower() for t in self.csv_tabs()}
        n = 1
        while f"t{n}" in taken:
            n += 1
        return f"t{n}"

    @staticmethod
    def _same_file(a: str, b: str) -> bool:
        if os.path.normcase(os.path.abspath(a)) == os.path.normcase(os.path.abspath(b)):
            return True
        try:
            return os.path.exists(a) and os.path.exists(b) and os.path.samefile(a, b)
        except OSError:
            return False

    def find_open_tab(self, path: str, sheet: str | None = None):
        """La scheda già aperta per questo file (per Excel: per questo foglio), oppure None."""
        for tab in self.csv_tabs():
            if self._same_file(tab.original_path or tab.path, path) or self._same_file(tab.path, path):
                if tab.kind == "excel" and excel_kind(path):
                    if tab.sheet == sheet:
                        return tab
                elif tab.kind == "csv" and not excel_kind(path):
                    return tab
        return None

    def open_path(self, path: str, options: ImportOptions | None = None):
        sheet, header = None, True
        if excel_kind(path):
            try:
                sheets = list_sheets(path)
            except ExcelProblem as exc:
                QMessageBox.critical(self, "Impossibile aprire il file", f"{os.path.basename(path)}\n\n{exc}")
                return
            if not sheets:
                QMessageBox.critical(self, "Impossibile aprire il file", "Il file non contiene fogli di dati.")
                return
            if len(sheets) == 1:
                sheet = sheets[0][0]
            else:
                dialog = SheetDialog(self, os.path.basename(path), sheets)
                if dialog.exec() != QDialog.Accepted:
                    return
                sheet, header = dialog.selected_sheet(), dialog.header()
        existing = self.find_open_tab(path, sheet)
        if existing is not None:                    # già aperto: si torna alla sua scheda invece di duplicarlo
            self.docs.setCurrentWidget(existing)
            return
        tab = CsvTab()
        tab.table_name = self.next_alias()
        tab.openFailed.connect(lambda t: QTimer.singleShot(0, lambda: self.close_widget(t, force=True)))
        tab.opened.connect(lambda _t: self.update_global())
        tab.dataChanged.connect(lambda _t: self.update_global())
        tab.titleChanged.connect(self._tab_title_changed)
        tab.toggleTopRequested.connect(self.toggle_top)
        tab.set_top_hidden(self.top_hidden)
        label = os.path.basename(path) + (f" · {sheet}" if sheet else "")
        index = self.docs.count()
        index = self.docs.insertTab(index, tab, label)
        self.docs.setTabToolTip(index, path + (f"\nFoglio: {sheet}" if sheet else ""))
        self._attach_close(tab)
        self.docs.setCurrentIndex(index)
        self.stack.setCurrentWidget(self.docs)
        tab.open(path, options, sheet=sheet, header=header)
        self.update_global()
        self.update_nav()

    # ---- nuovo CSV (pulsante «+ Nuovo CSV» nella barra superiore, Ctrl+N)
    def new_csv(self):
        """Procedura guidata: crea il file (a blocchi, in un thread) e lo apre in una nuova scheda."""
        if getattr(self, "_creating", False):
            return
        dialog = NewCsvDialog(self, self._new_csv_folder)
        while True:
            if dialog.exec() != QDialog.Accepted or not dialog.spec:
                return
            spec = dialog.spec
            if self.find_open_tab(spec["path"]) is not None:
                # si resta nella finestra: basta cambiare nome o cartella
                dialog.error.setText("Questo file è aperto in una scheda: chiudila prima di sostituirlo, oppure scegli un altro nome.")
                dialog.error.show()
                continue
            if os.path.exists(spec["path"]):
                answer = QMessageBox.question(self, "Sostituire il file?",
                                              f"«{os.path.basename(spec['path'])}» esiste già. Vuoi sostituirlo?",
                                              QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
                if answer != QMessageBox.Yes:
                    continue                       # si torna alla finestra per cambiare nome o cartella
            break
        self._new_csv_folder = os.path.dirname(spec["path"])
        self._creating = True
        self.setCursor(Qt.BusyCursor)
        worker = Worker(lambda: create_csv(spec["path"], spec["columns"], spec["rows"], spec["delimiter"], spec["encoding"]))
        self._new_csv_worker = worker

        def done(result):
            self._creating = False
            self.unsetCursor()
            self.open_new_csv(spec)

        def failed(error):
            self._creating = False
            self.unsetCursor()
            text = error
            QMessageBox.critical(self, "Impossibile creare il file", text)

        worker.finished_data.connect(done)
        worker.failed.connect(failed)
        worker.start()

    def open_new_csv(self, spec: dict):
        """Apre il file appena creato con le impostazioni scelte (intestazione, delimitatore e codifica espliciti)."""
        self.open_path(spec["path"], ImportOptions(delimiter=spec["delimiter"], encoding=spec["encoding"], header=True))

    def _attach_close(self, widget):
        c = COLORS
        button = TabCloseButton(c['muted'], c['accent_soft'], c['accent'])
        button.clicked.connect(lambda: self.close_widget(widget))
        holder = QWidget()
        holder_lay = QHBoxLayout(holder)
        holder_lay.setContentsMargins(4, 0, 8, 0)
        holder_lay.addWidget(button)
        self.docs.tabBar().setTabButton(self.docs.indexOf(widget), QTabBar.RightSide, holder)

    # ---- chiusura
    def close_widget(self, widget, force: bool = False):
        index = self.docs.indexOf(widget)
        if index < 0 or widget is self.global_tab:
            return
        if force:
            if widget.worker is not None:
                widget.worker.wait(3000)
            self._remove_tab(widget)
            return
        if widget.is_busy():
            QMessageBox.information(self, "Operazione in corso", "Attendi il completamento dell'operazione corrente.")
            return
        widget.guard_unsaved("chiudere il file", lambda: self._remove_tab(widget), closing=True)

    UNSAVED_MARK = "• "

    @classmethod
    def tab_label(cls, tab) -> str:
        """Titolo della scheda: nome del file (e foglio), preceduto da un punto se ci sono modifiche non salvate."""
        sheet = f" · {tab.sheet}" if tab.kind == "excel" and tab.sheet else ""
        return (cls.UNSAVED_MARK if tab.has_unsaved() else "") + os.path.basename(tab.path) + sheet

    @staticmethod
    def tab_tooltip(tab) -> str:
        text = tab.path + (f"\nFoglio: {tab.sheet}" if tab.kind == "excel" and tab.sheet else "")
        return text + ("\nModifiche non salvate" if tab.has_unsaved() else "")

    def _tab_title_changed(self, tab):
        index = self.docs.indexOf(tab)
        if index >= 0:
            self.docs.setTabText(index, self.tab_label(tab))
            self.docs.setTabToolTip(index, self.tab_tooltip(tab))

    def _remove_tab(self, widget):
        index = self.docs.indexOf(widget)
        if index < 0:
            return
        self.docs.removeTab(index)
        widget.shutdown()
        widget.deleteLater()
        self.update_global()
        self.update_nav()
        if not self.csv_tabs():
            self.stack.setCurrentWidget(self.empty_state)

    def _pin_global(self):
        """«SQL globale» resta il primo tab: se viene spostato (o un altro tab gli passa davanti) torna in testa."""
        bar = self.docs.tabBar()
        if getattr(self, "_pinning", False):
            return
        self._pinning = True
        try:
            if self.global_tab is not None:
                index = self.docs.indexOf(self.global_tab)
                if index > 0:
                    bar.moveTab(index, 0)
        finally:
            self._pinning = False

    def update_global(self):
        count = len(self.csv_tabs())
        if count >= 2 and self.global_tab is None:
            self.global_tab = GlobalSqlTab(self.csv_tabs)
            self.global_tab.toggleTopRequested.connect(self.toggle_top)
            self.global_tab.set_top_hidden(self.top_hidden)
            index = self.docs.insertTab(0, self.global_tab, "SQL globale")       # sempre il primo tab, fisso, prima di «+»
            self.docs.tabBar().setTabButton(index, QTabBar.RightSide, None)
        elif count < 2 and self.global_tab is not None:
            self.docs.removeTab(self.docs.indexOf(self.global_tab))
            self.global_tab.shutdown()
            self.global_tab.deleteLater()
            self.global_tab = None
        if self.global_tab is not None:
            self.global_tab.refresh_tables()
        self.update_nav()

    # ---- file passati dal sistema («Apri con…», doppio clic, riga di comando)
    def open_paths(self, paths):
        for path in paths:
            if not os.path.isfile(path):
                continue
            self.open_path(os.path.abspath(path))      # se è già aperto porta in primo piano la sua scheda

    def handle_external(self, paths):
        self.open_paths(paths)
        self.bring_to_front()

    def bring_to_front(self):
        if self.isMinimized():
            self.showNormal()
        self.show()
        self.raise_()
        self.activateWindow()

    # ---- trascinamento
    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event):
        for url in event.mimeData().urls():
            if url.isLocalFile():
                self.open_path(url.toLocalFile())

    def closeEvent(self, event):
        unsaved = [t for t in self.csv_tabs() if t.has_unsaved()]
        if unsaved and not getattr(self, "_close_confirmed", False):
            event.ignore()
            self._close_chain(unsaved)
            return
        for tab in self.csv_tabs():
            tab.shutdown()
        if self.global_tab:
            self.global_tab.shutdown()
        event.accept()

    def _close_chain(self, tabs: list):
        """Chiede per ogni file modificato Salva / Non salvare / Annulla; chiude l'applicazione solo alla fine."""
        if not tabs:
            self._close_confirmed = True
            QTimer.singleShot(0, self.close)                   # fuori dal closeEvent corrente: Qt ignora close() annidati
            return
        tab = tabs.pop(0)
        if tab not in self.csv_tabs() or not tab.has_unsaved():
            return self._close_chain(tabs)
        self.docs.setCurrentWidget(tab)
        tab.guard_unsaved("chiudere l'applicazione", lambda: self._close_chain(tabs), closing=True)   # «Annulla»: resta aperta


class CsvApplication(QApplication):
    """Su macOS i file aperti dal Finder arrivano come evento FileOpen."""

    fileOpened = Signal(str)

    def event(self, event):
        if event.type() == QEvent.FileOpen:
            self.fileOpened.emit(event.file())
            return True
        return super().event(event)


def main():
    paths = [os.path.abspath(a) for a in sys.argv[1:] if os.path.isfile(a)]
    setup_logging()
    app = CsvApplication(sys.argv)
    app.setApplicationName("CSV Revolution")
    app.setApplicationDisplayName("CSV Revolution")
    app.setWindowIcon(app_icon())

    user = os.environ.get("USERNAME") or os.environ.get("USER") or "utente"
    instance = SingleInstance(f"csv-revolution-{user}")
    if instance.send(paths):
        return 0  # un'altra finestra è già aperta e ha ricevuto i file

    install_translations(app)
    colors = apply_theme(app)
    window = MainWindow(colors)
    window.setWindowIcon(app_icon())
    instance.pathsReceived.connect(window.handle_external)
    instance.listen()
    app.fileOpened.connect(lambda path: window.handle_external([path]))
    window.show()
    QTimer.singleShot(0, lambda: window.open_paths(paths))
    return app.exec()
