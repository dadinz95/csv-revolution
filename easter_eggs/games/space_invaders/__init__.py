"""Space Invaders: si avvia digitando «space_invaders»."""
NAME = "space_invaders"
ALIASES: tuple[str, ...] = ()
DESCRIPTION = "Space Invaders in stile arcade retrò"


def launch():
    """Crea e mostra la finestra del gioco (PySide6 importato solo ora)."""
    from easter_eggs.games.space_invaders.game import launch as _launch
    return _launch()
