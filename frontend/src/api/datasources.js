/**
 * API client per il modulo Data Source.
 * Segue lo stesso pattern di features.js e client.js.
 */

import http from './http';
import { fetchAllOffsetPages } from './pagination';

export const listDataSources = async () => {
  return fetchAllOffsetPages('/datasources', {
    extractItems: data => data.datasources,
    mergeItems: (items, lastPage) => ({ ...lastPage, datasources: items }),
  });
};

export const createDataSource = async (data) => {
  const res = await http.post('/datasources', data);
  return res.data;
};

export const updateDataSource = async (id, data) => {
  const res = await http.put(`/datasources/${id}`, data);
  return res.data;
};

export const deleteDataSource = async (id) => {
  await http.delete(`/datasources/${id}`);
};

export const testDataSource = async (id) => {
  const res = await http.post(`/datasources/${id}/test`);
  return res.data;
};

export const listFiles = async (id, { path = '', pattern = null, include_hash = false } = {}) => {
  const params = { path, include_hash };
  if (pattern) params.pattern = pattern;
  const res = await http.get(`/datasources/${id}/files`, { params });
  return res.data;
};

/**
 * Restituisce lo schema (colonne + format) di un file specifico esposto
 * da un Data Source. Usato dal nodo canvas DataSource per popolare la
 * lista colonne disponibile a valle (manipola/arricchisci).
 *
 * Backend: GET /datasources/:id/files/schema?path=...
 *
 * @param {string} id   - id del data source
 * @param {string} path - path del file dentro il data source
 * @returns {Promise<{columns: Array<Object>, format?: string}>}
 */
export const getFileSchema = async (id, path, { sheet = null, layer = null } = {}) => {
  const params = { path };
  if (sheet) params.sheet = sheet;
  if (layer) params.layer = layer;
  const res = await http.get(`/datasources/${id}/files/schema`, { params });
  return res.data;
};

