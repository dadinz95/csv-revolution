# Easter egg

Si attivano con 7 clic sulla prima «o» di «Revolution» nel logo in alto a sinistra: si apre una finestra con un campo di
testo; scrivendo il nome di un gioco e premendo «Avvia» (o Invio) il gioco parte in una finestra indipendente.
Maiuscole/minuscole e spazi non contano. Un nome sconosciuto chiude la finestra senza messaggi.

Giochi: `snake`.

## Aggiungere un gioco

1. Crea `easter_eggs/games/<nome>/` con un `__init__.py` che definisce:
   - `NAME = "<nome>"` (quello da digitare; facoltativi `ALIASES = ("altro nome",)` e `DESCRIPTION`);
   - `launch()` che crea, mostra e **restituisce** la finestra (un `QWidget` indipendente: niente importazioni da `csv_reader`).
2. Metti il resto del gioco negli altri moduli della cartella (come `snake/game.py`). Il gioco deve fermare i propri timer
   quando la finestra si chiude.
3. Fatto: i giochi sono trovati da soli. (Per i programmi compilati aggiungi il nome anche a `KNOWN_GAMES` in
   `games/__init__.py`; lo spec di PyInstaller include già tutti i moduli di `easter_eggs/`.)

In alternativa `easter_eggs.register("nome", funzione_di_avvio)` registra un gioco da codice.
