"""Registro dei giochi nascosti: riconosce il nome digitato e avvia il gioco corrispondente.

Come aggiungere un gioco (nessuna modifica a questo file né all'applicazione):
  1. creare la cartella ``easter_eggs/games/<nome>/``;
  2. nel suo ``__init__.py`` definire ``NAME`` (nome da digitare), facoltativamente ``ALIASES`` (altri nomi),
     e ``launch()``, che crea, mostra e restituisce la finestra del gioco (un QWidget indipendente).
I giochi sono trovati automaticamente. In alternativa, ``register("nome", funzione_di_avvio)`` registra un gioco da codice.

I nomi si confrontano senza distinguere maiuscole/minuscole e ignorando spazi (e «-», «_»): «Snake», « SNAKE », «sn ake»
indicano lo stesso gioco."""
from __future__ import annotations

import importlib
import logging
import pkgutil
import re
from dataclasses import dataclass
from typing import Callable

LOG = logging.getLogger("easter_eggs")

_SEPARATORS = re.compile(r"[\s_\-]+")


def normalize(text: str | None) -> str:
    """Forma canonica di un nome: minuscolo, senza spazi, trattini e underscore."""
    return _SEPARATORS.sub("", (text or "").strip().lower())


@dataclass(frozen=True)
class Egg:
    name: str                                  # nome canonico (normalizzato)
    launch: Callable[[], object]               # crea e mostra la finestra del gioco, restituendola
    aliases: tuple[str, ...] = ()
    description: str = ""


_registry: dict[str, Egg] = {}                 # chiave: nome o alias normalizzato
_discovered = False
_windows: dict[int, object] = {}               # finestre aperte: il riferimento le tiene in vita finché non si chiudono


def register(name: str, launch: Callable[[], object], aliases: tuple[str, ...] = (), description: str = "") -> Egg:
    """Registra un gioco da codice. Un nome già usato da un altro gioco è un errore."""
    key = normalize(name)
    if not key:
        raise ValueError("Il nome di un easter egg non può essere vuoto.")
    egg = Egg(key, launch, tuple(normalize(a) for a in aliases if normalize(a)), description)
    for k in (key, *egg.aliases):
        if k in _registry and _registry[k].name != key:
            raise ValueError(f"Il nome «{k}» è già usato dall'easter egg «{_registry[k].name}».")
        _registry[k] = egg
    return egg


def discover(force: bool = False) -> None:
    """Cerca i giochi in ``easter_eggs/games/*`` (un pacchetto per gioco). Un gioco difettoso viene saltato."""
    global _discovered
    if _discovered and not force:
        return
    _discovered = True
    try:
        games = importlib.import_module("easter_eggs.games")
    except Exception:                                                     # pragma: no cover
        LOG.exception("Impossibile caricare easter_eggs.games")
        return
    names = [m.name for m in pkgutil.iter_modules(games.__path__) if m.ispkg]
    for known in getattr(games, "KNOWN_GAMES", ()):                       # rete di sicurezza (es. programma compilato)
        if known not in names:
            names.append(known)
    for pkg in sorted(names):
        try:
            module = importlib.import_module(f"easter_eggs.games.{pkg}")
            register(getattr(module, "NAME", pkg), module.launch, tuple(getattr(module, "ALIASES", ())),
                     getattr(module, "DESCRIPTION", ""))
        except Exception:
            LOG.exception("Easter egg «%s» non caricato", pkg)


def find(name: str | None) -> Egg | None:
    """Il gioco corrispondente al nome digitato, o None."""
    discover()
    return _registry.get(normalize(name))


def available() -> list[str]:
    """Nomi canonici dei giochi registrati (per test e diagnostica; l'interfaccia non li mostra)."""
    discover()
    return sorted({egg.name for egg in _registry.values()})


def bring_to_front(window) -> None:
    """Porta la finestra del gioco in primo piano e le dà il focus. Chi la apre è spesso una finestra modale che si sta
    chiudendo (la finestra di attivazione): subito dopo la chiusura l'applicazione riattiva la finestra principale e
    il gioco finirebbe dietro, quindi l'operazione si ripete a eventi ormai assestati."""
    def raise_now():
        try:
            if window.isMinimized():
                window.showNormal()
            window.raise_()
            window.activateWindow()
            window.setFocus()
        except RuntimeError:                                              # finestra già chiusa e distrutta
            pass
    raise_now()
    try:
        from PySide6.QtCore import QTimer
        for delay in (0, 150):
            QTimer.singleShot(delay, raise_now)
    except Exception:                                                     # pragma: no cover
        LOG.exception("Impossibile pianificare il primo piano")


def start(name: str | None):
    """Avvia il gioco ``name`` in una finestra indipendente. Ritorna la finestra, o None se il nome non è riconosciuto
    (o se il gioco non parte: l'errore va nel log, mai all'utente)."""
    egg = find(name)
    if egg is None:
        return None
    try:
        window = egg.launch()
    except Exception:
        LOG.exception("Easter egg «%s» non avviato", egg.name)
        return None
    if window is not None:
        bring_to_front(window)
        key = id(window)
        _windows[key] = window
        window.destroyed.connect(lambda *_a, k=key: _windows.pop(k, None))
    return window


def open_windows() -> list[object]:
    return list(_windows.values())
