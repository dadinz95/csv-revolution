# CSV Revolution — descrizione del progetto

## Descrizione

CSV Revolution è un'applicazione desktop locale per aprire file CSV (anche molto grandi) e fogli Excel, cercarli, filtrarli, ordinarli, modificarli, interrogarli con SQL e salvarli. Non usa servizi in rete: i dati restano sul computer.

## Funzioni principali

- Apertura di CSV con rilevamento di delimitatore, codifica e intestazione, correggibili dalla finestra «Importazione…»; apertura di `.xlsx` e `.xls` (un foglio alla volta).
- Più file aperti in schede; tabella paginata con ricerca, filtri per colonna, ordinamento, colonne nascoste, dettaglio del record.
- Modifica delle celle, aggiunta/rinomina/eliminazione di colonne, aggiunta/eliminazione di righe, «Trova e sostituisci…», «Formattazione condizionale…» (colore di sfondo).
- **Annulla** (Ctrl/Cmd+Z) per modifiche di celle, sostituzioni, incolla e cambi di colonne e righe; **Copia/Incolla** compatibili con Excel; salti Ctrl/Cmd(+Maiusc)+Frecce.
- SQL per file (con query automatica che segue filtri e ordine) e **SQL globale** tra più file.
- Salvataggio come CSV o XLSX con «Salva» e «Salva con nome…»; un file Excel originale non viene mai sovrascritto (si crea una copia). Avvisi sulle modifiche non salvate.
- **Nuovo CSV** dal pulsante «+ Nuovo CSV» nella barra superiore: colonne, righe, cartella, nome, codifica e delimitatore a scelta.

- **Easter egg (v0.19.0):** cartella `easter_eggs/` indipendente dall'applicazione, con registro e un gioco per cartella (primo: Snake).
- **Tutto testo e tipi su richiesta (v0.17.0):** CSV ed Excel si aprono senza alcuna conversione (Excel: testo come lo mostra la cella); il pulsante «Tipi» riconosce e assegna Testo, Intero, Decimale, Data, Data e ora, Booleano in modo esplicito e annullabile; larghezza standard uniforme delle colonne con «Adatta» e «Larghezza standard» nel menu «Colonne».
- **Sicurezza dei dati (v0.16.0):** modifiche atomiche con rollback, esportazione SQL protetta e atomica, vuoto ≡ NULL, nessun arrotondamento silenzioso dei decimali, date in colonne di testo, ricerca di « », limite di circa 2 GB per le copie di «Annulla», file già aperto = scheda esistente, marcatore «• » per le modifiche non salvate, Tab per il dettaglio del record.

## Sistemi operativi

- **Da sorgente:** Windows, macOS e Linux, con Python 3.11 o superiore e i pacchetti di `requirements.txt`.
- **Eseguibile:** va compilato sul sistema di destinazione (PyInstaller non fa compilazione incrociata). Gli script sono presenti per i tre sistemi; solo la compilazione su **Linux** è stata provata.

## Tecnologie realmente usate

Python, **PySide6** (interfaccia Qt), **DuckDB** (lettura dei file, filtri, ordinamento, SQL, tabella di lavoro su disco), **openpyxl** (`.xlsx`), **xlrd** (`.xls`, solo lettura), **PyInstaller** (eseguibili), Pillow (icone), `unittest` (test). Inno Setup è opzionale, per l'installer di Windows.

## Architettura generale

- **Interfaccia (`csv_reader/desktop`)**: finestra principale e schede (`app.py`), finestre di dialogo (`tools_dialogs.py`), tema (`theme.py`), scorciatoie centralizzate (`shortcuts.py`). Le operazioni lente girano in thread separati, in coda per scheda.
- **Motore dati (`csv_reader/core/engine.py`)**: una connessione DuckDB per file. Finché non si modifica nulla le query leggono direttamente il file; alla prima modifica i dati sono copiati in una tabella su disco (file temporaneo) con un identificativo stabile per riga. Le modifiche annullabili conservano solo le celle cambiate (delta) in tabelle su disco.
- **Importazione/esportazione**: `engine.py` (CSV, scrittura atomica con file temporaneo), `excel.py` (lettura e copia di file Excel), `newcsv.py` (creazione di nuovi CSV a blocchi), `clipboard.py` (testo degli appunti), `conditions.py` (condizioni di filtri e formattazione).

## Punti di forza (verificati)

- Le pagine si leggono con query su DuckDB: non si carica l'intero file in memoria. Prova su Linux con un CSV da 1,5 milioni di righe: apertura 1,2 s, prima modifica circa 1,4 s; salto Ctrl+Freccia 0,6–1,0 s; incolla di 100.000 celle 0,2 s; annulla di una cancellazione di 333.334 righe 0,7 s.
- Il file originale CSV/Excel non è modificato se non lo si salva esplicitamente; per Excel non lo è mai (verificato con confronto di hash nei test).
- Scrittura dei file atomica (temporaneo + sostituzione) e conferme prima di sovrascrivere.
- Suite di test automatici (motore e interfaccia senza schermo): vedi `README.md`.

## Limiti

- **Formati:** CSV/TSV con delimitatori comuni e `.xlsx`/`.xls`. Un `.xls` si salva solo come `.xlsx`. Excel: formule, macro, grafici, immagini e formattazione non sono importati; salvando un Excel dopo cambi di struttura il foglio è riscritto con soli valori.
- **Memoria e dimensioni:** i CSV sono letti da DuckDB, i `.xlsx` in streaming, ma salvare un `.xlsx` rilegge il file con openpyxl (memoria alta per file grandi) e un `.xls` è caricato per intero. Non provato con file da 1 GB. XLSX: massimo 1.048.575 righe e 16.384 colonne. Nuovo CSV: massimo 5.000 colonne, 10 milioni di righe, 200 milioni di celle.
- **Annulla:** ultime 50 operazioni e circa 2 GB di copie (stima); non si annullano salvataggio, filtri, ordinamento, regole di formattazione e importazione. Nessun «Ripeti».
- **Copia/Incolla:** copia le celle selezionate della pagina visibile; incolla fino a 1.000.000 di celle, senza cambiare il numero di righe o colonne (se non entrano il comando è rifiutato). Valori non compatibili con il tipo di una colonna la trasformano in testo.
- **Selezione oltre la pagina:** Ctrl+Maiusc+Frecce attraverso più pagine seleziona solo la pagina di arrivo (con avviso).
- **Differenze fra sistemi operativi:** Ctrl equivale a Comando (⌘) su macOS; l'aspetto dei popup e dei temi è stato controllato solo su Linux (schermate senza display).
- Filtri e regole di formattazione non sono salvati nel file né fra le sessioni.

## Modalità di sviluppo

Il progetto è stato scritto in modalità **«vibe coded»**, cioè con un assistente di intelligenza artificiale che genera il codice su indicazioni della persona che lo guida, con verifiche automatiche e prove a campione. **Questo non è una garanzia di qualità.**

- **Verificato con test automatici** (Linux, interfaccia senza schermo): motore dati, modifiche, annulla, copia/incolla, salvataggio CSV/XLSX, esportazione, SQL, nuovo CSV, stabilità della finestra, navigazione da tastiera simulata.
- **Verificato a mano o con prove singole:** avvio da sorgente in un ambiente virtuale pulito, compilazione ed avvio dell'eseguibile su Linux, prove di velocità su CSV grandi, schermate in modalità senza schermo.
- **Non verificato:** Windows e macOS (avvio, aspetto, compilazione, installer), uso con mouse e finestre reali di un sistema grafico, file bloccati o disco pieno reali, file molto grandi (≥ 1 GB), accessibilità.
