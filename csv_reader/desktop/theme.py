"""Tema grafico dell'applicazione: palette chiara/scura e foglio di stile Qt."""
from __future__ import annotations

import os
import sys

from PySide6.QtCore import QEvent, QObject, Qt
from PySide6.QtGui import QFont, QFontDatabase, QPalette
from PySide6.QtWidgets import QApplication, QComboBox, QDialog

LIGHT = {
    "bg": "#F3F5F8",        # sfondo finestra
    "surface": "#FFFFFF",   # tabelle, campi
    "surface2": "#EAEEF4",  # intestazioni, pulsanti secondari
    "border": "#D6DCE6",
    "text": "#18202E",
    "muted": "#667085",
    "accent": "#2447D6",
    "accent_hover": "#1B38AD",
    "accent_soft": "#E4E9FB",
    "on_accent": "#FFFFFF",
    "row_alt": "#F8F9FC",
    "select": "#DCE4FB",
    "danger": "#B42318",
    "warn": "#9A5B00",
}

DARK = {
    "bg": "#12151C",
    "surface": "#1A1E27",
    "surface2": "#242A36",
    "border": "#303847",
    "text": "#E6E9F0",
    "muted": "#8E98AB",
    "accent": "#7C95FF",
    "accent_hover": "#99ADFF",
    "accent_soft": "#262F57",
    "on_accent": "#0E1330",
    "row_alt": "#1E232D",
    "select": "#2B3769",
    "danger": "#FF8A80",
    "warn": "#FFC46B",
}


def is_dark(app: QApplication) -> bool:
    """Tema scuro se lo è il sistema; la variabile d'ambiente CSV_READER_THEME=light|dark lo forza."""
    forced = os.environ.get("CSV_READER_THEME", "").strip().lower()
    if forced in ("light", "dark"):
        return forced == "dark"
    return app.palette().color(QPalette.Window).lightness() < 128


def mono_family() -> str:
    families = set(QFontDatabase.families())
    for name in ("JetBrains Mono", "Cascadia Mono", "SF Mono", "Menlo", "Consolas",
                 "DejaVu Sans Mono", "Liberation Mono"):
        if name in families:
            return name
    return QFontDatabase.systemFont(QFontDatabase.FixedFont).family()


def sans_family() -> str:
    families = set(QFontDatabase.families())
    for name in ("Segoe UI Variable", "Segoe UI", "SF Pro Text", "Inter",
                 "Helvetica Neue", "Noto Sans", "DejaVu Sans"):
        if name in families:
            return name
    return QApplication.font().family()


