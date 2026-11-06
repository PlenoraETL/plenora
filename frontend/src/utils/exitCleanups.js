/**
 * exitCleanups — pulizie dell'esecutore che la pagina non ha visto finire
 * (conferma finale G4, G10; 06/11/2026).
 *
 * Chiudendo la scheda, /run manda la pulizia completa con una fetch
 * `keepalive` (api/tasks.releaseTaskOnPageExit), di cui non vede l'esito: con
 * un'API su un'altra origine (apiBaseUrl assoluto) serve un preflight CORS
 * e alcuni browser non fanno partire una keepalive con preflight, in
 * silenzio. E una pagina messa nella cache del browser (`pagehide` con
 * `persisted`) puo' tornare: pulire subito le toglierebbe il risultato.
 * Regola unica: la pulizia si ANNOTA qui prima di partire (o al posto di
 * partire, con la pagina in cache) e si toglie solo quando il server ha
 * risposto (api/tasks.flushParkedExitCleanups, all'apertura di /run) o
 * quando la pagina torna dalla cache. Una pulizia ripetuta e' innocua (il
 * server la rende idempotente).
 *
 * Solo id di task, per utente (riga `exitCleanup` di utils/browserStorage),
 * restano all'uscita dall'account: sono pulizie da finire, non dati.
 */
import { readJson, storageKey, writeJson, removeKey } from './browserStorage';
import { currentDraftUser } from './localDrafts';

const MAX_PARKED = 50;

const keyFor = (user = currentDraftUser()) => (user ? storageKey('exitCleanup', user) : null);

export function parkedExitCleanups(user) {
  const list = readJson('exitCleanup', keyFor(user), []);
  return Array.isArray(list) ? list.map(String) : [];
}

function save(list, user) {
  const key = keyFor(user);
  if (!key) return false;
  if (list.length === 0) { removeKey('exitCleanup', key); return true; }
  return writeJson('exitCleanup', key, list.slice(-MAX_PARKED));
}

/** Annota una pulizia da finire (idempotente). false = nessun utente o storage. */
export function parkExitCleanup(taskId, user) {
  const id = String(taskId);
  const list = parkedExitCleanups(user);
  return save(list.includes(id) ? list : [...list, id], user);
}

/** Toglie l'annotazione; true se c'era (la pagina e' tornata prima della pulizia). */
export function unparkExitCleanup(taskId, user) {
  const id = String(taskId);
  const list = parkedExitCleanups(user);
  if (!list.includes(id)) return false;
  save(list.filter(x => x !== id), user);
  return true;
}
