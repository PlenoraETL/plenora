/**
 * tasks.js — API per la gestione dei task asincroni (worker Dramatiq)
 *
 * URBY-ETL esegue le operazioni pesanti (esecuzione, preview, arricchimento)
 * in modo asincrono tramite il worker. Questi endpoint permettono di monitorare
 * e controllare i task in esecuzione.
 *
 * Pattern di polling consigliato:
 *   const { task_id } = await executeProjectQuery(projectId);
 *   const interval = setInterval(async () => {
 *     const status = await getTaskStatus(task_id);
 *     if (status.status === 'success' || status.status === 'failure') {
 *       clearInterval(interval);
 *     }
 *   }, 1000);
 */

import http from './http';
import { downloadBlob } from '../utils/downloadBlob';

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
 * @returns {Promise<Object>} { status: 'cancelled', task_id }
 */
export const cancelTask = async (taskId) => {
  const response = await http.delete(`/tasks/${taskId}`);
  return response.data;
};

/**
 * Crea un nuovo task asincrono con payload personalizzato.
 * Usato per task di tipo 'carica' con mappatura colonne esplicita.
 *
 * Pre-fix (P0 contracts-schemas): il JSDoc non documentava
 * `payload.canvas` che e' REQUIRED nel modello BE `CreateTaskRequest`
 * (no default). Caller che ometteva canvas → 422 Unprocessable Entity.
 * Post-fix: doc esplicita + assert client-side per fallire fast.
 *
 * @param {Object} payload
 * @param {string}   payload.task_type            - Tipo task ('carica', 'enrichment', ...)
 * @param {string}   payload.project_id           - ID progetto
 * @param {Object}   payload.canvas               - **REQUIRED** Canvas serializzato {nodes, edges}
 * @param {string}   [payload.output_format]      - 'csv' | 'parquet' | ... default 'csv'
 * @param {Object}   [payload.carica_node_data]   - **REQUIRED se task_type==='carica'**
 *                                                  Config nodo CARICA (mapping colonne, mode, target).
 *                                                  Il BE risponde 400 se task_type='carica' e questo
 *                                                  campo manca (vedi orchestrator/router.py:324-325).
 * @param {Object}   [payload.options]            - Opzioni runtime
 * @returns {Promise<{task_id: string}>}
 */
export const createTask = async (payload) => {
  if (!payload || !payload.canvas) {
    // Fail-fast client-side: senza canvas il BE risponde 422.
    // Meglio sollevare qui con un messaggio chiaro che aspettare
    // l'errore HTTP poco specifico dell'interceptor.
    throw new Error(
      'createTask: payload.canvas e\' required (shape {nodes, edges}). ' +
      'Il caller deve fornire il canvas serializzato del progetto.'
    );
  }
  // Pre-fix (P1 contracts-schemas): `carica_node_data` Optional BE
  // ma il router risponde 400 condizionale quando task_type='carica'.
  // Caller che ometteva il campo per task carica facevano roundtrip
  // inutile. Fail-fast client-side allineato al check BE.
  if (payload.task_type === 'carica' && !payload.carica_node_data) {
    throw new Error(
      'createTask: payload.carica_node_data e\' required quando ' +
      'task_type === "carica" (mapping colonne + target DB).'
    );
  }
  const response = await http.post('/tasks', payload);
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
export const downloadTaskOutput = async (taskId, filename = null) => {
  // Pre-fix (P1 contracts-flows): il 410 Gone (output scaduto/cancellato,
  // tipico dei task con TTL retention) cadeva nell'interceptor http
  // come errore generico. L'utente vedeva "Errore di comunicazione con
  // il server" senza capire il motivo. Post-fix: gestione 410 esplicita
  // con messaggio dedicato. Mappa al cleanup GDPR-aware del retention.
  try {
    const response = await http.get(`/tasks/${taskId}/download`, {
      responseType: 'blob',
    });
    downloadBlob(response, filename, `task_${taskId}_output`);
  } catch (err) {
    if (err?.response?.status === 410) {
      const customErr = new Error(
        "Il file di output non è più disponibile: scaduto per la policy di retention. " +
        "Esegui nuovamente il task per generare un nuovo output."
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
      errorMessage: e?.message || String(e),
    };
  }
};
