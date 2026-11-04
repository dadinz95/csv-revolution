"""Pong: si avvia digitando «pong»."""
NAME = "pong"
ALIASES: tuple[str, ...] = ()
DESCRIPTION = "Pong in stile arcade retrò"


def launch():
    """Crea e mostra la finestra del gioco (PySide6 importato solo ora)."""
    from easter_eggs.games.pong.game import launch as _launch
    return _launch()
