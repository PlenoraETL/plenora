/**
 * imdf.js — API IMDF (pubblicazione ArcGIS)
 *
 * Endpoint:
 *   POST /api/imdf/publish    → lancia task di pubblicazione async
 *   GET  /api/imdf/bundles    → lista bundle disponibili su disco
 */
import http from './http';

/**
 * Avvia la pubblicazione di un bundle IMDF su ArcGIS.
 *
 * @param {Object} params
 * @param {string} params.bundle_task_id  — task_id del precedente arricchimento
 * @param {string} params.connection_id   — ID connessione ArcGIS
 * @param {string} params.fs_name         — nome Feature Service
 * @param {string} [params.folder]        — cartella ArcGIS (root se null)
 * @param {'replace'|'append'|'upsert'} [params.mode='replace']
 * @param {boolean} [params.apply_renderers=false]
 * @param {Object<string,string>} [params.match_columns]
 * @returns {Promise<{task_id:string, status:string, message:string}>}
 */
export const publishImdf = async (params) => {
  const response = await http.post('/imdf/publish', params);
  return response.data;
};

/**
 * Lista i bundle IMDF disponibili su disco.
 * @param {Object} [opts]
 * @param {string} [opts.project_id]  filtra per progetto
 * @returns {Promise<Array>}
 */
export const listImdfBundles = async ({ project_id } = {}) => {
  const params = {};
  if (project_id) params.project_id = project_id;
  const response = await http.get('/imdf/bundles', { params });
  return response.data;
};

/**
 * Recupera i metadata completi di un bundle IMDF persistito
 * (manifest, validation, renderers, geojson, site_id, files).
 * Usato per rehydratare il canvas store dopo un refresh.
 *
 * @param {string} bundleTaskId
 * @returns {Promise<Object>}  stessa struttura di result.imdf nel task
 */
export const getImdfBundleMetadata = async (bundleTaskId) => {
  const response = await http.get(`/imdf/bundles/${bundleTaskId}`);
  return response.data;
};

/**
 * Esegue un dry-run di validazione prima della pubblicazione.
 * Ritorna un report con layer table, schema compat, impatto upsert, data issues.
 *
 * Campi: bundle_task_id, connection_id, fs_name, mode, match_columns, folder.
 * `folder` è informativo: se diverso dalla folder effettiva dell'FS
 * esistente, il report emette un warning FOLDER_MISMATCH.
 * @param {Object} params
 * @returns {Promise<Object>} report
 */
export const validateImdfPublish = async (params) => {
  const response = await http.post('/imdf/validate-publish', params);
  return response.data;
};

/**
 * Cancella un bundle IMDF dal disco del server. Richiede accesso al
 * progetto del bundle (group ACL).
 * @param {string} bundleTaskId
 */
export const deleteImdfBundle = async (bundleTaskId) => {
  const response = await http.delete(`/imdf/bundles/${bundleTaskId}`);
  return response.data;
};
