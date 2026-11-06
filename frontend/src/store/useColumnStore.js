/**
 * useColumnStore — la mappa delle colonne dell'editor aperto (Blocco A,
 * regola 4): schemi e stati per nodo calcolati dal backend sul canvas
 * DELL'EDITOR. Si scrive solo da startColumnSync (un'unica sottoscrizione
 * allo store del canvas); si legge solo dal selettore columnsOf
 * (utils/columnSource, hooks/useColumns).
 *
 *  - status: 'idle' | 'loading' | 'ok' | 'error';
 *  - error: messaggio dell'ultima richiesta fallita. Le colonne restano le
 *    ultime note: un errore non svuota i pannelli.
 *  - key: FIRMA del canvas (columnSignature) da cui la mappa e' stata
 *    calcolata.
 *
 * Regola unica della firma (vale per la sottoscrizione e per il
 * salvataggio):
 *  1. ogni mappa porta la firma del canvas che il server ha letto
 *     (columnSignature: contenuto semantico + ordine dei lati senza
 *     maniglia; niente posizioni, niente stato di interfaccia);
 *  2. una risposta entra nello store solo se la sua firma e' quella del
 *     canvas CORRENTE; ogni risposta entra comunque nella CACHE DELLE MAPPE
 *     della sua firma (UI-2, C12: piu' mappe per progetto, cosi' Annulla,
 *     «scrivi e cancella» e la riapertura dell'editor non richiedono). Una
 *     mappa in cache si usa subito; se ha piu' di SCHEMA_FRESH_MS si chiede
 *     anche in sottofondo (le sorgenti a monte possono cambiare);
 *  3. il salvataggio allega le colonne derivate solo per la firma del canvas
 *     che salva: se la mappa e' di un'altra firma (o manca) la chiede subito
 *     e la attende (con tetto); se non arriva, non allega niente (il backend
 *     le ricalcola). Mai colonne di una firma diversa;
 *  4. una richiesta superata da una firma nuova si ANNULLA (AbortController),
 *     salvo che un salvataggio la stia aspettando.
 * La cache vale per la sessione e per progetto: non si azzera cambiando
 * editor o riaprendo lo stesso progetto (si svuota all'uscita, R5.5).
 */
import { create } from 'zustand';
import { inferProjectSchemas } from '../api';
import { serializeCanvas } from '../utils/canvasSerializer';
import { columnSignature, handlelessOrderKey, normalizeSchemasResponse } from '../utils/columnSource';
import { humanizeError } from '../utils/errorMessages';
import { registerSessionReset } from '../utils/session';

const NO_ENSURE = null;
const INITIAL = { projectId: null, schemas: {}, states: {}, status: 'idle', error: null, key: null };

export const createColumnStore = () => create(() => ({ ...INITIAL, refresh: () => {}, ensure: NO_ENSURE }));

const useColumnStore = createColumnStore();

// Cache delle mappe per firma: per store delle colonne e per progetto.
const SCHEMA_CACHE_PER_PROJECT = 16;
const SCHEMA_CACHE_PROJECTS = 4;
// Oltre questa eta' una mappa in cache si usa e si richiede in sottofondo.
const SCHEMA_FRESH_MS = 120_000;
const caches = new WeakMap();

function cacheOf(columnStore, projectId) {
  let byProject = caches.get(columnStore);
  if (!byProject) { byProject = new Map(); caches.set(columnStore, byProject); }
  let entries = byProject.get(projectId);
  if (!entries) {
    entries = new Map();
    byProject.set(projectId, entries);
    while (byProject.size > SCHEMA_CACHE_PROJECTS) byProject.delete(byProject.keys().next().value);
  }
  return entries;
}

/** Svuota la cache delle mappe (uscita dalla sessione, test). */
export function clearColumnCache(columnStore = useColumnStore) {
  caches.delete(columnStore);
}

// All'uscita le colonne dell'editor dell'utente non restano in memoria (R5.5).
registerSessionReset('columnStore', () => {
  clearColumnCache(useColumnStore);
  useColumnStore.setState({ ...INITIAL, refresh: () => {}, ensure: NO_ENSURE });
});