def build_stylesheet(c: dict, sans: str, mono: str) -> str:
    return f"""
* {{ font-family: "{sans}"; font-size: 13px; color: {c['text']}; }}
QWidget {{ background: transparent; }}
QMainWindow, QDialog, QWidget#root, QWidget#findReplace {{ background-color: {c['bg']}; }}
QToolTip {{ background: {c['surface']}; color: {c['text']}; border: 1px solid {c['border']}; padding: 4px 6px; }}

/* Barra superiore */
QLabel#fileName {{ font-size: 15px; font-weight: 600; }}
QLabel#fileMeta, QLabel#hint {{ color: {c['muted']}; }}
QLabel#statusText {{ color: {c['muted']}; }}

/* Pulsanti */
QPushButton {{
    background: {c['surface2']}; border: 1px solid {c['border']};
    border-radius: 7px; padding: 6px 14px; font-weight: 500;
}}
QPushButton:hover {{ border-color: {c['accent']}; }}
QPushButton:pressed {{ background: {c['accent_soft']}; }}
QPushButton:disabled {{ color: {c['muted']}; background: transparent; border-color: {c['border']}; }}
QPushButton:focus {{ border: 1px solid {c['accent']}; }}
QPushButton#primary {{
    background: {c['accent']}; color: {c['on_accent']}; border: 1px solid {c['accent']};
}}
QPushButton#primary:hover {{ background: {c['accent_hover']}; border-color: {c['accent_hover']}; }}
QPushButton#primary:disabled {{ background: {c['surface2']}; color: {c['muted']}; border-color: {c['border']}; }}
QPushButton#ghost {{ background: transparent; border: 1px solid transparent; color: {c['accent']}; }}
QPushButton#ghost:hover {{ color: {c['accent_hover']}; background: {c['accent_soft']}; }}
QPushButton#ghost:disabled {{ color: {c['muted']}; background: transparent; }}
QPushButton#chip {{
    background: {c['accent_soft']}; color: {c['accent']}; border: 1px solid transparent;
    border-radius: 11px; padding: 2px 10px; font-size: 12px; font-weight: 500;
}}
QPushButton#chip:hover {{ border-color: {c['accent']}; }}

/* Campi */
QLineEdit, QSpinBox, QPlainTextEdit {{
    background: {c['surface']}; border: 1px solid {c['border']}; border-radius: 7px;
    padding: 6px 10px; selection-background-color: {c['select']}; selection-color: {c['text']};
}}
QLineEdit:focus, QSpinBox:focus, QPlainTextEdit:focus {{ border: 1px solid {c['accent']}; }}
QLineEdit#search {{ padding-left: 12px; }}
QPlainTextEdit {{ font-family: "{mono}"; font-size: 13px; padding: 8px 10px; }}
QPlainTextEdit[readOnly="true"] {{ background: {c['surface2']}; color: {c['muted']}; }}
QSpinBox {{ padding: 5px 10px 5px 10px; min-width: 96px; max-width: 110px; }}
QSpinBox::up-button, QSpinBox::down-button {{ width: 0; border: none; }}

/* Schede */
QTabWidget::pane {{ border: none; border-top: 1px solid {c['border']}; top: -1px; }}
QTabBar {{ background: transparent; }}
QTabBar::tab {{
    background: transparent; color: {c['muted']}; padding: 8px 18px 9px 18px; margin-right: 2px;
    border: none; border-bottom: 2px solid transparent; font-weight: 600;
}}
QTabBar::tab:hover {{ color: {c['text']}; }}
QTabBar::tab:selected {{ color: {c['accent']}; border-bottom: 2px solid {c['accent']}; }}

/* Tabelle */
QTableWidget {{
    background: {c['surface']}; alternate-background-color: {c['row_alt']};
    border: 1px solid {c['border']}; border-radius: 9px;
    gridline-color: {c['border']}; selection-background-color: {c['select']};
    selection-color: {c['text']}; outline: 0;
}}
QTableWidget::item {{ padding: 3px 8px; border: none; }}
QTableWidget::item:selected {{ background: {c['select']}; color: {c['text']}; }}
QTableWidget QLineEdit {{ border: 1px solid {c['accent']}; border-radius: 0; padding: 2px 6px; }}
QHeaderView {{ background: transparent; }}
QHeaderView::section {{
    background: {c['surface2']}; color: {c['text']}; font-weight: 600;
    padding: 7px 10px; border: none; border-right: 1px solid {c['border']};
    border-bottom: 1px solid {c['border']};
}}
QHeaderView::section:hover {{ background: {c['accent_soft']}; color: {c['accent']}; }}
QTableCornerButton::section {{ background: {c['surface2']}; border: none; border-bottom: 1px solid {c['border']}; }}


/* Schede dei file */
QLabel#appTitle {{ font-size: 16px; font-weight: 700; }}
QTabWidget#docs::pane {{ border: none; border-top: 1px solid {c['border']}; top: -1px; }}
QTabWidget#docs > QTabBar::tab {{
    background: {c['surface2']}; color: {c['muted']}; border: 1px solid {c['border']}; border-bottom: none;
    border-top-left-radius: 8px; border-top-right-radius: 8px; padding: 7px 14px 7px 14px; margin-right: 3px; font-weight: 500; min-height: 18px;
}}
QTabWidget#docs > QTabBar::tab:selected {{ background: {c['bg']}; color: {c['text']}; border-bottom: 1px solid {c['bg']}; }}
QTabWidget#docs > QTabBar::tab:hover {{ color: {c['text']}; }}
QToolButton#tabClose {{ background: transparent; border: none; padding: 0; margin: 0; }}

QToolButton {{
    background: {c['surface2']}; border: 1px solid {c['border']}; border-radius: 7px;
    padding: 6px 14px; font-weight: 500;
}}
QToolButton:hover {{ border-color: {c['accent']}; }}
QToolButton::menu-indicator {{ subcontrol-position: right center; subcontrol-origin: padding; right: 5px; }}
QMenu {{ background-color: {c['surface']}; color: {c['text']}; border: 1px solid {c['border']}; padding: 4px; }}
QMenu::item {{ background-color: transparent; color: {c['text']}; padding: 6px 22px 6px 14px; border-radius: 4px; }}
QMenu::item:selected {{ background-color: {c['accent_soft']}; color: {c['text']}; }}
QMenu::item:disabled {{ color: {c['muted']}; }}
QMenu::separator {{ height: 1px; background: {c['border']}; margin: 4px 8px; }}
QWidget#menuPanel {{ background-color: {c['surface']}; }}
QHeaderView#dataHeader::section:horizontal {{ padding-right: 34px; }}
QListWidget {{
    background: {c['surface']}; border: 1px solid {c['border']}; border-radius: 7px; padding: 3px; outline: 0;
}}
QListWidget::item {{ padding: 4px 6px; border-radius: 4px; }}
QListWidget::item:hover {{ background: {c['accent_soft']}; }}
QListWidget::item:selected {{ background: {c['select']}; color: {c['text']}; }}
QSplitter::handle {{ background: transparent; height: 8px; }}

QLineEdit#alias {{ font-family: "{mono}"; font-weight: 600; padding: 3px 6px; max-width: 72px; }}
QLabel#tableInfo {{ color: {c['muted']}; font-size: 12px; }}
QLabel#tableFile {{ font-weight: 500; }}
QListWidget#tableList::item {{ border-radius: 8px; padding: 2px; }}
QListWidget#tableList::item:selected {{ background: {c['accent_soft']}; }}

/* Avvisi e moduli */
QLabel#notice {{ color: {c['warn']}; }}
QLabel#error {{ color: {c['danger']}; }}
QComboBox {{
    background: {c['surface']}; border: 1px solid {c['border']}; border-radius: 7px; padding: 6px 10px;
}}
QComboBox::drop-down {{ border: none; width: 22px; }}
QComboBox:hover, QComboBox:focus {{ border-color: {c['accent']}; }}
QComboBox {{ combobox-popup: 0; }}
QComboBoxPrivateContainer {{ background-color: {c['surface']}; border: 1px solid {c['border']}; }}
QComboBox QAbstractItemView {{ background-color: {c['surface']}; color: {c['text']}; border: 1px solid {c['border']}; selection-background-color: {c['select']};
    selection-color: {c['text']}; outline: 0; padding: 3px;
}}
QCheckBox {{ spacing: 8px; }}
QCheckBox::indicator {{ width: 15px; height: 15px; border: 1px solid {c['muted']}; border-radius: 4px; background: {c['surface']}; }}
QCheckBox::indicator:hover {{ border-color: {c['accent']}; }}
QCheckBox::indicator:checked {{ background: {c['accent']}; border-color: {c['accent']}; image: url("data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' width='15' height='15'><path d='M3.5 8l3 3 5-6' fill='none' stroke='white' stroke-width='2'/></svg>"); }}
QToolButton#iconButton {{ background: transparent; border: none; padding: 0; }}
QHeaderView::section:vertical {{ padding: 0 8px; border-bottom: 1px solid {c['border']}; font-weight: 500; color: {c['muted']}; }}

/* Barre di scorrimento */
QScrollBar:vertical {{ background: transparent; width: 12px; margin: 2px; }}
QScrollBar:horizontal {{ background: transparent; height: 12px; margin: 2px; }}
QScrollBar::handle {{ background: {c['border']}; border-radius: 4px; min-height: 28px; min-width: 28px; }}
QScrollBar::handle:hover {{ background: {c['muted']}; }}
QScrollBar::add-line, QScrollBar::sub-line, QScrollBar::add-page, QScrollBar::sub-page {{ background: none; border: none; width: 0; height: 0; }}

/* Avanzamento */
QProgressBar {{ background: {c['surface2']}; border: none; border-radius: 1px; max-height: 3px; }}
QProgressBar::chunk {{ background: {c['accent']}; }}

/* Stato vuoto */
QFrame#emptyState {{ background: {c['surface']}; border: 1.5px dashed {c['border']}; border-radius: 12px; }}
QLabel#emptyTitle {{ font-size: 18px; font-weight: 600; }}
QLabel#emptyBody {{ color: {c['muted']}; }}

QMessageBox {{ background: {c['bg']}; }}
"""


