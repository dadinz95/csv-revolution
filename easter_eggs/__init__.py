"""Easter egg di CSV Revolution: sistema modulare, indipendente dall'applicazione.

* ``activation.py``: la finestra di attivazione (campo di testo, «Avvia», «Annulla»).
* ``manager.py``: riconosce il nome digitato e avvia il gioco corrispondente.
* ``games/<nome>/``: un gioco per cartella, ciascuno con la propria finestra (vedi ``README.md``).

L'applicazione principale conosce solo ``open_activation``; non sa nulla dei singoli giochi."""
from easter_eggs.manager import available, find, normalize, register, start  # noqa: F401

__all__ = ["available", "find", "normalize", "register", "start", "open_activation"]


def open_activation(parent=None):
    """Apre la finestra di attivazione (importata solo quando serve) e la restituisce."""
    from easter_eggs.activation import open_activation as _open
    return _open(parent)
