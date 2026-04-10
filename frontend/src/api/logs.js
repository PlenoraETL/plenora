/**
 * api/logs.js — API per la visualizzazione e gestione dei log di sistema
 *
 * Espone gli endpoint del backend per:
 *   - Recupero log con filtri (livello, data, testo, progetto)
 *   - Statistiche aggregate sui log
 *   - Pulizia log (admin)
 *
 * Usato da: pages/LogViewer.jsx
 */

import http from './http';

/**
 * Recupera i log di sistema con supporto filtri e paginazione.
 * @param {Object} params
 * @param {string}   [params.level]       - Livello log: 'DEBUG'|'INFO'|'WARNING'|'ERROR'|'CRITICAL'
 * @param {string}   [params.start_date]  - Data inizio (ISO)
 * @param {string}   [params.end_date]    - Data fine (ISO)
 * @param {string}   [params.search]      - Testo libero nel messaggio
 * @param {string}   [params.project_id]  - Filtra per progetto
 * @param {number}   [params.limit]       - Numero massimo di risultati
 * @param {number}   [params.offset]      - Offset per paginazione
 * @returns {Promise<{logs: Array, total: number}>}
 */
export const getLogs = async (params = {}) => {
  const response = await http.get('/logs', { params });
  return response.data;
};

/**
 * Restituisce statistiche aggregate sui log (conteggi per livello, per giorno).
 * @returns {Promise<Object>}
 */
export const getLogsStats = async () => {
  const response = await http.get('/logs/stats');
  return response.data;
};

/**
 * Elimina tutti i log dal database (operazione irreversibile, solo admin).
 * @returns {Promise<{deleted: number}>}
 */
export const clearLogs = async () => {
  const response = await http.post('/logs/clear');
  return response.data;
};
