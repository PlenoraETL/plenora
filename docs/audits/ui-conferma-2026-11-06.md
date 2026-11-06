# UI-1..UI-4 — giro di conferma (06/11/2026)

Base: `9e878339`, cioè le quattro famiglie unite. Tre lettori indipendenti, in sola lettura, sull'elenco
`ui-velocita-chiamate-usabilita-raccolta-2026-11-06.md` e sulle regressioni. Prima dell'unione le
suite erano verdi: frontend 4.951 test, lint e build.

Gravità: A alta, M media, B bassa.

## A — Dati, editor, render (UI-1, UI-2)

- **A-K1 (A)** `projects/db.py:652`, `router.py:358`: `pg_input_is_valid(jsonb, …)` non esiste in
  PostgreSQL, quindi la query mirata di C24 fallisce sempre e GET /projects torna al vecchio percorso,
  con un warning. I test la sostituiscono con un finto e non l'hanno mai eseguita su un database vero.
- **A-K2 (A)** `sharedInvalidations.js:31`: le esecuzioni (`/execute`, `/enrich-async`…) dichiarano
  `[]`, ma riscrivono `output_contract` e la freschezza, e Carica crea tabelle. Quando il task finisce
  non si invalida niente: contratto del progetto sorgente, `/schemas`, `source_freshness` e catalogo
  tabelle restano vecchi (fino a 2 o 10 minuti).
- **A-K3 (M)** `useProjectListData.js:33-37,71`: ogni risorsa che arriva rilancia `loadDashboard`, che
  include `getLatestRunByProject` (fuori dalla cache): fino a 7 chiamate a latest-by-project a cache
  vuota.
- **A-K4 (M)** `useEditorData.js:45,86-90,131-150`: il progetto all'apertura dell'editor viene da
  stale-while-revalidate, ma il dato fresco non si applica. Possibili canvas vecchio, conflitto 409 al
  primo salvataggio, `applied_migrations` a monte perse.
- **A-K5 (M)** `poller.js:119,145-151`, `taskPoller.js:174`:
  - (a) la durata massima conta il tempo reale, quindi dopo più di 3 ore nascosta o in sospensione un
    task finito risulta perso, senza un'ultima lettura;
  - (b) i profili dati si fermano dopo 30 minuti senza `onExpire`;
  - (c) `useResource.js:192,201`: con risorse condivise interrogate a intervalli la notifica dello
    stesso hook supera la richiesta, quindi il backoff è perpetuo;
  - (d) nessun tetto dichiarato per Arricchisci.
- **A-K6 (M)** `TableNode.jsx:209`, `VirtualList.jsx:122-125`:
  - nel nodo mancano `nowheel`/`nodrag`, quindi la rotella zooma e trascinare la barra sposta il nodo;
  - doppio scorrimento in dashboard e ScheduleList;
  - Ctrl+F e Tab non raggiungono le righe non disegnate;
  - le misure sono per indice e non per chiave;
  - il menu ⋯ viene tagliato dal contenitore.
- **A-K7 (M)** `useColumnStore.js:52,184-191,210-211`: la cache `/schemas` dipende solo dalla firma del
  canvas. Entro 2 minuti si usa anche se è cambiato qualcosa a monte (progetto, file, tabella), e
  `ensure()` usa qualunque età per il salvataggio.
- **A-K8 (B)** `AuthContext.jsx:132-142`: un cambio di `group_id`/`role` da `/auth/me` non invalida la
  cache; `/auth/users|groups` ha `[]`.
- **A-K9 (B)** `sharedCache.js:69,126`: una sola generazione per tutte le risorse (qualunque scrittura
  scarta tutte le letture in volo); un errore di rivalidazione non mostra «non aggiornati».
- **A-K10 (B)** `files/source_metadata.py:108-117`: le righe senza metadati accorciano la pagina e
  un'eccezione dà `files=[]`, quindi il client tronca la lista in silenzio. Le pagine in parallelo
  possono saltare o duplicare un file durante un caricamento.
- **A-K11 (B)** migrazione dell'indice su `log_runs` senza `CONCURRENTLY`: blocca le scritture
  durante l'installazione.
- Parziali dichiarate: C7, C10 (lista dei ticket), C14 (due richieste), C15 Estrai, C21, C25
  (`DISTINCT ON` legge ancora tutte le righe di ogni progetto).

## B — Usabilità e funzioni (UI-3)

- **B-K1 (A, confermato)** `manipola/transforms/filtering.py:146`: con `np.asarray(mask, dtype=bool)`
  una maschera con valori mancanti (colonne Int64/string nullable) dà `ValueError`/`TypeError`. Ogni
  Filtra con `conditions` su colonne con valori mancanti fallisce, anche con un solo gruppo.
  Riprodotto con `>`, `==` e con 2 gruppi.
- **B-K2 (M)** `canvasCommands.js:60-80,105-112`:
  - appunti globali, quindi si possono incollare tra editor diversi nodi di tipo non valido;
  - la copia di un Input runtime duplica `input_key`.
- **B-K3 (M)** `canvasCommands.js:63-66` rispetto a `EstraiEditor.jsx:219-224`: copia o duplicato di
  una tabella Estrai senza un alias nuovo. Il backend assegna `tabella_2`, il frontend usa
  `alias || table_name`: riferimenti ambigui.
