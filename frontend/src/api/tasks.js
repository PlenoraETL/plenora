/**
 * tasks.js — API per la gestione dei task Celery
 *
 * URBY-ETL esegue le operazioni pesanti (esecuzione, preview, arricchimento)
 * in modo asincrono tramite Celery. Questi endpoint permettono di monitorare
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
 * Restituisce lo stato corrente di un task Celery.
 *
 * @param {string} taskId
 * @returns {Promise<Object>} { task_id, status, progress, message, result?, error? }
 *   status: 'PENDING' | 'STARTED' | 'PROGRESS' | 'SUCCESS' | 'FAILURE'
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
 * Crea un nuovo task Celery con payload personalizzato.
 * Usato per task di tipo 'carica' con mappatura colonne esplicita.
 * @param {Object} payload
 * @param {string}   payload.task_type
 * @param {string}   payload.project_id
 * @param {Object}   [payload.options]
 * @returns {Promise<{task_id: string}>}
 */
export const createTask = async (payload) => {
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
  const response = await http.get(`/tasks/${taskId}/download`, {
    responseType: 'blob',
  });

  downloadBlob(response, filename, `task_${taskId}_output`);
};

/**
 * Cleanup completo di un task: elimina result file/blob, log entries e
 * revoca il record Celery sul server. Pensato per il ruolo "esecutore"
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
  try {
    await http.delete(`/tasks/${taskId}/full-cleanup`);
  } catch (e) {
    // Best-effort: il cleanup è una pulizia "soft", non blocca la UX
    console.warn('[cleanupTaskCompletely] errore cleanup:', e?.message || e);
  }
};
