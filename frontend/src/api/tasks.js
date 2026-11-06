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

import http from './http';
import { downloadBlob } from '../utils/downloadBlob';
import { downloadProgress } from '../utils/transferProgress';
import { requestErrorMessage } from '../utils/errorMessages';

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
  try {
    const response = await http.delete(`/tasks/${taskId}/full-cleanup`);
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
    console.warn('[cleanupTaskCompletely] errore cleanup:', e?.message || e);
    return {
      partialCleanup: true,
      networkError: true,
      errorMessage: requestErrorMessage(e),
    };
  }
};
