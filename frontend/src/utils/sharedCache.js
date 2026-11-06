/**
 * sharedCache.js — il motore della cache dati condivisa del client (UI-1,
 * 06/11/2026; audit C1, C2, C8-C11, C17, C27, M-Estrai; conferma A-K4,
 * A-K7, A-K9).
 *
 * Una risorsa (nome + parametri) = UNA richiesta in volo, riusata da pagine,
 * nodi e componenti. Le risorse e la loro freschezza sono la tabella di
 * utils/sharedResources (SHARED_RESOURCES); le scritture che le invalidano
 * sono la tabella di utils/sharedInvalidations (WRITE_INVALIDATIONS), che
 * api/http applica a OGNI risposta di una scrittura; la fine dei task la
 * tabella TASK_INVALIDATIONS (stesso modulo, applicata da utils/taskRegistry).
 *
 * Regole (una per caso):
 *  - in volo: chi chiede la stessa risorsa riceve la stessa Promise;
 *  - fresca (eta' < freshMs): risponde la cache, nessuna richiesta;
 *  - scaduta ma entro STALE_LIMIT_MS: risponde SUBITO la cache e parte UNA
 *    richiesta in background (stale-while-revalidate); all'arrivo gli
 *    iscritti ricaricano; se fallisce gli iscritti ricevono l'errore
 *    (`kind: 'error'`): i dati restano, con «non aggiornati» (A-K9);
 *  - `allowStale: false`: una copia scaduta non vale, si aspetta il server
 *    (chi la userebbe come base di una scrittura, es. il canvas all'apertura
 *    dell'editor, A-K4);
 *  - `force` (reload, Riprova, poll): dal server (una richiesta in volo
 *    partita dopo l'ultima invalidazione vale);
 *  - errore: non resta in cache, la prossima lettura riprova;
 *  - invalidazione (scrittura riuscita o no, fine di un task): le voci
 *    INVALIDATE spariscono e una risposta in volo partita prima non le
 *    riempie; le altre risorse non sono toccate (la «generazione» e' della
 *    voce, non globale: A-K9). Ogni invalidazione si conta
 *    (invalidationCount): chi tiene dati derivati da una risorsa (le mappe
 *    /schemas, A-K7) usa il conteggio come versione;
 *  - fine sessione (utils/session, resetSession): tutto svuotato.
 */
import { registerSessionReset } from './session';
import { invalidationsForWrite } from './sharedInvalidations';

// Oltre questa eta' un dato scaduto non si mostra piu': si aspetta il server.
export const STALE_LIMIT_MS = 10 * 60_000;

const definitions = new Map();
// chiave -> { name, data, fetchedAt, inFlight }
const entries = new Map();
// nome -> Set({ listener, keys })
const listeners = new Map();
// Invalidazioni contate: per nome (tutte, anche quelle di una voce) e per voce.
const invalidations = new Map();

const keyOf = (name, params) => JSON.stringify([name, ...params]);

/** Registra le risorse (utils/sharedResources lo fa al caricamento). */
export function defineSharedResources(table) {
  for (const [name, def] of Object.entries(table)) definitions.set(name, def);
}

// key null = tutte le voci della risorsa.
function notify(name, key, kind, error = null) {
  const event = { name, key, kind, error };
  for (const { listener, keys } of [...(listeners.get(name) || [])]) {
    // Iscritto a voci precise (es. il progetto sorgente X): solo quelle.
    if (key && keys && !keys.has(key)) continue;
    try { listener(name, event); } catch (err) { console.warn('[sharedCache] iscritto non riuscito:', err); }
  }
}

function fetchEntry(name, params, key) {
  const def = definitions.get(name);
  if (!def) return Promise.reject(new Error(`[sharedCache] risorsa sconosciuta: ${name}`));
  const entry = entries.get(key) || { name, data: undefined, fetchedAt: 0, inFlight: null };
  entries.set(key, entry);
  // Aggiornamento di una copia gia' mostrata: il suo errore va a chi la mostra.
  const revalidating = entry.fetchedAt > 0;
  let request;
  try {
    request = Promise.resolve(def.load(...params));
  } catch (err) {
    request = Promise.reject(err);
  }
  // La voce e' ancora la stessa: non invalidata ne' svuotata a fine sessione.
  const current = () => entries.get(key) === entry;
  const tracked = request
    .then(
      (data) => {
        if (current()) {
          entry.data = data;
          entry.fetchedAt = Date.now();
          notify(name, key, 'arrived');
        }
        return data;
      },
      (err) => {
        if (revalidating && current()) notify(name, key, 'error', err);
        throw err;
      },
    )
    .finally(() => { if (entry.inFlight === tracked) entry.inFlight = null; });
  entry.inFlight = tracked;
  return tracked;
}

