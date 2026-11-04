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
 *  1. ogni mappa porta la firma del canvas che il server ha letto;
 *  2. una risposta entra nello store solo se la sua firma e' quella del
 *     canvas CORRENTE; se no si scarta e basta (nessuna memoria di "gia'
 *     chiesto" che possa bloccare una richiesta futura per la stessa firma);
 *  3. il salvataggio allega le colonne derivate solo per la firma del canvas
 *     che salva: se la mappa e' di un'altra firma (o manca) la chiede subito
 *     e la attende (con tetto); se non arriva, non allega niente (il backend
 *     le ricalcola). Mai colonne di una firma diversa.
 */
import { create } from 'zustand';
import { inferProjectSchemas } from '../api';
import { serializeCanvas } from '../utils/canvasSerializer';
import { columnSignature, normalizeSchemasResponse } from '../utils/columnSource';
import { humanizeError } from '../utils/errorMessages';

const NO_ENSURE = null;
const INITIAL = { projectId: null, schemas: {}, states: {}, status: 'idle', error: null, key: null };

export const createColumnStore = () => create(() => ({ ...INITIAL, refresh: () => {}, ensure: NO_ENSURE }));

const useColumnStore = createColumnStore();

// Ritardo dopo l'ultima modifica: piu' modifiche ravvicinate, una richiesta.
export const COLUMN_SYNC_DELAY_MS = 250;
// Tetto dell'attesa della mappa al salvataggio.
export const SAVE_COLUMNS_TIMEOUT_MS = 10000;

/**
 * L'unica sottoscrizione: ascolta il canvas e, dopo un piccolo ritardo,
 * chiede gli schemi del canvas serializzato se la mappa non e' gia' quella
 * della sua firma. Pubblica nello store anche `ensure(canvas)`, che chiede
 * SUBITO la mappa di un canvas preciso (il salvataggio). Restituisce la
 * funzione che la ferma.
 */
export function startColumnSync({
  projectId,
  canvasStore,
  columnStore = useColumnStore,
  fetchSchemas = inferProjectSchemas,
  delay = COLUMN_SYNC_DELAY_MS,
}) {
  let timer = null;
  let stopped = false;
  let force = false;
  // La richiesta in volo, con la firma che porta: una per firma.
  let inflight = null;

  columnStore.setState({ ...INITIAL, projectId });

  const currentKey = () => {
    const st = canvasStore.getState();
    if (!st.graphLoaded || st.projectId !== projectId) return null;
    return columnSignature(st.nodes, st.edges);
  };

  // Regola 2: entra solo la risposta della firma corrente.
  const accept = (key, result) => {
    if (stopped || currentKey() !== key) return;
    columnStore.setState({ ...result, status: 'ok', error: null, key });
  };
  const reject = (key, err) => {
    if (stopped || currentKey() !== key) return;
    // Nessuno svuotamento: restano le ultime colonne note, con l'avviso.
    columnStore.setState({ status: 'error', error: humanizeError(err).message });
  };

  /** Mappa di un canvas preciso: { key, schemas, states } (una richiesta per firma). */
  const fetchFor = (canvas, key) => {
    if (inflight?.key === key) return inflight.promise;
    if (currentKey() === key) columnStore.setState({ status: 'loading' });
    const entry = { key };
    const done = () => { if (inflight === entry) inflight = null; };
    entry.promise = Promise.resolve()
      .then(() => fetchSchemas(projectId, serializeCanvas(canvas.nodes, canvas.edges, { mode: canvas.mode })))
      .then(
        (data) => { done(); const result = normalizeSchemasResponse(data); accept(key, result); return { key, ...result }; },
        (err) => { done(); reject(key, err); throw err; },
      );
    inflight = entry;
    return entry.promise;
  };

  const run = () => {
    timer = null;
    if (stopped) return;
    const key = currentKey();
    if (key == null) return;
    const st = columnStore.getState();
    if (!force && st.key === key) {
      // La mappa e' gia' quella di questa firma (es. dopo un Annulla).
      if (st.status !== 'ok') columnStore.setState({ status: 'ok', error: null });
      return;
    }
    force = false;
    const canvas = canvasStore.getState();
    fetchFor({ nodes: canvas.nodes, edges: canvas.edges, mode: canvas.mode }, key).catch(() => {});
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
    return fetchFor(canvas, key);
  };
  columnStore.setState({ refresh, ensure });

  const unsubscribe = canvasStore.subscribe((s, prev) => {
    if (s.nodes !== prev.nodes || s.edges !== prev.edges
      || s.graphLoaded !== prev.graphLoaded || s.projectId !== prev.projectId) {
      // Nessuna mappa ancora: subito; modifiche successive: dopo il ritardo.
      schedule(columnStore.getState().key === null && !inflight ? 0 : delay);
    }
  });
  schedule(0);

  return () => {
    stopped = true;
    clearTimeout(timer);
    unsubscribe();
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
