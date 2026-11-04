# CSV Revolution (locale) — v0.20.0

Applicazione desktop per Windows, macOS e Linux, con **Python, PySide6 e DuckDB**. Apre CSV anche molto grandi e
fogli Excel, senza caricarli per intero in memoria: i dati restano in DuckDB e si leggono una pagina alla volta.
Tutto il trattamento avviene sul tuo computer.

## Installazione e avvio
Script di avvio nella cartella principale del progetto (accanto a `requirements.txt` e `run_csv_reader.py`). Ognuno crea `.venv`, lo attiva, installa le dipendenze e avvia l'app. Estrai lo ZIP, apri il terminale nella cartella del progetto ed esegui:

| Sistema | Script | Comando |
|---|---|---|
| Windows (PowerShell) | `run.ps1` | `.\run.ps1` |
| macOS (Terminale) | `run.command` | `bash run.command` |
| Linux (Terminale) | `run.sh` | `bash run.sh` |

- **Windows:** lo script esegue per prima cosa `Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass`; se PowerShell non consente comunque l'esecuzione, verifica che quel comando sia stato eseguito correttamente.
- **macOS e Linux:** gli script si lanciano con `bash`, quindi non serve impostare il permesso di esecuzione; funzionano da qualunque cartella (`cd "$(dirname "$0")"`).
- Richiedono Python 3.11 o superiore e la connessione per scaricare i pacchetti la prima volta.

Procedura a mano:

Richiede Python 3.11 o superiore.

```powershell
# Windows (PowerShell)
py -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m csv_reader            # oppure: python run_csv_reader.py file.csv
```
```bash
# macOS / Linux
python3 -m venv .venv && source .venv/bin/activate
python -m pip install -r requirements.txt
python -m csv_reader [file.csv ...]
```
`-m` serve perché `csv_reader` è un pacchetto: va lanciato dalla cartella che lo contiene.

**Dipendenze:** `duckdb`, `PySide6` (interfaccia), `openpyxl` (.xlsx), `xlrd` (.xls). Solo per compilare e provare:
`pyinstaller`, `pillow` (icone), `xlwt` (crea .xls di prova nei test) — in `requirements-dev.txt`.

## Funzioni

### Importazione CSV (pulsante «Importazione…» accanto a «Salva»)
Ogni impostazione è **automatica** per default e correggibile a mano. Le scelte vengono applicate davvero al parser
e il file viene ricaricato con «Applica e ricarica».

| Impostazione | Scelte |
|---|---|
| Delimitatore | automatico, `;`, `,`, tabulazione, `\|`, personalizzato (1–4 caratteri) |
| Codifica | automatica, UTF-8, UTF-8 con BOM, Windows-1252, ISO-8859-1, UTF-16 |
| Intestazione | automatica, prima riga come intestazione, prima riga come dato |
| Separatore decimale | automatico, virgola, punto (solo predefinito per l'esportazione e per «Tipi»: all'apertura non converte nulla) |

Come funziona il rilevamento automatico:
- **Delimitatore, virgolette, intestazione:** sniffer di DuckDB.
- **Codifica:** BOM, poi UTF-16 senza BOM (byte nulli), poi si **verifica l'intero file** (a blocchi da 8 MB) come UTF-8;
  se non è valido si assume Windows-1252. Il rilevamento non è infallibile: un file Windows-1252 che per caso risulta UTF-8
  valido verrebbe letto come UTF-8 (raro, ma possibile). In caso di dubbio scegli la codifica a mano.
- **Tutto testo (v0.17.0):** all'apertura ogni valore resta testo, identico al file: `00123`, `1.50`, `1,5`, date, codici
  fiscali, IUV e identificativi non vengono convertiti. Niente rilevamento del separatore decimale.
