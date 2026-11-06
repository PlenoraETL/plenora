# UI — conferma finale dopo la correzione (06/11/2026)

Base: `bfdf81a9`. Due lettori indipendenti hanno verificato l'elenco `ui-conferma-2026-11-06.md` e le
regressioni introdotte dalle correzioni `9385c47c..bfdf81a9`.

Esito dell'elenco: tutte le voci sono chiuse, tranne quelle parziali indicate sotto.

## Regressioni e residui

Gravità: M media, B bassa.

- **F1 (M)** `TableNode.jsx:211`: `nowheel nodrag` è applicato anche sotto le 60 colonne, dove la lista
  non scorre, quindi la rotella non zooma più sul corpo delle tabelle normali.
- **F2 (B)** `VirtualList.jsx:168,57`: dopo Ctrl+F `renderAll` resta attivo per sempre (nessun ritorno,
  nessun reset, `defaultPrevented` ignorato); `scrollParentOf` prende un antenato che non scorre.
- **F3 (B-M)** `migrations_runner.py:312`: con più istanze, una vede come INVALID l'indice che un'altra
  sta ancora costruendo CONCURRENTLY e lo toglie. Manca un advisory lock.
- **F4 (B-M)** CREATE INDEX CONCURRENTLY all'avvio aspetta le transazioni aperte; un riavvio a metà
  lascia l'indice INVALID e può innescare un ciclo. Non dichiarato.
- **F5 (M)** `taskPoller.js:57`, `taskRegistry`: l'invalidazione a fine task parte solo se la pagina che
  l'ha avviato lo vede finire. Uscendo dall'editor durante l'esecuzione la cache resta vecchia e la voce
  del registro non si toglie.
- **F6 (B)** `useColumnStore.js:235`: `ensure()` vuole una mappa con meno di 120 s, quindi un
  salvataggio senza modifiche dopo 2 minuti aspetta una `/schemas`.
- **F7 (B)** `pagination.js:60`: la lettura a cursore dei file è solo sequenziale (prima 4 pagine in
  parallelo).
- **F8 = G3 (M, privacy)** `api/tasks.js:87-90`: annullamento e full-cleanup partono in parallelo
  senza ordine, quindi quello che il worker scrive dopo l'annullamento resta (log, record).
- **F9 (B)** `projects/db.py:678,686`: sottoquery correlate quadratiche sui nodi e JSON letto due
  volte.
- **G1 (M)** Finestre caricate al bisogno negli editor (Versioni, Anteprima) dopo un deploy:
  - la fallback di `ModalErrorBoundary` offre solo «Chiudi» e non si chiude, perché lo stato `show*`
    resta vero e lazy rilancia l'errore;
  - la boundary unica si porta via anche `ExecutionModal`;
  - non c'è nessun «Ricarica».
- **G2 (B)** `EditorShell.jsx:326-329`: se manca il chunk di dagre l'errore va solo in console. Il
  commento di `chunkReload.js:17` è falso.
- **G4 (B)** `useEseguibileRun.js:108-118`: `pagehide` con `persisted` (bfcache) pulisce comunque;
  nessun test.
- **G5 (B)** `has_manifest` è sempre vero, perché `manifest` è NOT NULL con `{}`, mentre il backend
  tratta `{}` come assenza. Gli Eseguibili a file singolo mostrano «Modulo» e il ramo «File con N
  colonne» è morto.
- **G6 (B)** `cron.js:60`: una pianificazione salvata con `7-7` non si salva più in modifica, anche se
  il backend la accetta.
- **G7 (B)** `models.py:460`: `timezone` è il `BEAT_TIMEZONE` del processo API e non quello del beat;
  se l'healthcheck fallisce si usa `Europe/Rome` senza dirlo.
- **G8 (B)** Duplicazioni:
  - le chiavi di localStorage hanno cinque forme (C-K11 non preso da nessuno);
  - «cosa si perde uscendo» è deciso in due posti (`leaveGuard` e `atRiskRef` di `useEseguibileRun`).
- **G9 (B)** `shortcuts.js:105`: Maiusc+C ora attiva «Collega» (prima no).
- **G10 (B)** `http.js:195`: il commento «stessa origine» di `sendOnPageExit` è falso con
  `apiBaseUrl` assoluto; la keepalive con preflight può non partire, in silenzio.
- **G11 (B)** `useProjectListData.js:48-50`: un conteggio non disponibile si mostra come 0.
- **Test di A-K1 su PG vero**: girano solo con `PLENORA_REAL_PG_DSN`, quindi in CI c'è solo la firma.
  È lo stesso punto debole che aveva causato A-K1.
- **B-K10**: le chiavi per utente restano dopo l'uscita; le vecchie chiavi si cancellano senza
  migrazione.
