/**
 * pendingRun — «Esegui» dalla home per catene (09/11/2026).
 *
 * L'esecuzione di un progetto ha un solo percorso: «Esegui» dell'editor
 * (editor/PipelineRunner: salva, valida, «Prima di avviare», avvio). La home
 * non ne fa una copia: chiede l'esecuzione e apre l'editor, che la avvia una
 * volta sola appena il canvas di QUEL progetto e' caricato.
 *
 * Regole: una richiesta alla volta; vale per il progetto chiesto e per poco
 * (RUN_REQUEST_TTL_MS: un editor aperto molto dopo, o di un altro progetto,
 * non parte da solo); presa una volta, non c'e' piu'; l'uscita dall'account
 * la toglie (utils/session).
 */
import { registerSessionReset } from './session';

const RUN_REQUEST_TTL_MS = 60_000;

let pending = null;

/** La home chiede «Esegui» per il progetto che sta per aprire. */
export function requestRunOnOpen(projectId, now = Date.now()) {
  pending = projectId ? { projectId: String(projectId), at: now } : null;
}

/** L'editor del progetto `projectId` prende la richiesta (true una volta sola). */
export function takeRunOnOpen(projectId, now = Date.now()) {
  if (!pending) return false;
  if (now - pending.at > RUN_REQUEST_TTL_MS) { pending = null; return false; }
  if (pending.projectId !== String(projectId)) return false;
  pending = null;
  return true;
}

registerSessionReset('pendingRun', () => { pending = null; });
