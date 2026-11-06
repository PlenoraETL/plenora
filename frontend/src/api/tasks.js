/**
 * tasks.js — API per la gestione dei task asincroni (worker Dramatiq)
 *
 * URBY-ETL esegue le operazioni pesanti (esecuzione, preview, arricchimento)
 * in modo asincrono tramite il worker. Questi endpoint permettono di monitorare
 * e controllare i task in esecuzione.
 *
 * Il polling NON si scrive a mano: hooks/useTask (un task) o
 * utils/taskPoller (piu' task) applicano le regole uniche di fine, 404,
 * errori ripetuti e annullamento (Fase 5 T2; lo vieta
 * tests/shared/asyncArchitecture.contract.test.js).
 */

import http, { sendOnPageExit } from './http';
import { downloadBlob } from '../utils/downloadBlob';
import { downloadProgress } from '../utils/transferProgress';
import { requestErrorMessage } from '../utils/errorMessages';
import { parkExitCleanup, parkedExitCleanups, unparkExitCleanup } from '../utils/exitCleanups';

/**
 * Restituisce lo stato corrente di un task asincrono.
 *
 * @param {string} taskId
 * @returns {Promise<Object>} { task_id, status, progress, message, result?, error? }
 *   status: 'pending' | 'started' | 'progress' | 'success' | 'failure'
 */
export const getTaskStatus = async (taskId) => {
  const response = await http.get(`/tasks/${taskId}`);
  return response.data;
};

/**
 * Annulla un task in esecuzione.
 * @param {string} taskId
 * @returns {Promise<Object>} { status: 'cancellation_requested', task_id }
 */
export const cancelTask = async (taskId) => {
  const response = await http.delete(`/tasks/${taskId}`);
  return response.data;
};

/**
 * Scarica il risultato di un task autenticato tramite axios (non navigazione diretta).
 * Necessario perché il token JWT è in sessionStorage e non viene allegato
 * automaticamente dal browser nelle navigazioni dirette.
 *
 * @param {string} taskId
 * @param {string} [filename] - Nome file suggerito (sovrascrivibile da Content-Disposition)
 * @returns {Promise<void>}
 */
export const downloadTaskOutput = async (taskId, filename = null, { onProgress = null } = {}) => {
  // Pre-fix (P1 contracts-flows): il 410 Gone (output scaduto/cancellato,
  // tipico dei task con TTL retention) cadeva nell'interceptor http
  // come errore generico. L'utente vedeva "Errore di comunicazione con
  // il server" senza capire il motivo. Post-fix: gestione 410 esplicita
  // con messaggio dedicato. Mappa al cleanup GDPR-aware del retention.
  try {
    const response = await http.get(`/tasks/${taskId}/download`, {
      responseType: 'blob',
      ...downloadProgress(onProgress),
    });
    downloadBlob(response, filename, `task_${taskId}_output`);
  } catch (err) {
    if (err?.response?.status === 410) {
      const customErr = new Error(
        "Il file del risultato non è più disponibile: è scaduto il tempo di conservazione. " +
        "Esegui di nuovo per ottenere un nuovo risultato."
      );
      customErr.code = 'TASK_OUTPUT_GONE';
      customErr.status = 410;
      throw customErr;
    }
    throw err;
  }
};

/*
 * Pulizie dell'esecutore: UNA regola (semplificazione H2, 06/11/2026). Ogni
 * pulizia si annota (utils/exitCleanups) PRIMA di partire e si toglie quando
 * il server l'ha chiusa: 204 (fatta, o non c'era piu' niente da pulire) o
 * 403/404 (il task non e' piu' di questo utente: non si potra' mai fare).
 * 202 (in corso: annullato, la pulizia la fa chi lo chiude), 207 (passi non
 * riusciti), 401, 5xx o rete: resta annotata e la ripete la prossima apertura
 * di /run, che con il task gia' pulito riceve subito 204 (nessuna attesa ne'
 * revoca sul server) e la toglie. Mai ripetizioni per una pulizia gia' chiusa.
 */
const SETTLED = new Set([204, 403, 404]);
const fullCleanupUrl = (taskId) => `/tasks/${encodeURIComponent(taskId)}/full-cleanup`;
const settle = (taskId, status) => {
  if (!SETTLED.has(status)) return false;
  unparkExitCleanup(taskId);
  return true;
};

/**
 * Pulizia all'uscita (scheda chiusa, uscita dall'account, pagina lasciata;
 * UI conferma B-K4): UNA richiesta DELETE /tasks/{id}/full-cleanup con
 * `fetch keepalive` (http.sendOnPageExit), che parte subito col token di
 * adesso. L'ordine «prima annulla, poi pulisci» e' del server
 * (core/orchestrator/task_cleanup). Se la risposta arriva (pagina ancora
 * viva) l'annotazione si toglie secondo la regola sopra.
 * @returns {boolean} true se la richiesta e' partita.
 */
