/**
 * eseguibili.js — API per il modulo Eseguibili (catene on-demand)
 *
 * Gli eseguibili sono "template" di catene arricchisci che possono essere
 * eseguiti caricando un file al volo. Il canvas è clonato dal progetto
 * originale al momento della creazione ed è indipendente.
 */

import http from './http';

/**
 * Lista gli eseguibili visibili al gruppo dell'utente.
 * @returns {Promise<Array>} [{id, name, description, default_output_format, expected_columns_count, ...}]
 */
export const listEseguibili = async () => {
  const r = await http.get('/eseguibili');
  return r.data;
};

/**
 * Ritorna i dettagli completi di un eseguibile (con expected_columns).
 * @param {string} id
 */
export const getEseguibile = async (id) => {
  const r = await http.get(`/eseguibili/${id}`);
  return r.data;
};

/**
 * Crea un nuovo eseguibile clonando un progetto arricchisci esistente.
 * Solo admin del gruppo.
 * @param {Object} payload
 * @param {string} payload.source_project_id
 * @param {string} payload.name
 * @param {string} [payload.description]
 * @param {string} [payload.default_output_format]
 * @param {string} [payload.default_output_name]
 */
export const createEseguibile = async (payload) => {
  const r = await http.post('/eseguibili', payload);
  return r.data;
};

/**
 * Elimina un eseguibile. Solo admin.
 */
export const deleteEseguibile = async (id) => {
  const r = await http.delete(`/eseguibili/${id}`);
  return r.data;
};

/**
 * Upload di un file per un eseguibile. Ritorna colonne rilevate + auto-mapping.
 * Il file viene conservato in cache server-side fino al successivo execute().
 *
 * @param {string} eseguibileId
 * @param {File} file
 * @returns {Promise<{file_id, detected_columns, row_count, auto_mapping, preview_rows}>}
 */
export const analyzeUpload = async (eseguibileId, file) => {
  const fd = new FormData();
  fd.append('file', file);
  const r = await http.post(`/eseguibili/${eseguibileId}/analyze-upload`, fd, {
    headers: { 'Content-Type': 'multipart/form-data' },
  });
  return r.data;
};

/**
 * Esegue un eseguibile: applica mapping, sostituisce sorgente, lancia task Celery.
 * @param {string} eseguibileId
 * @param {Object} payload
 * @param {string} payload.file_id
 * @param {Array}  payload.column_mapping - [{chain_column, file_column}]
 * @param {string} [payload.output_format]
 * @param {string} [payload.output_name]
 * @returns {Promise<{async, task_id, project_id, message}>}
 */
export const executeEseguibile = async (eseguibileId, payload) => {
  const r = await http.post(`/eseguibili/${eseguibileId}/execute`, payload);
  return r.data;
};