/**
 * Legge una risorsa condivisa.
 * @param {string} name     nome in SHARED_RESOURCES
 * @param {Array} [params]  parametri del caricatore (es. id)
 * @param {{force?: boolean, maxAgeMs?: number, allowStale?: boolean}} [opts]
 * @returns {Promise<any>}
 */
export function readShared(name, params = [], { force = false, maxAgeMs, allowStale = true } = {}) {
  const def = definitions.get(name);
  if (!def) return Promise.reject(new Error(`[sharedCache] risorsa sconosciuta: ${name}`));
  const key = keyOf(name, params);
  const entry = entries.get(key);
  if (entry?.inFlight && (force || entry.fetchedAt === 0)) return entry.inFlight;
  if (!force && entry && entry.fetchedAt > 0) {
    const age = Date.now() - entry.fetchedAt;
    if (age < (maxAgeMs ?? def.freshMs)) return Promise.resolve(entry.data);
    if (allowStale && age < STALE_LIMIT_MS) {
      // Scaduta: risponde la cache, il server in background (una volta).
      if (!entry.inFlight) {
        fetchEntry(name, params, key).catch((err) => console.warn(`[sharedCache] aggiornamento di ${name} non riuscito:`, err));
      }
      return Promise.resolve(entry.data);
    }
  }
  if (entry?.inFlight) return entry.inFlight;
  return fetchEntry(name, params, key);
}

/** Il valore in cache adesso (anche scaduto, entro STALE_LIMIT_MS): { data, fresh } o null. */
export function peekShared(name, params = []) {
  const def = definitions.get(name);
  const entry = entries.get(keyOf(name, params));
  if (!def || !entry || entry.fetchedAt === 0) return null;
  const age = Date.now() - entry.fetchedAt;
  if (age >= STALE_LIMIT_MS) return null;
  return { data: entry.data, fresh: age < def.freshMs };
}

const bump = (k) => invalidations.set(k, (invalidations.get(k) || 0) + 1);

/**
 * Quante volte una risorsa e' stata invalidata in questa pagina (anche per
 * una sola voce), tolte le invalidazioni delle voci `except` (es. il
 * progetto aperto, che si salva da solo): la «versione» dei dati che ne
 * derivano.
 * @param {string} name
 * @param {{except?: Array<Array>}} [opts]  parametri delle voci da non contare
 */
export function invalidationCount(name, { except = [] } = {}) {
  const own = except.reduce((sum, params) => sum + (invalidations.get(keyOf(name, params)) || 0), 0);
  return (invalidations.get(name) || 0) - own;
}

/**
 * Invalida: un nome = tutte le voci della risorsa; [nome, ...parametri] =
 * solo quella voce.
 */
export function invalidateShared(names) {
  const list = Array.isArray(names) ? names : [names];
  if (list.length === 0) return;
  const events = [];
  for (const item of list) {
    if (Array.isArray(item)) {
      const [name, ...params] = item;
      const key = keyOf(name, params);
      entries.delete(key);
      bump(name);
      bump(key);
      events.push([name, key]);
    } else {
      for (const [key, entry] of entries) {
        if (entry.name === item) entries.delete(key);
      }
      bump(item);
      events.push([item, null]);
    }
  }
  events.forEach(([name, key]) => notify(name, key, 'invalidated'));
}

/** Dopo una scrittura (api/http): invalida quello che la tabella dice. */
export function invalidateAfterWrite(method, url) {
  invalidateShared(invalidationsForWrite(method, url));
}

/**
 * Iscrizione: `listener(name, { kind, key, error })` quando una delle
 * risorse viene invalidata (`invalidated`), arriva nuova (`arrived`) o il
 * suo aggiornamento in background fallisce (`error`).
 * `names` = nomi o [nome, ...parametri] (solo quelle voci).
 * @returns {() => void}
 */
export function subscribeShared(names, listener) {
  // nome -> chiavi precise (null = tutte le voci).
  const byName = new Map();
  for (const n of (Array.isArray(names) ? names : [names])) {
    const [name, ...params] = Array.isArray(n) ? n : [n];
    if (params.length === 0) byName.set(name, null);
    else if (byName.get(name) !== null) byName.set(name, new Set([...(byName.get(name) || []), keyOf(name, params)]));
  }
  const subs = [];
  for (const [name, keys] of byName) {
    const sub = { listener, keys };
    if (!listeners.has(name)) listeners.set(name, new Set());
    listeners.get(name).add(sub);
    subs.push([name, sub]);
  }
  return () => subs.forEach(([name, sub]) => listeners.get(name)?.delete(sub));
}

/** Svuota la cache (fine sessione; i test). */
export function resetSharedCache() {
  entries.clear();
}

registerSessionReset('sharedCache', resetSharedCache);
