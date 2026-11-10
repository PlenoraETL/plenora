/**
 * pendingRun — richieste della home per catene alla pagina che apre
 * (09/11/2026; 10/11/2026 anche «Modifica» di una connessione).
 *
 * «Esegui»: l'esecuzione di un progetto ha un solo percorso: «Esegui»
 * dell'editor (editor/PipelineRunner: salva, valida, «Prima di avviare»,
 * avvio). La home non ne fa una copia: chiede l'esecuzione e apre l'editor,
 * che la avvia una volta sola appena il canvas di QUEL progetto e' caricato.
 *
 * «Modifica» di una connessione: la home apre la pagina Connessioni, che
 * apre la SUA finestra di modifica per quella connessione (la stessa del
 * pulsante ✏️ della tabella).
 *
 * Regole (per ogni tipo di richiesta, PENDING_KINDS): una richiesta alla
 * volta; vale per l'oggetto chiesto e per poco (REQUEST_TTL_MS: una pagina
 * aperta molto dopo, o di un altro oggetto, non la esegue); presa una volta,
 * non c'e' piu'; l'uscita dall'account la toglie (utils/session).
 */
import { registerSessionReset } from './session';

const REQUEST_TTL_MS = 60_000;

/** Tipi di richiesta: 'run' (id del progetto), 'editConnection' (`db:<id>` | `arcgis:<id>`). */
const PENDING_KINDS = Object.freeze(['run', 'editConnection']);

const pending = new Map();

/** Chiede alla pagina che sta per aprirsi l'azione `kind` su `id`. */
export function requestOnOpen(kind, id, now = Date.now()) {
  if (!PENDING_KINDS.includes(kind)) throw new Error(`Richiesta sconosciuta: ${kind}`);
  if (id) pending.set(kind, { id: String(id), at: now });
  else pending.delete(kind);
}

/** La pagina prende la richiesta `kind` per `id` (true una volta sola). */
export function takeOnOpen(kind, id, now = Date.now()) {
  const p = pending.get(kind);
  if (!p) return false;
  if (now - p.at > REQUEST_TTL_MS) { pending.delete(kind); return false; }
  if (p.id !== String(id)) return false;
  pending.delete(kind);
  return true;
}

/** La home chiede «Esegui» per il progetto che sta per aprire. */
export const requestRunOnOpen = (projectId, now = Date.now()) => requestOnOpen('run', projectId, now);

/** L'editor del progetto `projectId` prende la richiesta (true una volta sola). */
export const takeRunOnOpen = (projectId, now = Date.now()) => takeOnOpen('run', projectId, now);

/** Chiave della richiesta «Modifica» di una connessione: categoria della pagina + id. */
export const connectionEditKey = (category, id) => `${category === 'arcgis' ? 'arcgis' : 'db'}:${id}`;

registerSessionReset('pendingRun', () => { pending.clear(); });
