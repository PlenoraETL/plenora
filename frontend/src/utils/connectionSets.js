/**
 * connectionSets.js — i set di connessioni come risorsa CONDIVISA della
 * sessione (Fase 5, correzione dopo la conferma T1+T3, punto 12).
 *
 * Dal 06/11/2026 (UI-1) e' una voce della cache condivisa unica
 * (utils/sharedResources, risorsa `connectionSets`), con le sue regole:
 *  - N nodi "Input di progetto" montati (e la finestra «Prima di avviare»)
 *    fanno UNA richiesta GET /connection-sets (deduplica);
 *  - la risposta e' fresca per `freshMs` della tabella, poi si mostra e si rilegge in
 *    background (set creati/condivisi da altri compaiono alla lettura dopo);
 *  - ogni scrittura su /connection-sets la invalida (api/http, tabella
 *    utils/sharedInvalidations); invalidateConnectionSets() resta per chi
 *    vuole dirlo esplicitamente;
 *  - resetSession() (logout, anche da 401) la svuota.
 * Una richiesta fallita non resta: la prossima lettura riprova.
 */
import { readShared, invalidateShared } from './sharedResources';
import { registerSessionReset } from './session';


/** I set di connessioni della sessione: Promise<Array>. `force` dal server (Riprova). */
export function loadSharedConnectionSets({ force = false } = {}) {
  return readShared('connectionSets', [], { force }).then((value) => value?.connection_sets || []);
}

/** Dopo una modifica dei set: la prossima lettura li chiede al server. */
export function invalidateConnectionSets() {
  invalidateShared('connectionSets');
}

registerSessionReset('connectionSets', invalidateConnectionSets);