- **B-K4 (M, privacy)** `useEseguibileRun.js:77-82,104-116`: la pulizia sul server avviene solo alla
  chiusura della finestra, quindi chiudendo la scheda i file restano sul server.
- **B-K5 (B-M)** `useEseguibileRun.js:70,91,114`: dopo 30 minuti la copia locale sparisce ma la fase
  resta `success`. «Scarica» chiede di nuovo al server (consegna unica, quindi errore); il messaggio di
  chiusura è falso; il blob resta in memoria; chiudere durante lo scaricamento pulisce in volo.
- **B-K6 (M)** `ExecutionModal.jsx:460-461,104`, `runFailure.js:47`:
  - «Apri il registro» e «Segnala» vengono mostrati all'esecutore (403);
  - `failedStepOf` legge lo store di un altro progetto fuori dall'editor;
  - il ticket usa `result.message` grezzo.
- **B-K7 (B-M)** `CanvasOverlays.jsx:32,65-67`: con il fuoco su body, Ctrl+F e Ctrl+D del browser
  vengono bloccati sempre; `preventDefault` arriva prima della verifica di disponibilità; «C» e
  Ctrl+V/D in sola lettura mostrano un avviso.
- **B-K8 (M)** `EditorFileUploadModal.jsx:46`: il file finisce nel gruppo dell'utente e non in quello
  del progetto (admin su un progetto di un altro gruppo).
- **B-K9 (B)** `editorNodeCatalog.js:292-305`: `project_name`/`file_name` salvati non si aggiornano
  mai; con una sorgente fuori dall'elenco l'effetto si ripete.
- **B-K10 (B)** `paletteMemory.js:14`: preferiti e recenti sono per browser e non per utente, e non si
  cancellano a fine sessione.
- **B-K11 (B)**:
  - `editorNodeCatalog.js:138-143`: «Carica su DB» con `dest_type: 'arcgis'` non blocca «Carica su
    ArcGIS»;
  - la pulizia degli identificatori con lettere accentate è diversa tra frontend (`estraiJoins.js:327`)
    e backend (`query_aliases.py:39`).
- Parziali: U13 (K4, K5), U16 (caricatori delle sorgenti senza `error_node_id`, dichiarato), U19/P29
  lato frontend (K3), U20 (K1), U27 (K2, K7).

## C — Coerenza, bundle, trasversali (UI-4)

- **C-K1 (M)** `editorChunks.js:20-24`, `chunkReload.js:19-28`: un prefetch fallito (dopo un deploy)
  lancia `vite:preloadError` e ricarica la pagina al solo passaggio del mouse o della freccia in
  Ctrl+K. Dall'editor con modifiche compare la domanda nativa del browser.
- **C-K2 (M)** `ScheduleWizard.jsx:35-49`, `cron.js:192`: le prossime esecuzioni sono calcolate nel
  fuso della pianificazione, ma il beat valuta il cron in `BEAT_TIMEZONE` (`cron_schedule.py:69-77`).
  Il test non confronta con croniter.
- **C-K3 (B)** `shortcuts.js:25-28,42-45`: su tastiera italiana «/» è Shift+7, quindi apre insieme la
  palette e la guida dei tasti.
- **C-K4 (B)** `canvasCommands.js:105,126,137`: emoji scritte a mano fuori da `ICON` (📋 doppio
  significato con openapi, 🔎 contro 🔍).
- **C-K5 (B, privacy)** `api/projects.js:123-129`, `knownProjects.js:13`: `rememberProjects` gira prima
  del controllo di sessione, quindi una risposta tardiva dopo logout e login mostra a B i progetti di A
  in Ctrl+K. Anche progetti rinominati o eliminati restano visibili.
- **C-K6 (B)** `ProjectList.css:106-112`: il badge da 14 px con testo a 11 px fa uscire il numero; il
  testo bianco su `--pl-blue` non è controllato.
- **C-K7 (B)** `AppSidebar.css:276`: a barra chiusa «Cambia password» non si raggiunge.
- **C-K8 (B)** `EditorPanels.jsx:58-68`: localStorage scritto a ogni `pointermove`; nessuna gestione di
  `pointercancel`/capture.
- **C-K9 (B)** `ManipolaEditor.jsx:242,419`, `ArricchisciEditor.jsx:278,374`: «Carica un file» compare
  anche in sola lettura e il messaggio di esito è falso.
- **C-K10 (B)** `dashboard/router.py:65-109`: tutto o niente (un errore toglie tutti i conteggi);
  conteggio che scorre le liste intere e calcola `can_edit` per ogni riga.
- **C-K11 (B)** informazioni scritte in più posti:
  - tasti scritti a mano in `glossary.js` e in `EditorShell.jsx:487`;
  - due convenzioni per le chiavi di localStorage;
  - `NodePalette.jsx:83` confronta il testo `'Recenti'`.
- Parziali: U14 (`EsecutoreHome.jsx:63-65`: il modulo dinamico dipende dal manifest e non da
  `expected_columns_count`), U22 (`glossary.js:66` «flusso»), U24 (C-K4), V13 (C-K1).
