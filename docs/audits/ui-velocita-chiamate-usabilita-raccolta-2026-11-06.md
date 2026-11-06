# UI: velocità, chiamate, usabilità — raccolta (06/11/2026)

Base: `e2bada1b`, in produzione su 172.16.28.6. Fonti:
- tre lettori indipendenti sul codice, in sola lettura: V = velocità, C = chiamate, U = usabilità;
- misura dal vivo sul sistema online (M).

Nessuna correzione in questa fase. Le voci U partono da U9 perché U1–U8 sono già nell'audit del
03/11. Ogni voce indica file:riga relativi a `frontend/src` (o a `backend/app` dove indicato) e la
gravità: A = alta, M = media, B = bassa.

## M — Misura dal vivo (dati del server: 19 progetti, 3 connessioni)

| Pagina | Chiamate | Fine caricamento | Note |
|---|---|---|---|
| Dashboard `/` | 11 | 0,24 s | 9 liste intere; `/tickets/counts` ×2 |
| `/run` | 11 | 0,22 s | stesse chiamate della dashboard |
| Pagine di sistema | 2–4 | 0,06–0,13 s | `/admin/system-status` 0,5 s |
| Manipola, copia di `SC_scheda_manipola` (61 nodi) | 6 | 2,0 s | progetto 83 kB in 0,52 s; `/schemas` 49 kB in 1,35 s |
| Estrai, `SC_H_ticket` | 6 | 2,6 s | `/connections/{id}/tables` 96 kB in 2,4 s (1.287 tabelle, a ogni apertura); `/query-preview` ×2 |
| Estrai, `Scheda_Complesso` | 6 | 1,2 s | stesse chiamate |

Trascinamento di un nodo nella copia di Manipola: nessun long task sul PC dell'utente. I difetti di
render V3–V6 non si percepiscono con 61 nodi su una macchina potente; vanno misurati su un portatile
o con progetti più grandi.

Con pochi dati le pagine di sistema sono rapide. Il costo cresce con il numero di progetti, file e
log (C4, C24, C25).

## V — Velocità

- **V1 (A)** Lo store del canvas (`@xyflow`) finisce nel chunk iniziale passando per
  `NavigationGuard` → `useUnsavedChangesGuard` → `useCanvasStore`. Così vendor-reactflow (61 kB gzip)
  si scarica già su `/login` e sulle pagine di sistema.
- **V2 (M)** `EditorShell.jsx:79` importa staticamente dagre (`autoLayout`), che serve solo al
  pulsante «Riordina».
- **V3b (B)** `utils/openapiImport.js:51` importa staticamente js-yaml.
- **V3c (B)** `index.jsx:29`: Sentry, 158 kB gzip, si scarica all'avvio quando c'è la DSN.
- **V3 (A)** `EditorToolbar.jsx:26` e `StepPreview.jsx:46` (`hasUnsavedChanges`). A canvas modificato,
  ogni frame ricalcola `savedCanvasSignature`, cioè serializeCanvas più JSON.stringify di tutto il
  canvas. Lo stesso calcolo parte anche da `useLocalDraft.js:140-146`.
- **V4 (A)** `useCanvasCore.js:27-28`: le pagine editor sono iscritte a nodes ed edges, quindi a ogni
  frame si ri-renderizza tutto l'editor. EditorShell non è memo e riceve props nuove a ogni render. In
  Estrai, a ogni frame, girano anche il filtro sulle 1.287 tabelle e il calcolo di `joinStatus`.
- **V5 (M)** Selettori O(N+E) a ogni aggiornamento: `EditorShell.jsx:313,318`, `BranchSection.jsx:29`.
  `CompactNode.jsx:22-26,50` costa O(N·E) per frame.
- **V6 (A con tabelle larghe)** `NodeInspector.jsx:215` e `StepPreview.jsx:45` prendono l'intero nodo:
  trascinando il nodo selezionato si ri-renderizza il form, e TableNode con 100+ colonne non è
  virtualizzato.
- **V7 (M)** `QueryPreview.jsx:51-57`: `canvasContentKey` è calcolato a ogni tasto, prima del debounce.
- **V8 (B)** `EditorShell.jsx:199,437`: callback inline passate a ReactFlow.
- **V9 (M-A)** react-window è installato ma non è usato. Liste rese per intero: ProjectList,
  FileManager, ScheduleList, colonne di TableNode.