- Se il rilevamento è sospetto (una sola colonna, righe iniziali scartate, prima riga che non sembra un'intestazione)
  compare un avviso sotto il nome del file con l'invito a correggere le impostazioni.

Errori comprensibili: file mancante, vuoto o non leggibile; codifica non valida; formato non riconosciuto; numero di
colonne incoerente (con il numero di riga); configurazione incompatibile (delimitatore e decimale entrambi `,`).
Se l'apertura fallisce puoi correggere le impostazioni dalla stessa finestra di avviso. Se a fallire è una **ricarica**,
il dataset precedente resta aperto e invariato.

Prima di ricaricare, se ci sono modifiche non salvate, l'app chiede conferma (la ricarica le scarta).

**Prestazioni e limiti:**
- DuckDB legge in streaming; l'apertura fa una scansione completa per il conteggio righe e il delimitatore.
- DuckDB legge nativamente UTF-8 e ISO-8859-1. **Windows-1252 e UTF-16 vengono prima convertiti in UTF-8 a blocchi in un
  file temporaneo** (stessa dimensione circa del file; si elimina alla chiusura della scheda o dell'app). Serve spazio libero su disco.
- Il salvataggio di un CSV scrive sempre UTF-8 con `,` come delimitatore.

### Excel (`.xlsx`, `.xls`)
«Apri file…» accetta CSV ed Excel (anche trascinando i file nella finestra, o «Apri con»). Con più fogli compare una
finestra per scegliere il foglio e se la prima riga è l'intestazione; «Importazione…» permette di cambiare foglio o intestazione.
- Il foglio diventa la stessa vista `csv_data` dei CSV: filtri, ricerca, modifica celle, colonne nascoste, SQL e SQL globale funzionano allo stesso modo.
- **Tutto testo, come lo mostra Excel (v0.17.0):** ogni cella è importata come testo con il formato della cella: `00123` con formato `00000`, `1.50` con `0.00`, la data con il suo formato (`05/01/2026`), `25.6%`, `TRUE`/`FALSE`. Il valore originale nel file non cambia. Convenzioni dei formati: decimale `.`, migliaia `,`, nomi di mesi e giorni in italiano; i formati non riconosciuti ricadono sul testo «generale» (data ISO, numero con al massimo 15 cifre). Salvando una copia Excel le celle non toccate restano quelle originali (numeri compresi); le celle modificate sono scritte come testo, salvo nelle colonne a cui hai assegnato un tipo.
- Nomi di colonna vuoti o duplicati vengono rinominati (`colonna_7`, `ID_2`); le celle vuote diventano `NULL`; le celle di errore (`#DIV/0!`) restano come testo; le righe vuote in mezzo ai dati sono conservate, quelle finali no.
- **Cosa viene importato:** solo i valori delle celle. **Formule** → ultimo valore calcolato salvato nel file (se il file non è mai stato calcolato, la cella risulta vuota). Macro, grafici, immagini e formattazione **non** sono importati né mostrati.
- `.xlsx` si legge in streaming; `.xls` (xlrd) carica il foglio in memoria (il formato arriva a 65.536 righe).

**Salvataggio (mai sull'originale):** per i file Excel «Salva» crea sempre una nuova copia (menu «Salva» → «Salva con nome…»).
- Chiede sempre destinazione e nome; rifiuta il file originale; se la copia esiste già chiede conferma prima di sostituirla.
- `.xlsx`: la copia conserva gli **altri fogli** e le **formule non modificate**; **grafici, immagini, macro e parte della formattazione avanzata possono andare persi** (limite di openpyxl). Le celle modificate sono scritte come valori, nel tipo della colonna (se non compatibile restano testo e l'app lo segnala). Le celle in aree unite non sono modificabili.
- `.xls`: il formato non è scrivibile; la copia è un **`.xlsx`** con i **valori di tutti i fogli**, senza formule, macro, grafici né formattazione.
- Prima del salvataggio l'app mostra questi limiti; dopo il salvataggio sotto il nome del file compare «Originale: … (non modificato). Copia salvata: …».
- Per salvare una copia di un `.xlsx` grande il file viene riletto per intero in memoria (openpyxl).
- Anche per i CSV il salvataggio rifiuta il file originale.

### Più file
Ogni file ha la sua scheda con dati, filtri, ricerca, SQL e modifiche **separati**. Con due o più file compare «SQL globale»
(alias `t1`, `t2`… modificabili, anche per i fogli Excel; l'SQL globale legge i file originali, senza le modifiche non salvate).
- **Frecce ‹ › in alto** per passare al file precedente/successivo, con tooltip, stato disabilitato agli estremi e posizione «2 di 5»; scorciatoie Ctrl+PagSu / Ctrl+PagGiù (funzionano anche con la barra nascosta). Dalla scheda «SQL globale», ‹ torna all'ultimo file.
- Aprire due volte lo stesso CSV porta in primo piano la scheda già aperta.

### Paginazione
In basso a sinistra: «Righe per pagina» (50–1.000.000), poi Precedente/Successiva; a destra la posizione. Cambiare le righe per pagina o pagina **non riconta** il dataset (il conteggio si rifà solo se cambiano ricerca o filtri) e resta sulla pagina che contiene la prima riga visibile. Il filtro per colonna resta attivo con il clic sull'intestazione (senza etichetta esplicativa); le etichette dei filtri compaiono solo quando ce ne sono.

### Ridimensionamento righe e colonne
- Selezionando più colonne (anche trascinando su più celle) e trascinando il bordo di una di esse, **tutte le selezionate** prendono la stessa larghezza. Per le righe vale lo stesso con l'altezza (il numero di riga a sinistra è il numero di record del file).
- **Doppio clic sul bordo**: adatta la colonna al contenuto; con più colonne selezionate ognuna si adatta al **proprio** contenuto. Le righe si adattano al testo (a capo).
- Limite: l'adattamento misura al massimo **2.000 righe** distribuite uniformemente nella pagina corrente e non supera 900 px, quindi non analizza mai l'intero dataset; un valore molto lungo non campionato può restare tagliato.
- Per selezionare colonne senza aprire il filtro: **Ctrl o Maiusc + clic** sull'intestazione.

### Barra superiore nascondibile
Il pulsante con la freccia ˄ / ˅ (a destra delle schede «Dati» e «SQL», e nella barra pulsanti dell'SQL globale) nasconde titolo, frecce dei file, schede dei file e intestazione del file («Nascondi barra superiore» / «Mostra barra superiore»). I widget non vengono ricreati: filtri, selezioni, pagina e modifiche restano.

### Dettaglio record (TAB)
Con una o più righe selezionate, **TAB** dalla tabella apre il dettaglio: nomi delle colonne a sinistra, valori a destra (una colonna per record, fino a 200), layout compatto, testi lunghi a capo e finestra dimensionata sul contenuto. Mostra **anche le colonne nascoste** nella tabella principale (il menu «Colonne» del dettaglio ha un elenco proprio). Le celle sono modificabili e le modifiche entrano subito nella sessione.
**TAB richiude il dettaglio** da qualunque controllo abbia il focus (conferma prima l'eventuale cella in modifica), il focus torna alla tabella «Dati» e la selezione resta. TAB viene intercettato solo mentre il dettaglio è aperto e attivo.

### Fase 2 (v0.12.1)
**Filtri avanzati.** Il clic sull'imbuto (o «Filtra e ordina…» dal tasto destro sull'intestazione) apre la finestra con campo valore e menu condizione (predefinita «Contiene»): Uguale a, Diverso da, Contiene, Non contiene, Inizia con, Termina con, Maggiore di, Minore di, Vuoto, Non vuoto. Più colonne = AND. Testo senza distinzione maiuscole/minuscole salvo la casella «Maiuscole/minuscole». Su colonne numeriche e data il confronto è numerico/cronologico (virgola decimale e `gg/mm/aaaa` accettati; un valore non valido è respinto nella finestra). «Vuoto» = NULL, stringa vuota o solo spazi; i NULL soddisfano solo Diverso da, Non contiene e Vuoto. I filtri sono eseguiti da DuckDB, restano cambiando pagina, ordine e scheda, il contatore «Righe x–y di N» è quello dei record filtrati, la numerazione a sinistra resta quella del file.

**Formattazione condizionale** (voce «Formattazione condizionale…» del menu «Menu»). Solo colore di sfondo. Regole per colonna + condizione (le stesse dei filtri) + colore; si creano, modificano, disattivano, eliminano e riordinano. Precedenza deterministica: a parità di cella vince la prima regola dell'elenco. Le regole sono valutate in SQL solo sulle righe della pagina visualizzata, quindi funzionano con paginazione, filtri, ordine, ricerca e colonne nascoste e non toccano dati, salvataggio, ricerca e SQL. Le celle selezionate mostrano il colore di selezione. Le regole seguono la rinomina della colonna e sono rimosse se la colonna viene eliminata. Non sono salvate nel file (vedi limiti).

**Trova e sostituisci** (Ctrl+F / Ctrl+H, F3 = trova successivo; finestra non modale). Opzioni: Maiuscole/minuscole, Corrispondenza esatta (cella intera), Solo record filtrati. «Trova» vuoto + esatta = celle vuote. Ambito dalla selezione, con precedenza colonne intere › righe intere › celle/intervallo › intero dataset (la finestra mostra l'ambito e avverte se la selezione è mista): colonne intere = tutti i record anche fuori pagina; righe intere = tutte le colonne, anche nascoste; una sola cella selezionata è solo il punto di partenza. «Trova successivo» segue l'ordine di visualizzazione (filtri e ordine inclusi), cambia pagina, seleziona la cella e ricomincia da capo a fine ricerca. «Sostituisci tutto» chiede conferma con il conteggio e poi riepiloga. I caratteri speciali sono trattati come testo letterale. Le sostituzioni modificano il dataset (non il file) e restano agganciate alla riga originale.

**Colonne e righe** (menu «Modifica», tasto destro su intestazione di colonna e di riga). Aggiungi colonna (inserita dopo la colonna selezionata, in fondo se non c'è selezione; nome non vuoto e non duplicato, valori vuoti/NULL), rinomina (stessi controlli, valori invariati, filtri/ordine/regole/SQL aggiornati; l'SQL scritto a mano che usa il vecchio nome non è riscritto ma segnalato con un avviso), elimina colonna (conferma; rimuove filtri, ordine e regole che la usano), aggiungi riga (dopo la riga selezionata, altrimenti in fondo), elimina righe selezionate (conferma; i numeri di record si ricalcolano). Ogni eliminazione avvisa che diventa definitiva nel file solo salvando; chiudere o ricaricare con modifiche non salvate chiede conferma.

**Come funziona (importante).** Alla prima modifica il dataset viene copiato in una tabella DuckDB su disco (file temporaneo, non in RAM) e da lì in poi tutta l'app lavora su quella; il CSV/Excel originale non viene mai toccato. Se scrivi in una colonna numerica/data un valore non compatibile, la colonna diventa testo (nessun valore perso). Il salvataggio crea sempre una nuova copia: CSV riscritto per intero; Excel: se la struttura è invariata si modifica una copia del file (formule e fogli conservati), se sono state aggiunte/eliminate/rinominate colonne o righe il foglio è riscritto con soli valori (l'avviso lo dice). L'SQL globale vede i dati modificati.

### Altre funzioni già presenti
Ricerca globale, filtri per colonna (testo e valori univoci con conteggi), menu «Colonne» con ricerca, modifica celle con salvataggio in copia, SQL per file (editor unico che segue i filtri), esportazione del risultato SQL, istanza unica, tema chiaro/scuro.
**Filtro e ordinamento per colonna:** un clic sul nome di una colonna la seleziona; l'icona a imbuto a destra del nome (piena e colorata se la colonna ha filtro o ordine, insieme al pallino ●) apre la finestra con «Ordina crescente/decrescente» (clic di nuovo per toglierlo), ricerca tra i valori univoci e filtro «contiene». L'ordine rispetta il tipo della colonna (numeri come numeri, date come date), i vuoti restano in fondo, a parità vale l'ordine del file; è combinabile con filtri e ricerca, vale anche per i file Excel, compare come chip «Ordine»; sopra la tabella i filtri sono riassunti con un chip per colonna («Cliente: 3 filtri»; oltre 4 colonne le altre stanno in «+N colonne», con elenco nel tooltip e nel menu) e nell'editor SQL (`ORDER BY`), e le modifiche restano agganciate alla riga originale. L'ordine vale per la vista (e la paginazione); salvando, il file mantiene l'ordine originale delle righe. Ctrl/Maiusc+clic sull'intestazione seleziona solo le colonne (poi, trascinando il bordo di una di esse, tutte prendono la stessa larghezza; doppio clic sul bordo adatta ciascuna al proprio contenuto).

## Fase 4 (v0.14.0)
Guida breve per l'uso e la compilazione: `README_LITE.md`; descrizione, architettura e limiti: `DESCRIZIONE_PROGETTO.md`.

- **Finestra stabile:** il testo lungo (nome del file, percorso, stato) non alza più la larghezza minima della finestra. Causa: le etichette dell'intestazione avevano come larghezza minima quella del testo e l'intera finestra doveva allargarsi; ora ignorano il testo nel calcolo del minimo. Nessuna dimensione fissa, nessun `adjustSize`/`resize` aggiunto.
- **Menu «Salva»:** un solo pulsante a tendina con «Salva» (Ctrl+S) e «Salva con nome…» (Ctrl+Maiusc+S). «Salva una copia» non esiste più; i suggerimenti dei vecchi pulsanti sono nelle finestre di salvataggio. «Formattazione» è ora «Formattazione condizionale…».
- **Annulla** (Ctrl/Cmd+Z, voce nel menu «Menu»): ultime 50 operazioni — modifica di cella, sostituzioni (una sola operazione per tutte le celle), incolla, aggiunta/rinomina/eliminazione di colonne (ripristina intestazione, dati, posizione, tipo e anche filtro, ordine, regole e visibilità), aggiunta/eliminazione di righe. Memorizza solo le celle cambiate (su disco). Ripristina lo stato «non salvato» e aggiorna tabella, conteggi, filtri, ordine e SQL automatica. Nei campi di testo e nell'editor SQL vale l'annulla del campo.
- **Copia/Incolla** (Ctrl/Cmd+C, Ctrl/Cmd+V): testo separato da tabulazioni con virgolette alla Excel. Incolla parte dalla cella attiva in ordine di vista (filtri e ordine), salta le colonne nascoste, può attraversare più pagine, è una sola operazione annullabile. Se i dati non entrano nella tabella nulla viene modificato e l'app lo spiega. Un solo valore con più celle selezionate le riempie tutte. Valori incompatibili con il tipo di colonna: la colonna diventa di testo (come nella modifica manuale), con avviso.
- **Navigazione:** Ctrl/Cmd+Frecce (bordo del blocco di dati come in Excel, calcolato in SQL, cambia pagina), Ctrl/Cmd+Maiusc+Frecce (estende la selezione; oltre la pagina resta limitata alla pagina di arrivo, con avviso), Maiusc+Frecce, Ctrl+Home/End. Le scorciatoie sono nel registro `csv_reader/desktop/shortcuts.py` e nella finestra «Scorciatoie da tastiera…» (menu «Menu», tasto F1).
- **Nuovo CSV:** pulsante «+ Nuovo CSV» nella barra superiore (Ctrl+N). Dalla v0.15.0 non c'è più la scheda «+». Colonne (≥1), righe (≥0, anche solo intestazione), cartella, nome (.csv aggiunto), codifica e delimitatore; conferma prima di sostituire un file; scrittura a blocchi e atomica; il file si apre in una nuova scheda.

## CSV Revolution (v0.15.0)
Nuovo nome visibile: **CSV Revolution** (finestra, barra superiore, eseguibile `CSV Revolution(.exe/.app)` / `csv-revolution` su Linux, installer `CSV-Revolution-Setup`, voce «Apri con», documentazione). Restano invariati i nomi interni (`csv_reader`, `run_csv_reader.py`, `csv_reader.spec`, icone).

- **Schermata iniziale:** al centro solo «Apri file…» e il trascinamento; «+ Nuovo CSV» è nella barra superiore, accanto a «Apri file…» (anche senza file aperti; Ctrl+N). La scheda «+» è stata rimossa; i tab dei file e «SQL globale» sono invariati.
- **Griglia:** linea verticale sottile tra le colonne (disegnata dal delegato della cella, quindi presente anche scorrendo in orizzontale e verticale; colore ricavato dal tema).
- **Barra del file:** «Importazione…» e «Salva» hanno la stessa altezza (calcolata dalla più alta delle due). «Righe per pagina»: padding interno 10 px a sinistra e a destra.
- **Ctrl+Freccia:** causa del difetto: la cella di arrivo veniva selezionata con `setCurrentCell()`, che lascia a Qt la scelta tra «sostituisci» e «aggiungi alla selezione» in base ai modificatori premuti in quel momento; con Ctrl premuto la cella di partenza restava selezionata. Ora si usano flag espliciti (`ClearAndSelect`), indipendenti dalla tastiera. Vale anche per i salti verso un'altra pagina e per «Trova».
- **Errori SQL:** all'utente arriva solo il messaggio dell'eccezione di DuckDB (tipo, suggerimenti «Did you mean», `LINE n:` e indicatore `^^^`), senza traceback, percorsi o righe di codice. Il traceback completo va nel log tecnico `csv_revolution.log` nella cartella temporanea del sistema (rotazione, 1 MB × 3). Vale per SQL di file, SQL globale e per ogni altra operazione in background.

**Ottimizzazioni (prova singola su Linux, CSV da 64 MB / 1,5 milioni di righe / 5 colonne, motore senza interfaccia):**

| Percorso | Prima | Dopo |
|---|---|---|
| Apertura del file | 4,43 s | 1,17 s |
| di cui rilevamento codifica (validazione UTF-8) | 2,95 s | 0,03 s |

- Rilevamento codifica: i blocchi di sola ASCII sono validati con un controllo in C senza costruire la stringa Python; il decoder incrementale si usa solo per i blocchi con caratteri non ASCII (o con un carattere spezzato a metà). Il risultato è identico.
- Tipi delle colonne: ricavati dallo stesso `DESCRIBE` che legge i nomi (prima: una query `LIMIT 0` e poi un secondo `DESCRIBE`, cioè un secondo rilevamento dei tipi sul file).
- Riempimento della tabella: un solo ridisegno invece di uno per cella (la pagina da 5.000 righe impiega già 0,17 s, quindi il guadagno è piccolo).
- Già a posto (misurato, nessuna modifica): pagina iniziale 0,37 s, pagina profonda 0,5 s, ordinamento 0,6 s (0,01 s dopo la prima modifica), prima modifica 1,4 s (copia su disco, fuori dal thread grafico) e poi istantanea, salvataggio CSV 0,4 s; il conteggio con filtri non viene ripetuto senza filtri attivi. Non si usano cache dei risultati. Limiti: l'apertura legge ancora il file due volte (rilevamento dei tipi e conteggio), perché i tipi richiedono la lettura dell'intero file; non provato con file da 1 GB.

- **Invio in una cella (v0.15.1):** durante la modifica di una cella, Invio salva il valore e passa alla cella sotto (anche nella pagina successiva, rispettando filtri e ordine); Esc annulla. Alla scomparsa della barra di avanzamento l'interfaccia non «salta» più: lo spazio della barra è sempre riservato.

## Correzioni di sicurezza dei dati e interfaccia (v0.16.0)

**Dati e logica**
- **Atomicità:** modifica di celle, sostituzioni, aggiunta/rinomina/eliminazione di colonne e righe e «Annulla» sono transazioni: se un passo fallisce, tabella, colonne e tipi tornano come prima. Dopo il COMMIT la vista dei dati viene ridefinita (finestra minima non atomica).
- **Sostituzioni reali:** «Sostituisci» conta e segna come modificate solo le celle il cui valore cambia davvero; il conteggio mostrato prima della conferma coincide con le celle modificate.
- **Esportazione SQL protetta:** è rifiutata verso qualunque file aperto (CSV o Excel) e scrive su un file temporaneo poi rinominato: la destinazione non è mai lasciata a metà.
- **Annulla:** al massimo 50 passi e circa 2 GB di copie di sicurezza (stima basata su righe × colonne; il passo più recente è sempre conservato).
- **Vuoto = NULL = «»:** in filtri, valori univoci, ordinamento (i vuoti vanno in fondo) e salti Ctrl+Freccia; «Vuoto» include anche i valori di soli spazi.
- **Numeri:** la virgola è sempre il separatore decimale; un valore che in una colonna intera verrebbe arrotondato (es. 1,5 in una colonna intera) non viene convertito (la condizione resta testuale).
- **Date in colonne di testo:** filtri e ordinamenti seguono i formati di `parse_date` (anche con ora); l'uguaglianza su colonne di testo resta testuale.
- **Spazi:** «contiene», «non contiene», «inizia con», «finisce con» accettano anche il solo spazio « ».
- **Concorrenza e ordine:** ogni lettura usa un cursore proprio (nessun errore «connessione occupata» tra esportazione e interfaccia); `preserve_insertion_order` è attivo per paginazione stabile.

**Interfaccia**
- **File già aperto:** riaprirlo porta in primo piano la scheda esistente (per Excel conta file + foglio); «Nuovo CSV» segnala l'errore nella finestra stessa.
- **Modifiche non salvate:** marcatore «• » nel titolo della scheda (non più «●» nell'intestazione) e tooltip su scheda e pulsante «Salva».
- **Conferme esplicite:** i pulsanti dei messaggi hanno testi chiari («Sostituisci tutto», «Elimina colonna», «Elimina riga(righe)», «Sostituisci»/«Mantieni»); Sì/No/Annulla di Qt sono tradotti in italiano (`qtbase_it.qm`, incluso nel pacchetto PyInstaller).
- **Tab:** apre il dettaglio del record (voce «Dettaglio record selezionati» nel menu Modifica e nelle scorciatoie).
- Dalla scheda SQL, Ctrl+F, Ctrl+H e F3 non fanno nulla; «Azzera filtri» è disabilitato se non c'è nulla da azzerare; la riga di stato si accorcia al centro con tooltip completo; pulsanti «ghost» e caselle combinate con aspetto uniforme.

**Test eseguiti per questa versione** (Linux, Python 3.13, DuckDB 2.0.0, PySide6, offscreen, `unittest`), tutti superati: `test_core` 18, `test_phase2_core` 39, `test_phase3_core` 13, `test_phase4_core` 17, `test_phase5_core` 29, `test_ui` 40, `test_phase2_ui` 37, `test_phase3_ui` 27, `test_phase4_ui` 51, `test_phase5_ui` 21. **Non eseguiti:** prove manuali su Windows e macOS (aspetto dei controlli, UI-009), compilazione PyInstaller di questa versione.

## Tipi di dato e larghezza delle colonne (v0.17.0)

**Apertura.** CSV e Excel si aprono con tutte le colonne di testo (`all_varchar`): nessuna conversione automatica di numeri, date, orari o booleani. L'apertura non è una modifica (nessun «Salva» attivo). Conseguenza: ordinamenti e confronti «maggiore/minore» su colonne di testo restano testuali finché non si assegna un tipo.

**Pulsante «Tipi»** (accanto a «Menu» e «Colonne»):
- «Riconosci e formatta le colonne…»: finestra con tipo attuale, tipo riconosciuto (con l'esito: «tutti i valori sono validi», «prevalentemente…», esempi non convertibili), «Usa i tipi riconosciuti», scelta manuale in «Imposta come» e, per i decimali, le cifre decimali.
- «Scegli il tipo delle colonne…» (la stessa finestra senza analisi automatica) e, per la colonna corrente, «Imposta il tipo» con Testo, Numero intero, Numero decimale, Data, Data e ora, Booleano; il tipo attuale è indicato anche nel suggerimento dell'intestazione.
- Riconoscimento (solo su richiesta, non modifica nulla): intero `123`, `-5`, `+7` (fino a 18 cifre); decimale `1.50`, `1,50` (la virgola è sempre un separatore decimale) e migliaia solo se inequivocabili (`1.234,56`, `1,234.56`, `1.234.567`; `1,234` e `1.234` sono decimali); data `aaaa-mm-gg` e `gg/mm/aaaa` (anche `-` e `.`, il giorno viene prima del mese); data e ora con `hh:mm[:ss]`; booleano true/false, vero/falso, sì/no, yes/no, 1/0. Identificatori con zeri iniziali (`00123`) o con più di 15 cifre non vengono mai proposti come numeri. Se almeno il 90% dei valori è valido il tipo è proposto come «parziale» (da scegliere a mano).
- Conversione: esplicita, atomica e annullabile (Ctrl+Z ripristina il testo originale identico, anche per i valori non convertibili). I valori non convertibili non bloccano l'operazione: prima della conversione una conferma dice quanti sono, con esempi, e che diventeranno vuoti; lo stesso per gli zeri iniziali che si perderebbero. Il decimale usa un DECIMAL con le cifre scelte (mai meno di quelle presenti nei dati: nessun arrotondamento); il CSV esporta le date nel formato del file. I filtri della colonna convertita vengono tolti (il loro significato dipende dal tipo).
- Salvataggio: le colonne senza tipo assegnato escono esattamente come sono; quelle tipizzate escono come numeri/date/booleani (Excel) o nel formato del file (CSV).

**Larghezza delle colonne.** Tutte le colonne della tabella dati hanno all'apertura la stessa larghezza standard (160 px), mai calcolata sul contenuto, quindi un valore lunghissimo non altera la vista. Il ridimensionamento manuale è libero (trascinando il bordo; più colonne selezionate si ridimensionano insieme) e viene ricordato anche cambiando pagina. Nel menu «Colonne», sotto l'elenco: «Adatta larghezza al contenuto» (intestazione e valori della pagina visualizzata, massimo 420 px, ripetibile in qualsiasi momento, anche dopo modifiche manuali) e «Larghezza standard» (ripristina tutte le colonne alla larghezza uniforme). Non c'è nessun pulsante in più nella barra principale per le larghezze.

**Limiti.** L'adattamento misura la pagina visualizzata (al massimo 2.000 righe distribuite), non l'intero file. In Excel: formati a frazione (`# ?/?`) e altri formati rari ricadono sul testo generale. Se dopo l'apertura di un Excel si aggiungono/eliminano righe o colonne, il foglio viene riscritto con i valori del dataset: le colonne senza tipo assegnato sono scritte come testo.

**Test eseguiti per la v0.17.0** (Linux, Python 3.13, DuckDB 2.0.0, PySide6, offscreen, `unittest`), tutti superati: `test_core` 18, `test_phase2_core` 39, `test_phase3_core` 13, `test_phase4_core` 17, `test_phase5_core` 29, `test_phase6_core` 28 (apertura tutto testo con `00123`/`00045`/`1.50`, Excel con date, identificativi e zeri iniziali, riconoscimento di interi/decimali/date/booleani, colonne miste o non valide, conversione e annullamento esatto, fallimento a metà, valori molto lunghi, salvataggio CSV/Excel senza alterazioni), `test_ui` 40, `test_phase2_ui` 37, `test_phase3_ui` 27, `test_phase4_ui` 51, `test_phase5_ui` 21, `test_phase6_ui` 20 (larghezza standard uniforme, ridimensionamento manuale, adattamento ripetuto dopo modifiche manuali, ripristino, menu «Colonne» e «Tipi», conferme). I test storici su colonne numeriche/date usano `tests/legacy_types.py` (lettura con tipi dedotti) per continuare a verificare filtri, ordinamenti e salvataggi su colonne tipizzate. **Non eseguiti:** prove manuali su Windows e macOS, compilazione PyInstaller di questa versione, file Excel `.xls` con formati numero reali (il formato è letto con `xlrd`, ma il test dedicato richiede `xlwt`, non installato).

## Dettaglio record (v0.18.0)

- **Larghezza delle colonne (cioè dei record):** come nella tabella principale: larghezza standard uniforme all'apertura (320 px dalla v0.20.0, il doppio della tabella principale; con un solo record l'unica colonna riempie la finestra, come prima), trascinamento libero dei bordi e, nel menu «Colonne» del dettaglio, «Adatta larghezza al contenuto» (massimo 420 px, ripetibile anche dopo modifiche manuali) e «Larghezza standard». L'altezza delle righe segue la larghezza (testo a capo). Le larghezze del dettaglio non cambiano quelle della tabella principale.
- **Selezione sincronizzata (per celle dalla v0.20.0):** aprendo il dettaglio risultano selezionate esattamente le celle selezionate nella tabella principale (singole, multiple, non contigue; mai righe o record interi, con filtri e ordinamenti applicati), portate in vista e la cella attiva diventa quella del record e del campo corretti, portati in vista. Selezionando altri record nel dettaglio, le celle corrispondenti diventano la selezione della tabella principale (e vengono portate in vista); le celle dei record oltre il limite di 200 restano selezionate. Senza interventi nel dettaglio la selezione principale resta identica.
- Test: `test_phase7_ui` (10 test, offscreen).

## Easter egg (v0.19.0)

Sette clic sulla prima «o» di «Revolution» nel logo in alto a sinistra (al massimo 2,5 s tra un clic e il successivo; il logo per il resto non cambia) aprono una finestra con un campo di testo, «Avvia» e «Annulla». Digitando il nome di un gioco (maiuscole, minuscole e spazi non contano) e premendo «Avvia» o Invio il gioco parte in una finestra indipendente; un nome sconosciuto chiude la finestra senza messaggi. Il codice sta in `easter_eggs/` (fuori da `csv_reader/`): `manager.py` riconosce il nome e avvia il gioco, `activation.py` è la finestra, `games/<nome>/` contiene un gioco per cartella; i giochi sono trovati da soli (vedi `easter_eggs/README.md` per aggiungerne uno). Se la cartella manca, l'applicazione funziona come sempre.

Primo gioco: **`snake`** (arcade retrò: frecce o WASD, Spazio/P pausa, Invio/R ricomincia, Esc esce, punteggio e record in tempo reale, Game Over con «Ricomincia» e «Chiudi»; velocità crescente; pausa automatica se la finestra perde il focus; alla chiusura i timer si fermano e la finestra viene eliminata). Test: `test_easter_eggs` (30 test).

## Compilazione e «Apri con»
Vedi `packaging/`. Windows: `.\packaging\build.ps1` (con Inno Setup 6 crea anche l'installer che registra `.csv`, `text/csv`, ruolo editor, senza prendere il posto dell'app predefinita). macOS/Linux: `./packaging/build.sh` (+ `./packaging/linux/install.sh`). I `.xlsx/.xls` si aprono dall'app o trascinandoli; l'associazione di sistema è registrata solo per `.csv`.

## Test
```bash
python -m pip install -r requirements.txt -r requirements-dev.txt
python -m unittest tests.test_core tests.test_phase2_core tests.test_phase3_core tests.test_phase4_core tests.test_phase5_core tests.test_phase6_core -v                 # motore
QT_QPA_PLATFORM=offscreen python -m unittest tests.test_ui tests.test_phase2_ui tests.test_phase3_ui tests.test_phase4_ui tests.test_phase5_ui tests.test_phase6_ui tests.test_phase7_ui tests.test_easter_eggs -v   # interfaccia (senza display)
```
**Test automatici eseguiti per questa consegna (Fase 4)** (Linux, Python 3.13, DuckDB 2.0.0, PySide6 6.12, offscreen), tutti superati: `test_core` 18, `test_phase2_core` 39, `test_phase3_core` 13, `test_phase4_core` 17 (annulla nel motore con sequenza casuale, incolla in blocco, salti Excel, appunti TSV, nuovo CSV, documentazione), `test_ui` 40, `test_phase2_ui` 37, `test_phase3_ui` 27 (un test sulla vecchia «copia aggiuntiva» è stato rimosso con il comando), `test_phase4_ui` 45 (annulla, copia/incolla, navigazione con selezione unica dopo Ctrl+Freccia, menu Salva, finestra stabile, «+ Nuovo CSV» e schermata iniziale, errori SQL senza traceback e log, nome, griglia, altezze dei pulsanti). Prove singole (Fase 4 e v0.15.0): prova su 1,0 milioni di righe nell'interfaccia (apertura 1,0 s, ordinamento 0,5 s, ricerca 1,3 s, prima modifica 1,0 s; blocco massimo dell'interfaccia 33 ms),  compilazione PyInstaller e avvio dell'eseguibile su Linux, installazione in un ambiente virtuale pulito. Per la Fase 3 (il test di reattività salva un dataset di 600.000 righe: la suite Fase 3 dura ~80 s). Fase 3 coperta da: round-trip CSV (date, zeri iniziali, a capo, virgolette, vuoti, testo che inizia con «=»), tutti i delimitatori e codifiche, errore per carattere non rappresentabile con file di destinazione intatto e senza temporanei, separatore decimale, esportazione completa dopo modifiche/rinomina/eliminazione/aggiunta, CSV→XLSX (tipi, testo non interpretato come formula, caratteri illegali, interi lunghi), XLSX→XLSX e XLS→XLSX con originale intatto (hash), XLSX→CSV, destinazione protetta, cartella inesistente/permessi, versione salvata; UI: formato preselezionato, cambio formato con copia e scelta «dove continuare», percorso aggiornato solo su richiesta, salvataggi successivi sulla copia, copia extra senza cambiare lo stato, resoconto separato con un salvataggio riuscito e uno fallito, conferma di sovrascrittura, avvisi Salva/Non salvare/Annulla su chiusura file, ricarica e chiusura applicazione, nessun avviso senza modifiche, SQL automatica (colonne visibili/nascoste, filtri multipli, ordine ASC/DESC, nessuna colonna visibile, nomi speciali, aggiunta/rinomina/eliminazione), conservazione della query personalizzata, reattività del ciclo eventi durante il salvataggio.
- **Prestazioni (prova singola, motore senza interfaccia, CSV da 1,0 e 1,5 milioni di righe, 5 colonne):** salvataggio CSV 1,0 M righe 0,2 s (UTF-8) / 0,7 s (con ricodifica Windows-1252), 1,5 M righe 0,4 s; **salvataggio XLSX di 1,0 M righe 78 s**, memoria costante (picco ~275 MB, scrittura in streaming); oltre 1.048.575 righe l'XLSX è rifiutato. Apertura e prima modifica ~1,5 s. L'avanzamento è un indicatore indeterminato (non una percentuale) e l'operazione non è annullabile una volta partita. Non provato con file da 1 GB.
- **Verifiche manuali:** solo schermate offscreen (tema chiaro e scuro) di finestra e finestra di salvataggio.
- **Non verificato:** Windows e macOS, mouse e finestre reali, errori di scrittura reali di file bloccati su Windows o disco pieno (simulati solo con eccezioni sostituite o cartella inesistente/permessi), compilazione PyInstaller e installer con questa versione, file Excel molto grandi.
- **Limiti noti:** nessuna percentuale di avanzamento né annullamento del salvataggio; il formato di data di un CSV è quello rilevato (unico per tutte le colonne data); le regole di formattazione e i filtri non sono salvati nel file né fra sessioni; salvare un Excel dopo cambi di struttura riscrive il foglio con soli valori (niente formule/stili di quel foglio); una query SQL personalizzata resa obsoleta viene solo segnalata (la correggi tu o accetti quella automatica); l'inserimento di una colonna/riga con filtri o ordine attivi mostra la riga solo togliendoli.

**v0.12.1:** popup delle tendine reso opaco (finestre Importazione, Filtra e ordina, Scegli foglio); freccia della barra superiore staccata dal bordo; tab «SQL globale» sempre primo e fisso; colonna/riga aggiunte dopo la selezione. Non verificato su Windows: il nero delle tendine è corretto per ipotesi (popup translucido), controllato solo offscreen.

**v0.12.2:** «Trova e sostituisci…» e «Formattazione condizionale…» sono nel menu «Menu» (ex «Modifica»); sfondi di finestre e tendine forzati opachi, con precedenza corretta nel foglio di stile. Il tema segue il sistema; `CSV_READER_THEME=light` o `dark` lo forza. Il nero su Windows non è riprodotto qui: correzione non verificata su Windows.

**v0.12.3:** sfondo opaco anche per «Trova e sostituisci»; caselle di spunta con riquadro visibile nei due temi; in «SQL globale» la freccia sta sulla riga di «Tabelle disponibili», a destra.

## Novità v0.20.0 — correzioni al dettaglio record
- Larghezza standard delle colonne del dettaglio raddoppiata (320 px); adattamento, massimo (420 px), ridimensionamento manuale e ripristino invariati. Finestra più larga (minimo 640 px, margini maggiori).
- Selezione per celle: nel dettaglio risultano selezionate solo le celle selezionate nella tabella principale; la sincronizzazione inversa riporta le stesse celle.
- **SPAZIO** nel dettaglio non ha alcun effetto (non apre il menu «Colonne», non attiva pulsanti né campi); dentro una cella in modifica resta uno spazio. Le scorciatoie della finestra principale non cambiano.
- Linee di delimitazione di righe e colonne visibili nella tabella principale e nel dettaglio.
