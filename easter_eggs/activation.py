"""Finestra di attivazione: un campo di testo con «Avvia» e «Annulla», nient'altro.

«Avvia» (o Invio) avvia il gioco con quel nome; se non esiste la finestra si chiude senza dire nulla. «Annulla» ed Esc chiudono."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog, QHBoxLayout, QLineEdit, QPushButton, QVBoxLayout

from easter_eggs import manager


class ActivationDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("CSV Revolution")
        self.setAttribute(Qt.WA_DeleteOnClose)
        self.setMinimumWidth(320)
        self.game = None                       # finestra del gioco avviato (None se nessuno)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(18, 16, 18, 14)
        lay.setSpacing(12)
        self.input = QLineEdit()
        lay.addWidget(self.input)
        footer = QHBoxLayout()
        footer.addStretch(1)
        self.cancel_button = QPushButton("Annulla")
        self.cancel_button.clicked.connect(self.reject)
        self.start_button = QPushButton("Avvia")
        self.start_button.setObjectName("primary")
        self.start_button.setDefault(True)
        self.start_button.clicked.connect(self.launch)
        footer.addWidget(self.cancel_button)
        footer.addWidget(self.start_button)
        lay.addLayout(footer)
        self.input.returnPressed.connect(self.launch)       # Invio = «Avvia»
        self.input.setFocus()

    def launch(self):
        """Avvia il gioco corrispondente al testo (se esiste) e chiude la finestra in ogni caso."""
        self.game = manager.start(self.input.text())
        self.accept()


def open_activation(parent=None) -> ActivationDialog:
    dialog = ActivationDialog(parent)
    dialog.setModal(True)
    dialog.show()
    dialog.input.setFocus()
    return dialog