- **V10 (M)** `api/pagination.js:7-24`: le pagine da 200 si scaricano in serie e la prima
  visualizzazione aspetta l'ultima.
- **V11 (B-M)** `ProjectList.jsx:210-219`: ricerche lineari per ogni card.
- **V12 (B)** `AuthContext.jsx:131-138`: `/auth/me` parte sia su focus sia su visibilitychange (= C3).
- **V13 (B)** Nessun preload dei font. A ogni prima visita di una route compare un «Caricamento...» a
  tutto schermo. Gli editor non vengono precaricati.

## C — Chiamate al server

### Apertura delle pagine
- **C1 (M)** Lo scheletro della dashboard rimonta AppSidebar, quindi `/tickets/counts` parte 2 volte.
  Confermato dal vivo.
- **C2 (B-M)** `/tickets/counts` viene richiesto a ogni cambio di pagina.
- **C3 (B)** `/auth/me` viene richiesto due volte a ogni ritorno sulla scheda.
- **C4 (A)** La dashboard carica 9 liste intere; per diverse servirebbe solo il conteggio.
- **C5 (M)** `/files` non è paginato e il backend lo limita a 200 senza avviso. Ogni file include le
  `columns`.
- **C6 (M)** `OpenProjectPage.jsx:21` scarica il progetto completo solo per sapere il tipo, poi
  l'editor lo riscarica.
- **C7 (M)** `useEditorData.js:38,52`: l'editor carica tutte le connessioni (e tutte quelle ArcGIS)
  per usarne una.
- **C8 (M)** `useEditorData.js:125`: liste progetti e file ricaricate all'apertura dell'editor.
- **C9 (M)** Lista progetti intera caricata solo per i nomi: LogViewer, Eseguibili, ScheduleWizard.
- **C10 (B)** `/tickets/meta`, che è statico, viene richiesto a ogni navigazione; la lista ticket
  carica fino a 500 elementi.
- **C11 (B)** Backup in corso: lo stato viene richiesto 2 volte all'apertura.
- **M-Estrai (M)** L'elenco tabelle della connessione (96 kB, 1–2,4 s) viene riscaricato a ogni
  apertura dell'editor. All'apertura `/query-preview` parte 2 volte.

### Editor
- **C12 (A)** `/schemas` manda il canvas intero a ogni modifica, con un debounce di 250 ms e senza
  annullare le richieste superate. Dopo un Annulla le colonne si richiedono di nuovo; riaprendo
  l'editor si ricalcola tutto.
- **C13 (M)** La firma include `position.y` delle sorgenti, quindi un trascinamento verticale rilancia
  `/schemas`.
- **C14 (M)** Estrai: `/schemas` più `/query-preview` a ogni modifica, ciascuna con il canvas intero.
- **C15 (A, backend)** `projects/router.py:878-886`, `_prepare_canvas_for_owner`: ogni `/schemas`
  ricalcola tutto il grafo almeno 2 volte, circa 4 in Estrai. Le cache valgono solo per la singola
  chiamata.
- **C16 (B)** Scrivere in un nodo di input rilancia `/connection-sets`.
- **C17 (M)** Ogni selezione di un nodo rifà le sue richieste: progetto completo, lista datasources,
  lista ArcGIS e così via.
- **C18 (M)** Anteprima: PUT, validate, avvio e polling. Esegui: salvataggio, validate, preflight ed
  execute.

### Polling
- **C19 (A)** `taskPoller.js`: polling a 1 s che continua con la scheda nascosta, senza backoff e
  senza durata massima.
- **C20 (M)** Carica: un poller da 1 s per ogni task, tutti in parallelo.
- **C21 (M)** ScheduleList ricarica ogni 3 s la lista intera, con tutte le pagine.
- **C22 (B-M)** RunsTab, RunDetail e LogViewer ricaricano ogni 5 s.
- **C23 (B-M)** System status ogni 30 s e task runtime ogni 5 s. System status scrive anche una riga
  di audit a ogni poll.

### Backend e cache
- **C24 (A, backend)** GET `/projects` legge dal DB il `canvas` di tutti i progetti per ricavare
  `source_project_ids`.
- **C25 (A, backend)** `/pipeline-runs/latest-by-project` usa DISTINCT ON su tutta `log_runs`, senza
  indice su `(project_id, ts_start)`.