class ComboPopupFixer(QObject):
    """Rende opaco il popup delle tendine: su alcune piattaforme il contenitore è una finestra translucida e,
    con i widget figli trasparenti del foglio di stile, il popup risulterebbe nero."""

    def __init__(self, colors: dict, parent=None):
        super().__init__(parent)
        self.colors = colors

    def eventFilter(self, obj, event):
        if event.type() == QEvent.Polish and (isinstance(obj, QDialog) or obj.objectName() == "findReplace"):
            pal = QPalette(obj.palette())
            pal.setColor(QPalette.Window, self.colors["bg"])
            obj.setPalette(pal)
            obj.setAutoFillBackground(True)
        if event.type() == QEvent.Polish and isinstance(obj, QComboBox) and not obj.property("_popupFixed"):
            obj.setProperty("_popupFixed", True)
            view = obj.view()
            pal = QPalette(view.palette())
            for role in (QPalette.Base, QPalette.Window):
                pal.setColor(role, self.colors["surface"])
            pal.setColor(QPalette.Text, self.colors["text"])
            pal.setColor(QPalette.WindowText, self.colors["text"])
            for w in (view, view.viewport(), view.window()):
                w.setPalette(pal)
                w.setAutoFillBackground(True)
            window = view.window()
            window.setAttribute(Qt.WA_TranslucentBackground, False)
            window.setWindowFlag(Qt.NoDropShadowWindowHint, True)
        return False


