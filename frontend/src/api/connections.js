/**
 * connections.js — API per la gestione delle connessioni database
 *
 * Copre: CRUD connessioni DB, test connessione, lettura schema tabelle/viste.
 */

import http from './http';

/**
 * Crea una nuova connessione DB.
 * @param {Object} data - { name, type, host, port, database, username, password, ... }
 * @returns {Promise<Object>} Connessione creata
 */
export const createConnection = async (data) => {
  const response = await http.post('/connections', data);
  return response.data;
};

/**
 * Restituisce tutte le connessioni DB disponibili.
 * @returns {Promise<Object>} { connections: [...] }
 */
export const listConnections = async () => {
  const response = await http.get('/connections');
  return response.data;
};

/**
 * Aggiorna una connessione esistente.
 * @param {string} connectionId
 * @param {Object} data
 * @returns {Promise<Object>}
 */
export const updateConnection = async (connectionId, data) => {
  const response = await http.put(`/connections/${connectionId}`, data);
  return response.data;
};

/**
 * Elimina una connessione.
 * @param {string} connectionId
 * @returns {Promise<Object>}
 */
export const deleteConnection = async (connectionId) => {
  const response = await http.delete(`/connections/${connectionId}`);
  return response.data;
};

/**
 * Testa la raggiungibilità di una connessione.
 * @param {string} connectionId
 * @returns {Promise<Object>} { success, latency_ms, error? }
 */
export const testConnection = async (connectionId) => {
  const response = await http.post(`/connections/${connectionId}/test`);
  return response.data;
};

/**
 * Restituisce lo schema (tabelle + viste + colonne) di un database.
 * @param {string} connectionId
 * @returns {Promise<Object>} { tables: { tableName: [columns] }, views: { ... } }
 */
export const getConnectionTables = async (connectionId) => {
  const response = await http.get(`/connections/${connectionId}/tables`);
  return response.data;
};