export const releaseTaskOnPageExit = (taskId) => {
  parkExitCleanup(taskId);
  return sendOnPageExit('DELETE', fullCleanupUrl(taskId), (status) => settle(taskId, status));
};

/**
 * Uscita da un'esecuzione FUORI da /run (H4): l'esecuzione in corso si
 * annulla (DELETE /tasks/{id}), con la stessa keepalive; niente pulizia.
 * @returns {boolean} true se la richiesta e' partita.
 */
export const cancelTaskOnPageExit = (taskId) => sendOnPageExit('DELETE', `/tasks/${encodeURIComponent(taskId)}`);

/**
 * Pagina messa nella cache del browser (`pagehide` con `persisted`,
 * conferma finale G4): niente pulizia adesso (la pagina puo' tornare con il
 * risultato), solo l'annotazione. Torna -> `unparkExitCleanup`; non torna ->
 * la pulizia la fa la prossima apertura di /run.
 */
export const parkTaskCleanup = (taskId) => parkExitCleanup(taskId);

/**
 * All'apertura di /run: le pulizie annotate ripartono (idempotenti) e si
 * tolgono secondo la regola sopra.
 * @returns {Promise<number>} quante ne ha chiuse.
 */
export const flushParkedExitCleanups = async () => {
  let closed = 0;
  for (const taskId of parkedExitCleanups()) {
    let status;
    try {
      status = (await http.delete(fullCleanupUrl(taskId))).status;
    } catch (err) {
      status = err?.response?.status;
    }
    if (settle(taskId, status)) closed += 1;
  }
  return closed;
};

/**
 * Cleanup completo di un task: elimina result file/blob, log entries e
 * revoca il record del task sul server. Pensato per il ruolo "esecutore"
 * che richiede privacy GDPR-style: la UI kiosk chiama questo endpoint
 * subito dopo aver scaricato il risultato così non resta traccia.
 *
 * Tollerante agli errori: il backend fa best-effort, non lancia
 * eccezioni se uno dei layer di cleanup fallisce. Qui catturiamo
 * comunque per non bloccare la UX in caso di errore di rete.
 *
 * @param {string} taskId
 * @returns {Promise<void>}
 */
export const cleanupTaskCompletely = async (taskId) => {
  // Return shape:
  //   { partialCleanup: false, ...data }      # 200 OK pulito
  //   { partialCleanup: true, failedSteps, ...data }  # 207 alcuni step falliti
  //   { partialCleanup: true, networkError: true, ...}  # eccezione richiesta
  //
  // Pre-fix (P0 contracts-flows): 207 era ignored solo con console.warn.
  // Il claim GDPR "nessuna traccia dell'esecuzione" del ruolo esecutore
  // veniva VIOLATO silenziosamente — l'utente vedeva "download
  // completato + cleanup OK" mentre alcuni step lasciavano dati residui
  // (es. delete_log_runs fallito = log persistito sul server).
  //
  // Post-fix: la funzione ritorna sempre un oggetto strutturato col
  // flag `partialCleanup`. Il caller (EsecutoreHome.handleAfterDownload)
  // puo' mostrare un toast/banner di trasparenza all'utente: "Cleanup
  // completato parzialmente, alcuni dati possono richiedere intervento
  // manuale dell'amministratore".
  // Annotata prima di partire, tolta quando il server la chiude (regola sopra).
  parkExitCleanup(taskId);
  try {
    const response = await http.delete(fullCleanupUrl(taskId));
    settle(taskId, response.status);
    // 202: task ancora in corso, annullato; la pulizia la fa chi lo porta
    // allo stato terminale (worker o beat). Non e' parziale.
    if (response.status === 202) {
      return { partialCleanup: false, deferred: true, ...response.data };
    }
    if (response.status === 207) {
      const failedSteps = response.data?.failed_steps || [];
      console.warn(
        '[cleanupTaskCompletely] cleanup parziale (HTTP 207):',
        { taskId, failedSteps, partialStatus: response.data?.status },
      );
      return {
        partialCleanup: true,
        failedSteps,
        ...response.data,
      };
    }
    return {
      partialCleanup: false,
      ...response.data,
    };
  } catch (e) {
    settle(taskId, e?.response?.status);
    console.warn('[cleanupTaskCompletely] errore cleanup:', e?.message || e);
    return {
      partialCleanup: true,
      networkError: true,
      errorMessage: requestErrorMessage(e),
    };
  }
};
