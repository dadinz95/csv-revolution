"""Snake: il primo easter egg. Si avvia digitando «snake»."""
NAME = "snake"
ALIASES: tuple[str, ...] = ()
DESCRIPTION = "Snake in stile arcade retrò"


def launch():
    """Crea e mostra la finestra del gioco (PySide6 importato solo ora)."""
    from easter_eggs.games.snake.game import launch as _launch
    return _launch()
