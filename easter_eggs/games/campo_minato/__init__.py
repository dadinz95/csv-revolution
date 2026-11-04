"""Campo Minato: si avvia digitando «campo_minato»."""
NAME = "campo_minato"
ALIASES: tuple[str, ...] = ()
DESCRIPTION = "Campo Minato in stile arcade retrò"


def launch():
    """Crea e mostra la finestra del gioco (PySide6 importato solo ora)."""
    from easter_eggs.games.campo_minato.game import launch as _launch
    return _launch()
