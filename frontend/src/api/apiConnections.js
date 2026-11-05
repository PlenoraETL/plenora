/**
 * apiConnections.js — API per la gestione dei connettori esterni
 *
 * Connettori esterni = API REST, eseguibili Python/Shell, Data Sources (SharePoint, FTP, S3...).
 * Usati dal modulo Arricchisci per arricchire i dataset con dati esterni.
 */

import http from './http';
import { fetchAllOffsetPages } from './pagination';

/**
 * Restituisce tutti i connettori esterni configurati.
 *
 * @param {Object} filters
 * @param {string} filters.category        - Filtra per categoria (geocoding, routing, ml_model, ...)
 * @param {string} filters.connection_type - Filtra per tipo (manual, template, openapi, rest_file, datasource)
 * @returns {Promise<{api_connections: Array<Object>}>} shape backend: `arricchisci/router.py:458`
 *   ritorna `{"api_connections": all_conns}`. Niente alias `connections`
 *   lato BE.
 */
export const listApiConnections = async (filters = {}) => {
  const params = {};
  if (filters.category) params.category = filters.category;
  if (filters.connection_type) params.connection_type = filters.connection_type;
  return fetchAllOffsetPages('/api-connections', {
    params,
    extractItems: data => data.api_connections,
    mergeItems: (items, lastPage) => ({ ...lastPage, api_connections: items }),
  });
};

/**
 * Restituisce il dettaglio di una singola connessione API.
 * @param {string} apiId
 * @returns {Promise<Object>}
 */
export const getApiConnection = async (apiId) => {
  const response = await http.get(`/api-connections/${apiId}`);
  return response.data;
};

/**
 * Crea una nuova connessione API.
 * @param {Object} payload — body validato dal backend
 * @returns {Promise<Object>}
 */
export const createApiConnection = async (payload) => {
  const response = await http.post('/api-connections', payload);
  return response.data;
};

/**
 * Aggiorna una connessione esistente.
 * @param {string} apiId
 * @param {Object} payload
 * @returns {Promise<Object>}
 */
export const updateApiConnection = async (apiId, payload) => {
  const response = await http.put(`/api-connections/${apiId}`, payload);
  return response.data;
};

/**
 * Elimina una connessione.
 * @param {string} apiId
 * @returns {Promise<Object>}
 */
export const deleteApiConnection = async (apiId) => {
  const response = await http.delete(`/api-connections/${apiId}`);
  return response.data;
};

/**
 * Test di una configurazione API NON ancora salvata (draft).
 * Mirror del backend `test_api_config` — POST /api-connections/test.
 * Da non confondere con `testApiConnection` che richiede l'id di una
 * connessione gia' persistita.
 *
 * @param {Object} payload — config draft completa
 * @returns {Promise<Object>}
 */
export const testApiConfig = async (payload) => {
  const response = await http.post('/api-connections/test', payload);
  return response.data;
};

/**
 * Test di una connessione gia' salvata (by-id).
 * Mirror del backend `test_api_connection` — POST /api-connections/:id/test.
 *
 * @param {string} apiId
 * @param {Object} payload — tipicamente `{ test_params: {...} }`
 * @returns {Promise<Object>}
 */
export const testApiConnection = async (apiId, payload) => {
  const response = await http.post(`/api-connections/${apiId}/test`, payload);
  return response.data;
};

/**
 * Scarica uno schema OpenAPI/Swagger da un URL pubblico (non lo interpreta:
 * lo normalizza utils/openapiImport).
 * Backend: POST /api-connections/fetch-openapi-schema.
 *
 * @param {string} url — URL di uno schema OpenAPI raggiungibile
 * @returns {Promise<Object|string>} il documento JSON, o il testo YAML com'e'
 */
export const fetchOpenApiSchema = async (url) => {
  const response = await http.post('/api-connections/fetch-openapi-schema', { url });
  return response.data;
};
