/**
 * connectionSets.js — i set di connessioni come risorsa CONDIVISA della
 * sessione (Fase 5, correzione dopo la conferma T1+T3, punto 12).
 *
 * Regola: N nodi "Input di progetto" montati (e la finestra «Prima di
 * avviare») fanno UNA richiesta GET /connection-sets:
 *  - una richiesta in volo si condivide (deduplica);
 *  - l'esito vale per CACHE_TTL_MS (i nodi aperti insieme condividono una
 *    sola risposta, ma set creati/condivisi da altri o in un'altra scheda
 *    compaiono alla lettura successiva, rilettura T1+T3 R3); chi modifica i
 *    set (ConnectionSetsPanel) chiama invalidateConnectionSets();
 *  - resetSession() (logout, anche da 401) la svuota: registrata nel
 *    registro della sessione (utils/session).
 * Una richiesta fallita non resta: la prossima lettura riprova.
 */
import { listConnectionSets } from '../api/connections';
import { registerSessionReset } from './session';

// Validita' della risposta condivisa: abbastanza per i nodi montati insieme.
export const CACHE_TTL_MS = 30_000;

let cached = null;
let cachedAt = 0;
let inFlight = null;
// Generazione: una risposta arrivata dopo invalidate/reset non riempie la cache.
let generation = 0;

/** I set di connessioni della sessione: Promise<Array>. */
export function loadSharedConnectionSets() {
  if (cached && Date.now() - cachedAt < CACHE_TTL_MS) return Promise.resolve(cached);
  if (inFlight) return inFlight;
  const mine = generation;
  const request = listConnectionSets()
    .then((value) => {
      const items = value?.connection_sets || [];
      if (mine === generation) { cached = items; cachedAt = Date.now(); }
      return items;
    })
    .finally(() => { if (inFlight === request) inFlight = null; });
  inFlight = request;
  return request;
}

/** Dopo una modifica dei set: la prossima lettura li chiede al server. */
export function invalidateConnectionSets() {
  generation += 1;
  cached = null;
  cachedAt = 0;
  inFlight = null;
}

registerSessionReset('connectionSets', invalidateConnectionSets);
