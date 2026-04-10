/**
 * api/logRuns.js — API per le run di esecuzione pipeline (log.db)
 *
 * Diverso da api/runs.js che punta a intermediate.db.
 * Questi endpoint espongono la tabella `runs` di log.db.
 *
 * Usato da: pages/LogViewer.jsx (tab Esecuzioni)
 */

import http from './http';

/**
 * Lista run paginate con filtri.
 * @param {Object} params
 * @param {string}  [params.project_id]
 * @param {string}  [params.status]      - running | success | failure
 * @param {string}  [params.module]      - estrai | manipola | arricchisci | carica
 * @param {string}  [params.after]       - ISO datetime
 * @param {string}  [params.before]      - ISO datetime
 * @param {number}  [params.limit]
 * @param {number}  [params.offset]
 */
export const getLogRuns = async (params = {}) => {
  const response = await http.get('/pipeline-runs', { params });
  return response.data;
};

/**
 * Dettaglio run: metadati + steps + log tecnici correlati.
 * @param {string} runId
 * @param {boolean} [includeLogs=true]
 * @param {number}  [logLimit=200]
 */
export const getLogRun = async (runId, includeLogs = true, logLimit = 200) => {
  const response = await http.get(`/pipeline-runs/${runId}`, {
    params: { include_logs: includeLogs, log_limit: logLimit },
  });
  return response.data;
};

/**
 * Payload di debug per una run (solo run fallite).
 * @param {string} runId
 */
export const getRunPayload = async (runId) => {
  const response = await http.get(`/pipeline-runs/${runId}/payload`);
  return response.data;
};