- **C26 (M, backend)** `/files` restituisce le `columns` di ogni file.
- **C27 (A)** Nessuna cache tra pagine per progetti, connessioni, file, datasources e connessioni
  ArcGIS.

## U — Usabilità (nuove rispetto agli audit precedenti)

- **U9 (A)** Le sorgenti trascinate nel canvas non salvano il nome del progetto o del file, quindi il
  titolo del nodo diventa l'id: confermato dal vivo, «totale_immobili_20260714_153134_285697c0» nel
  progetto TEST. Riferimenti: `ManipolaEditor.jsx:443-451`, `ArricchisciEditor.jsx:373-376`,
  `nodeDescriptors.js:131,137`.
- **U10 (A)** Dopo «Crea progetto» non si entra nell'editor e non compare nessun messaggio; Invio non
  crea il progetto. `NewProjectModal.jsx:52-54`.
- **U11 (A)** Il canvas vuoto non dà nessuna guida. La palette non contiene le sorgenti, che si
  aggiungono solo trascinando: niente clic, niente tastiera.
- **U12 (M-A)** In Estrai l'Output sta solo nel menu ⋯ e il motivo per cui Esegui è spento compare
  solo nel tooltip.
- **U13 (A)** `/run`: Chiudi, × ed Esc cancellano il risultato sul server senza avviso. Anche lo
  scaricamento chiude e pulisce subito, quindi il file non si può riscaricare.
  `useEseguibileRun.js:45-58`.
- **U14 (M)** `/run` usa termini tecnici: catena, Upload file, Task in coda, cleanup, step falliti. La
  card mostra «0 colonne attese» per gli eseguibili con modulo dinamico. Manca un file modello da
  scaricare.
- **U15 (M)** Due pulsanti «Anteprima» con effetti diversi. Il salvataggio prima delle azioni segue
  regole diverse a seconda del punto.
- **U16 (M)** Errore di esecuzione: non dice quale passo è fallito e non offre né «Apri log» né
  «Segnala» con progetto e run precompilati.
- **U17 (M)** Nessun avanzamento nei caricamenti e negli scaricamenti dei file. I tempi sono mostrati
  in secondi grezzi.
- **U18 (M)** «Usa cache esistente» è l'opzione predefinita, è nascosta e non indica quanto siano
  aggiornate le sorgenti.
- **U19 (M)** Palette di Estrai: senza ricerca mostra 40 tabelle e 40 viste, tagliate senza avviso.
  La ricerca guarda solo il nome della tabella.
- **U20 (M)** Filtri: Estrai usa operatori SQL, Manipola parole italiane. Nessuno dei due ammette OR.
- **U21 (M)** Etichette dei join diverse tra gli editor; il tipo predefinito è inner dalla palette e
  left da «Collega».
- **U22 (M)** Terminologia mista: progetto, flusso, catena, pipeline, canvas, e molti termini inglesi.
- **U23 (B)** Barra laterale e Ctrl+K usano nomi diversi per le stesse pagine; Ctrl+K non cerca i
  progetti.
- **U24 (M)** Emoji con più significati (💾, 📁, 🔍) e icone dei tipi di progetto incoerenti.
- **U25 (M)** Contrasto di `--plen-text-dim` circa 3,7:1 (serve 4,5), usato 151 volte. Ci sono 54
  regole con testo a 9 px.
- **U26 (M)** A 1366×768 barra laterale e pannello sono fissi e non si chiudono: il canvas resta
  stretto.
- **U27 (M)** Mancano copia/incolla, «Duplica passo», ricerca nel canvas e «Seleziona tutto»; il
  collegamento tra nodi si fa solo con il mouse.
- **U28 (B)** La guida delle scorciatoie è incompleta e non ha un pulsante «?» visibile.
- **U29 (B-M)** Nessun preferito né elemento recente; «Più usate» è una lista fissa.
- **U30 (M)** Per caricare un file bisogna uscire dall'editor (ricaricando la pagina). Manca un aiuto
  per Manipola ed Estrai.
- **U31 (B)** Cron senza descrizione in parole né prossime esecuzioni; l'aiuto è un link esterno.
- **U32 (B)** Testi fuorvianti nella creazione del progetto e nella barra laterale.
