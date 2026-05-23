/**
 * api/logs.js — API per la visualizzazione e gestione dei log di sistema
 *
 * Espone gli endpoint del backend per:
 *   - Recupero log con filtri (livello, data, testo, progetto)
 *   - Statistiche aggregate sui log
 *
 * Usato da: pages/LogViewer.jsx
 */

import http from './http';

/**
 * Recupera i log di sistema con supporto filtri e paginazione.
 *
 * Pre-fix (P0 contracts-schemas): il JSDoc documentava `start_date` /
 * `end_date` ma il BE accetta `after` / `before`. Qualunque caller
 * basato sul JSDoc passava query param ignorati silenziosamente dal BE.
 *
 * @param {Object} params
 * @param {string}   [params.level]       - Livello log: 'DEBUG'|'INFO'|'WARNING'|'ERROR'|'CRITICAL'
 * @param {string}   [params.after]       - Data inizio (ISO 8601, es. '2026-01-01T00:00:00Z')
 * @param {string}   [params.before]      - Data fine (ISO 8601)
 * @param {string}   [params.search]      - Testo libero nel messaggio
 * @param {string}   [params.project_id]  - Filtra per progetto
 * @param {number}   [params.limit]       - Numero massimo di risultati
 * @param {number}   [params.offset]      - Offset per paginazione
 * @returns {Promise<{entries: Array, total: number, limit: number, offset: number, has_more: boolean}>}
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