// Ritardo dopo l'ultima modifica: piu' modifiche ravvicinate, una richiesta.
export const COLUMN_SYNC_DELAY_MS = 250;
// Tetto dell'attesa della mappa al salvataggio.
export const SAVE_COLUMNS_TIMEOUT_MS = 10000;

const isAbort = (err) => err?.name === 'AbortError' || err?.name === 'CanceledError' || err?.code === 'ERR_CANCELED';

/**
 * L'unica sottoscrizione: ascolta i contatori del canvas (store/
 * graphRevisions: il contenuto; le posizioni solo se ci sono lati senza
 * maniglia) e, dopo un piccolo ritardo, chiede gli schemi del canvas
 * serializzato se la mappa della sua firma non e' gia' nota. Pubblica nello
 * store anche `ensure(canvas)`, che chiede SUBITO la mappa di un canvas
 * preciso (il salvataggio). Restituisce la funzione che la ferma.
 */
export function startColumnSync({
  projectId,
  canvasStore,
  columnStore = useColumnStore,
  fetchSchemas = inferProjectSchemas,
  delay = COLUMN_SYNC_DELAY_MS,
  now = () => Date.now(),
}) {
  let timer = null;
  let stopped = false;
  let force = false;
  // Richieste in volo per firma: { key, promise, controller, waiters }.
  const inflight = new Map();
  const cache = cacheOf(columnStore, projectId);

  columnStore.setState({ ...INITIAL, projectId });

  const currentKey = () => {
    const st = canvasStore.getState();
    if (!st.graphLoaded || st.projectId !== projectId) return null;
    return columnSignature(st.nodes, st.edges);
  };

  const remember = (key, result) => {
    cache.delete(key);
    cache.set(key, { schemas: result.schemas, states: result.states, at: now() });
    while (cache.size > SCHEMA_CACHE_PER_PROJECT) cache.delete(cache.keys().next().value);
  };

  // Regola 2: nello store entra solo la risposta della firma corrente.
  const accept = (key, result) => {
    if (stopped || currentKey() !== key) return;
    columnStore.setState({ schemas: result.schemas, states: result.states, status: 'ok', error: null, key });
  };
  const reject = (key, err) => {
    if (stopped || isAbort(err) || currentKey() !== key) return;
    // Nessuno svuotamento: restano le ultime colonne note, con l'avviso.
    columnStore.setState({ status: 'error', error: humanizeError(err).message });
  };

  // Regola 4: le richieste di altre firme che nessuno aspetta si annullano.
  const abortOthers = (key) => {
    for (const [other, entry] of inflight) {
      if (other === key || entry.waiters > 0) continue;
      entry.controller?.abort();
      inflight.delete(other);
    }
  };

  /** Mappa di un canvas preciso: { key, schemas, states } (una richiesta per firma). */
  const fetchFor = (canvas, key, { wait = false, background = false } = {}) => {
    abortOthers(key);
    const running = inflight.get(key);
    if (running) {
      if (wait) running.waiters += 1;
      return running.promise;
    }
    if (!background && currentKey() === key) columnStore.setState({ status: 'loading' });
    const controller = typeof AbortController === 'function' ? new AbortController() : null;
    const entry = { key, controller, waiters: wait ? 1 : 0 };
    const done = () => { if (inflight.get(key) === entry) inflight.delete(key); };
    const payload = serializeCanvas(canvas.nodes, canvas.edges, { mode: canvas.mode });
    entry.promise = Promise.resolve()
      .then(() => (controller ? fetchSchemas(projectId, payload, { signal: controller.signal }) : fetchSchemas(projectId, payload)))
      .then(
        (data) => {
          done();
          const result = normalizeSchemasResponse(data);
          if (!stopped) remember(key, result);
          accept(key, result);
          return { key, ...result };
        },
        (err) => { done(); reject(key, err); throw err; },
      );
    inflight.set(key, entry);
    return entry.promise;
  };

  const run = () => {
    timer = null;
    if (stopped) return;
    const key = currentKey();
    if (key == null) return;
    const st = columnStore.getState();
    if (!force && st.key === key) {
      // La mappa e' gia' quella di questa firma.
      if (st.status !== 'ok') columnStore.setState({ status: 'ok', error: null });
      return;
    }
    const canvas = canvasStore.getState();
    const snapshot = { nodes: canvas.nodes, edges: canvas.edges, mode: canvas.mode };
    const cached = force ? null : cache.get(key);
    force = false;
    if (cached) {
      // Regola 2: la mappa gia' nota di questa firma, subito.
      columnStore.setState({ schemas: cached.schemas, states: cached.states, status: 'ok', error: null, key });
      abortOthers(key);
      if (now() - cached.at > SCHEMA_FRESH_MS) fetchFor(snapshot, key, { background: true }).catch(() => {});
      return;
    }
    fetchFor(snapshot, key).catch(() => {});
  };

  const schedule = (wait = delay) => {
    if (stopped) return;
    clearTimeout(timer);
    timer = setTimeout(run, wait);
  };

  const refresh = () => { force = true; schedule(0); };

  /** Regola 3: la mappa per la firma di `canvas`, chiesta subito. */
  const ensure = (canvas) => {
    if (stopped) return Promise.reject(new Error('sincronizzazione ferma'));
    const key = columnSignature(canvas.nodes, canvas.edges);
    const st = columnStore.getState();
    if (st.key === key) return Promise.resolve({ key, schemas: st.schemas, states: st.states });
    const cached = cache.get(key);
    if (cached) return Promise.resolve({ key, schemas: cached.schemas, states: cached.states });
    const promise = fetchFor(canvas, key, { wait: true });
    const release = () => {
      const entry = inflight.get(key);
      if (entry && entry.waiters > 0) entry.waiters -= 1;
    };
    promise.then(release, release);
    return promise;
  };
  columnStore.setState({ refresh, ensure });

  // Lati senza maniglia: solo allora una posizione puo' cambiare la firma.
  const initial = canvasStore.getState();
  let looseSides = handlelessOrderKey(initial.nodes, initial.edges) !== '';
  const unsubscribe = canvasStore.subscribe((s, prev) => {
    const contentMoved = s.contentRev !== prev.contentRev
      || s.graphLoaded !== prev.graphLoaded || s.projectId !== prev.projectId;
    if (contentMoved) looseSides = handlelessOrderKey(s.nodes, s.edges) !== '';
    if (contentMoved || (looseSides && s.layoutRev !== prev.layoutRev)) {
      // Nessuna mappa ancora: subito; modifiche successive: dopo il ritardo.
      schedule(columnStore.getState().key === null && inflight.size === 0 ? 0 : delay);
    }
  });
  schedule(0);

  return () => {
    stopped = true;
    clearTimeout(timer);
    unsubscribe();
    for (const entry of inflight.values()) if (entry.waiters === 0) entry.controller?.abort();
    inflight.clear();
    columnStore.setState({ ...INITIAL, refresh: () => {}, ensure: NO_ENSURE });
  };
}

/**
 * Colonne DERIVATE da allegare al salvataggio di `canvas` ({nodes, edges,
 * mode}) (utils/canvasSerializer), secondo la regola 3: la mappa della
 * STESSA firma, o null (niente colonne derivate: le ricalcola il backend).
 * Sincrono quando la risposta e' gia' nota (mappa della stessa firma, o
 * nessuna sincronizzazione per questo progetto); altrimenti una Promise.
 */
export function derivedColumnsForSave(projectId, canvas, {
  columnStore = useColumnStore,
  timeoutMs = SAVE_COLUMNS_TIMEOUT_MS,
} = {}) {
  const st = columnStore.getState();
  if (!projectId || st.projectId !== projectId || !canvas) return null;
  const key = columnSignature(canvas.nodes, canvas.edges);
  if (st.key === key) return st.schemas;
  if (typeof st.ensure !== 'function') return null;
  let timer = null;
  const timeout = new Promise((resolve) => { timer = setTimeout(() => resolve(null), timeoutMs); });
  const wanted = st.ensure(canvas)
    .then(result => (result?.key === key ? result.schemas : null))
    .catch(() => null);
  return Promise.race([wanted, timeout]).finally(() => clearTimeout(timer));
}

export default useColumnStore;
