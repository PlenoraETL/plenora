/**
 * taskOutcome.js — UNA regola per leggere lo stato di un task asincrono
 * (Fase 5 T2, audit R4.11, R4.12, R9.2, N12).
 *
 * Ogni risposta di GET /tasks/{id} (o la risposta sincrona di un avvio)
 * diventa un esito con una fase sola:
 *
 * | fase               | quando                                                        |
 * |--------------------|---------------------------------------------------------------|
 * | running            | pending, started, progress, retry                             |
 * | success            | success e il risultato non dice il contrario                  |
 * | failure            | failure; success con `result.success === false`; con         |
 * |                    | `businessStatus` anche un `result.status` diverso da          |
 * |                    | success/completed/partial_success; stato sconosciuto          |
 * | revoked            | revoked (annullata)                                           |
 * | permission_revoked | `result.status === 'permission_revoked'`, con qualunque stato |
 * |                    | del task (permessi tolti durante l'esecuzione)                |
 * | lost               | il task non si legge piu' (404 ripetuti, errori ripetuti,     |
 * |                    | 401/403): lo decide utils/taskPoller, mai questo modulo       |
 *
 * Mai "completata" falsa: solo `success` mostra il successo e il download.
 */
import { taskFailureMessage } from './taskFailureMessage';

export const TASK_PHASES = Object.freeze(['idle', 'starting', 'running', 'success', 'failure', 'revoked', 'permission_revoked', 'lost']);
const RUNNING_STATUSES = Object.freeze(['pending', 'started', 'progress', 'retry']);
const TERMINAL_PHASES = Object.freeze(['success', 'failure', 'revoked', 'permission_revoked', 'lost']);
const BUSINESS_OK = new Set(['success', 'completed', 'partial_success']);

/** Fase attiva: avvio in corso o task che gira. */
export const isActivePhase = (phase) => phase === 'starting' || phase === 'running';
/** Fase finale (esito da mostrare). */
export const isTerminalPhase = (phase) => TERMINAL_PHASES.includes(phase);

const permissionRevokedMessage = (result) => result?.message || "Permessi revocati durante l'esecuzione";

/**
 * @param {Object} data  risposta di GET /tasks/{id}
 * @param {{businessStatus?: boolean, failureFallback?: string}} [opts]
 * @returns {{phase: string, done: boolean, status: string|null, progress: number,
 *            message: string, result: Object|null, error: string|null, code: string|null}}
 */
export function taskOutcome(data, { businessStatus = false, failureFallback = 'Esecuzione non riuscita' } = {}) {
  const status = data?.status ?? null;
  const result = data?.result && typeof data.result === 'object' ? data.result : null;
  const base = {
    status,
    progress: Number(data?.progress) || 0,
    message: data?.message || '',
    result,
    error: null,
    code: null,
  };
  const end = (phase, extra = {}) => ({ ...base, phase, done: true, ...extra });

  if (result?.status === 'permission_revoked') {
    return end('permission_revoked', { error: permissionRevokedMessage(result), code: 'permission_revoked' });
  }
  if (RUNNING_STATUSES.includes(status)) return { ...base, phase: 'running', done: false };
  if (status === 'revoked') return end('revoked', { error: result?.message || "Esecuzione annullata dall'utente" });
  if (status === 'failure') {
    return end('failure', { error: taskFailureMessage(data, failureFallback), code: data?.error || result?.error || null });
  }
  if (status === 'success') {
    if (result?.success === false) {
      return end('failure', { error: result.message || failureFallback, code: result.error || null });
    }
    if (businessStatus && result?.status && !BUSINESS_OK.has(result.status)) {
      return end('failure', { error: taskFailureMessage(data, result.status), code: result.error || null });
    }
    return end('success', { progress: 100 });
  }
  return end('failure', { error: "Stato dell'operazione non riconosciuto", code: 'unknown_task_status' });
}

/** Esito di un avvio sincrono (il server ha risposto subito con il risultato). */
export const syncOutcome = (response, opts) => taskOutcome({ status: 'success', progress: 100, result: response }, opts);
