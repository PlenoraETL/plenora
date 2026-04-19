# Frontend — Resource leaks da sistemare

Audit del 2026-04-15. **Non toccare prima di aver chiuso il lavoro sui test.**
Questo file serve solo come lista di lavoro per dopo.

## Pattern comune

Quasi tutti i leak di interval sotto hanno la stessa forma:

> "Avvio il polling, lo salvo in un ref, lo pulisco solo all'unmount del componente."

Serve invece pulire anche quando:
- l'utente rilancia la stessa azione (doppio click su Publish, re-exec);
- la task termina (success o error);
- arriva un errore nel callback del polling.

Una soluzione trasversale è un helper tipo `useSafeInterval(fn, ms)` o
`useManagedInterval()` che:
1. fa `clearInterval` sul ref prima di ricrearne uno nuovo;
2. espone una `stop()` esplicita;
3. garantisce cleanup anche su unmount.

Questo taglia metà dei problemi qui sotto in un colpo solo.

---

## Alto impatto — si accumulano per azione utente

### 1. `src/components/nodes/ImdfPublisherNode.jsx:~147`
Polling del publish task avviato dentro callback async. Nessun guard contro
doppio click. Se l'utente rilancia validate/publish prima che il primo interval
finisca, gli interval si sommano e nessuno li ferma. Ogni click → un interval
in più che vive finché non smonta il nodo.

**Fix**: clear del ref precedente prima di avviare un nuovo interval, e
clear anche quando la task termina (non solo onUnmount).

### 2. `src/components/eseguibili/EseguibileExecuteWizard.jsx:~150`
`setInterval(poll, 1500)` salvato in `pollRef.current`. Il cleanup è legato
all'unmount del wizard, non al fatto che la task sia finita. Ogni nuova
esecuzione lascia l'interval precedente ancora attivo.

**Fix**: clear del ref alla fine di ogni poll-cycle riuscito/fallito.

### 3. `src/hooks/useExecution.js:~108` (`startCaricaPolling`)
Sovrascrive `caricaRefs.current[task_id]` senza fare `clearInterval` del
precedente. Se la stessa task viene ri-pollata (capita col retry), l'interval
vecchio continua a girare in parallelo.

**Fix**: prima di assegnare il nuovo interval, clear del vecchio se presente.

### 4. `src/components/modals/ImdfPreviewModal.jsx`
La modale si apre/chiude di continuo. Due sottoproblemi:

- **Resize handler**: dentro al callback crea `setTimeout` senza tracciarli.
  In sessioni lunghe con molte aperture i timeout si accumulano.
- **Deck.gl / Maplibre lifecycle**: verificare che al cleanup vengano chiamati
  `deck.finalize()` e `map.remove()`. Se manca anche solo uno dei due, ogni
  apertura lascia in memoria un'istanza (queste pesano MB, non KB).

**Fix**:
- tracciare il timeout con un ref singleton e clear prima di ricrearlo;
- verificare esplicitamente `deck.finalize()` + `map.remove()` nel return
  dell'effect di init;
- considerare `AbortController` per il fit-bounds e per il caricamento
  GeoJSON, così setState post-unmount non scatta.

---

## Medio — facile da sistemare, vale la pena farlo insieme

### 5. `src/pages/ScheduleList.jsx:~144`
Due interval concorrenti (`pollingRef`, `tickRef`) fermati in modo
condizionale. In alcuni passaggi di stato uno dei due non viene pulito.

**Fix**: cleanup simmetrico, entrambi i clear nello stesso return dell'effect.

### 6. `src/pages/LogViewer.jsx:~141`
`setInterval` con deps `[autoRefresh, loadLogs, loadStats]`. Se `loadLogs` o
`loadStats` cambiano identità (probabile: non sembrano memoizzati) il vecchio
interval resta finché il componente non smonta.

**Fix**: o memoizzare `loadLogs`/`loadStats` con `useCallback` stabile, o
spostare l'interval su un ref e gestirlo a mano.

### 7. `src/pages/ProjectList.jsx` — NL2 flow (`handleNl2Generate`)
`AbortController` c'è già, ma manca un `mountedRef` guard: il `.then` del
vecchio fetch può ancora chiamare `setNl2Logs` su un componente smontato.

**Fix**: `mountedRef` pattern classico, set a `false` nel cleanup del
componente, check prima di ogni `setState` nel `.then`.

---

## NON sono problemi (chiarificazione)

Solo per dire: *non toccare per errore durante il refactor.*

- **Zustand store** — non cresce senza controllo, bundles/task results sono
  limitati e sovrascritti.
- **`URL.createObjectURL` in `LogViewer`** — revoke sincrono nello stesso
  blocco, pulito.
- **`useHexCanvas`** — observer lifecycle ok, observer disconnessi nel
  cleanup.
- **`ModalWrapper` escape listener** — re-entry trascurabile, dipendenze
  stabili.
- **`useTaskPolling`** — guard `mountedRef` già presente, al massimo una
  fetch in-flight può perdere il setState ma non crasha.

---

## Ordine di attacco consigliato

Quando sarà il momento:

1. **Prima**: scrivere `useSafeInterval` / pattern helper (~30 righe).
2. **Poi**: applicarlo a `useExecution`, `ImdfPublisherNode`,
   `EseguibileExecuteWizard`. Sono i tre high-impact e condividono il pattern.
3. **Poi**: `ImdfPreviewModal` — va guardato a parte perché il problema è
   Deck/Maplibre, non interval. Verificare manualmente con DevTools →
   Performance → Memory che aprendo/chiudendo 20 volte la modale l'heap
   non cresce monotonicamente.
4. **Infine**: `ScheduleList`, `LogViewer`, `ProjectList` NL2 — sono i low
   hanging fruit, 5 minuti a testa.

## Come verificare che un fix funziona

- **Chrome DevTools → Memory → Heap snapshot** prima e dopo 20 cicli
  dell'azione (open/close modale, validate/publish ripetuto, ecc.).
- L'heap non deve crescere monotonicamente. Se cresce di qualche MB ma poi
  GC lo recupera, ok. Se cresce e non scende, il leak è ancora lì.
- Per gli interval: `window.setInterval` può essere wrappato in dev per
  contare quanti sono attivi — o più semplice, breakpoint dentro al poll
  callback e contare quante volte spara in parallelo.

## Cosa NON fare

- Non aggiungere `try/catch` generici per "nascondere" gli errori da
  setState su unmounted — quello è nascondere il sintomo, non risolvere.
- Non aggiungere test per questi leak prima di aver sistemato il codice:
  testare memory leaks in jsdom è inaffidabile, meglio DevTools manuale.
- Non usare `useLayoutEffect` al posto di `useEffect` "per sicurezza":
  non c'entra nulla con il problema.