def install_translations(app: QApplication) -> bool:
    """Traduce in italiano i testi standard di Qt (pulsanti «Sì/No», finestre dei file, menu di modifica dei campi)."""
    from PySide6.QtCore import QLibraryInfo, QTranslator
    folders = [QLibraryInfo.path(QLibraryInfo.TranslationsPath)]
    try:
        import PySide6
        folders.append(os.path.join(os.path.dirname(PySide6.__file__), "Qt", "translations"))
    except Exception:
        pass
    if getattr(sys, "_MEIPASS", None):
        folders.append(os.path.join(sys._MEIPASS, "PySide6", "Qt", "translations"))
    for folder in folders:
        translator = QTranslator(app)
        if folder and translator.load("qtbase_it", folder):
            app.installTranslator(translator)
            app._translators = [translator]           # il riferimento tiene vivo il traduttore
            return True
    return False


def apply_theme(app: QApplication) -> dict:
    app.setStyle("Fusion")
    colors = DARK if is_dark(app) else LIGHT
    sans, mono = sans_family(), mono_family()
    font = QFont(sans)
    font.setPixelSize(13)
    app.setFont(font)
    pal = QPalette(app.palette())
    pal.setColor(QPalette.Button, colors["surface2"])
    pal.setColor(QPalette.ToolTipBase, colors["surface"])
    pal.setColor(QPalette.ToolTipText, colors["text"])
    pal.setColor(QPalette.Link, colors["accent"])
    pal.setColor(QPalette.Mid, colors["border"])
    pal.setColor(QPalette.Midlight, colors["surface2"])
    pal.setColor(QPalette.Dark, colors["border"])
    pal.setColor(QPalette.Light, colors["surface"])
    pal.setColor(QPalette.Disabled, QPalette.Text, colors["muted"])
    pal.setColor(QPalette.Disabled, QPalette.ButtonText, colors["muted"])
    pal.setColor(QPalette.Disabled, QPalette.WindowText, colors["muted"])
    pal.setColor(QPalette.Window, colors["bg"])
    pal.setColor(QPalette.Base, colors["surface"])
    pal.setColor(QPalette.AlternateBase, colors["row_alt"])
    pal.setColor(QPalette.Text, colors["text"])
    pal.setColor(QPalette.WindowText, colors["text"])
    pal.setColor(QPalette.ButtonText, colors["text"])
    pal.setColor(QPalette.Highlight, colors["select"])
    pal.setColor(QPalette.HighlightedText, colors["text"])
    pal.setColor(QPalette.PlaceholderText, colors["muted"])
    app.setPalette(pal)
    app.setStyleSheet(build_stylesheet(colors, sans, mono))
    fixer = ComboPopupFixer(colors, app)
    app.installEventFilter(fixer)
    app._combo_fixer = fixer
    return colors
