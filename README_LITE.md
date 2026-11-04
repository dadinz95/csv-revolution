# CSV Revolution — guida rapida

Applicazione desktop locale per aprire, filtrare, modificare e salvare file CSV ed Excel. I dati restano sul tuo computer.

## Requisiti

- **Python 3.11 o superiore** (provato con 3.13 su Linux).
- Pacchetti Python (`requirements.txt`): `duckdb`, `PySide6`, `openpyxl` (file `.xlsx`), `xlrd` (file `.xls`, sola lettura).
- Per compilare un eseguibile (`requirements-dev.txt`): `pyinstaller`, `pillow` (crea le icone) e `xlwt` (serve solo ai test).
- Strumenti esterni, **solo se vuoi creare l'installer di Windows**: [Inno Setup 6](https://jrsoftware.org/isinfo.php). Non serve altro.

| | Windows | macOS | Linux |
|---|---|---|---|
| Avvio da sorgente | sì | sì | sì |
| Script di compilazione | `packaging\build.ps1` (PowerShell) | `packaging/build.sh` | `packaging/build.sh` |
| Risultato | `dist\CSV Revolution\CSV Revolution.exe` (+ installer con Inno Setup) | `dist/CSV Revolution.app` | `dist/CSV Revolution/csv-revolution` |
| «Apri con» per i `.csv` | creato dall'installer | registrato nel bundle `.app` | `packaging/linux/install.sh` |

## Avvio da sorgente

Script di avvio nella cartella principale del progetto (accanto a `requirements.txt` e `run_csv_reader.py`). Ognuno crea `.venv`, lo attiva, installa le dipendenze e avvia l'app. Estrai lo ZIP, apri il terminale nella cartella del progetto ed esegui:

| Sistema | Script | Comando |
|---|---|---|
| Windows (PowerShell) | `run.ps1` | `.\run.ps1` |
| macOS (Terminale) | `run.command` | `bash run.command` |
| Linux (Terminale) | `run.sh` | `bash run.sh` |

- **Windows:** lo script esegue per prima cosa `Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass`; se PowerShell non consente comunque l'esecuzione, verifica che quel comando sia stato eseguito correttamente.
- **macOS e Linux:** gli script si lanciano con `bash`, quindi non serve impostare il permesso di esecuzione; funzionano da qualunque cartella (`cd "$(dirname "$0")"`).
- Richiedono Python 3.11 o superiore e la connessione per scaricare i pacchetti la prima volta.

### Procedura a mano (equivalente agli script)

**Windows (PowerShell)**

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python run_csv_reader.py
```

**macOS / Linux**

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python run_csv_reader.py
```

Puoi passare uno o più file come argomento: `python run_csv_reader.py dati.csv`.

## Creare l'eseguibile (PyInstaller)

Lo strumento adottato è **PyInstaller**. Un eseguibile si compila **sul sistema operativo di destinazione**: quello creato su Linux non gira su Windows o macOS, e viceversa. Tutti i comandi vanno lanciati dalla **cartella principale del progetto**; il risultato è sempre nella cartella `dist/`.

**Windows (PowerShell)**

```powershell
.\packaging\build.ps1
```

Crea l'ambiente `.venv` se manca, installa i requisiti, genera le icone e lancia PyInstaller. Risultato: `dist\CSV Revolution\CSV Revolution.exe`. Se Inno Setup 6 è installato crea anche `dist\CSV-Revolution-Setup.exe`, che registra «Apri con» per i `.csv`.

**macOS / Linux**

```bash
chmod +x packaging/build.sh        # solo la prima volta, se serve
./packaging/build.sh
```

Risultato: `dist/CSV Revolution.app` su macOS, `dist/CSV Revolution/csv-revolution` su Linux. Su Linux, per aggiungere la voce di menu e «Apri con»: `./packaging/linux/install.sh`.

### A cosa servono i file in `packaging/`

- `csv_reader.spec` — la ricetta di PyInstaller (cosa includere, nome, icona, bundle macOS, versione). È usato da tutti e tre i sistemi; si lancia anche a mano con `python -m PyInstaller packaging/csv_reader.spec --noconfirm --distpath dist --workpath build`.
- `build.ps1` / `build.sh` — automatizzano ambiente virtuale, installazione dei requisiti, icone e compilazione.
- `make_icons.py` e `icons/` — creano e contengono le icone (`.ico`, `.icns`, `.png`).
- `windows/installer.iss` — script di Inno Setup per l'installer di Windows.
- `linux/csv-revolution.desktop` e `linux/install.sh` — voce di menu e «Apri con» su Linux.

## Verifiche fatte e non fatte

- Provato su **Linux**: installazione in un ambiente virtuale pulito con i comandi sopra, avvio da sorgente, compilazione con PyInstaller (`dist/CSV Revolution/csv-revolution`) e avvio dell'eseguibile risultante in modalità senza schermo.
- **Non provato** su Windows e macOS: i comandi corrispondono agli script presenti, ma la compilazione, l'installer e il bundle `.app` non sono stati eseguiti su quei sistemi.

## Test

```bash
python -m pip install -r requirements.txt -r requirements-dev.txt
python -m unittest tests.test_core tests.test_phase2_core tests.test_phase3_core tests.test_phase4_core
QT_QPA_PLATFORM=offscreen python -m unittest tests.test_ui tests.test_phase2_ui tests.test_phase3_ui tests.test_phase4_ui
```

Su Windows PowerShell la seconda riga diventa `$env:QT_QPA_PLATFORM="offscreen"; python -m unittest tests.test_ui ...`.
Le scorciatoie da tastiera sono elencate in **Menu → Scorciatoie da tastiera…** (tasto F1).


## Novità v0.20.0
- Dettaglio record: colonne il doppio più larghe, selezione esatta delle celle scelte, SPAZIO senza effetti.

## Novità v0.19.0
Giochi nascosti nella cartella `easter_eggs/` (si attivano dal logo). Il primo è Snake.

## Novità v0.18.0
Nel «Dettaglio record»: larghezza standard delle colonne con «Adatta larghezza al contenuto» e «Larghezza standard» nel menu «Colonne», e selezione sincronizzata con la tabella principale.

## Novità v0.17.0
Tutto si apre come testo (nessuna conversione automatica: `00123`, `1.50` e le date restano come nel file). Il pulsante «Tipi» riconosce e assegna Testo, Intero, Decimale, Data, Data e ora, Booleano, solo su richiesta e con «Annulla». Colonne alla stessa larghezza standard; nel menu «Colonne»: «Adatta larghezza al contenuto» e «Larghezza standard».

## Novità v0.16.0
Modifiche atomiche (se qualcosa fallisce nulla cambia), esportazione SQL che non sovrascrive mai i file aperti, vuoto = NULL nei filtri e nell'ordinamento, nessun arrotondamento silenzioso dei decimali, un file già aperto riporta alla sua scheda, «• » sulle schede con modifiche non salvate, Tab apre il dettaglio del record. Dettagli in `README.md`.
