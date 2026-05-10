/**
 * projects.js — API per la gestione dei progetti URBY-ETL
 *
 * Copre: CRUD progetti, salvataggio canvas, esecuzione, preview, download.
 */

import http from './http';
import { API_ENDPOINT } from './config';
import { downloadBlob } from '../utils/downloadBlob';

/**
 * Crea un nuovo progetto.
 *
 * @param {string}  name         - Nome del progetto
 * @param {string}  description  - Descrizione opzionale
 * @param {string}  type         - Tipo: 'estrai' | 'manipola' | 'arricchisci'
 * @param {string}  connectionId - ID connessione DB di default (opzionale)
 * @returns {Promise<Object>} Progetto creato
 */
export const createProject = async (name, description = '', type = 'estrai', connectionId = null) => {
  const response = await http.post('/projects', {
    name,
    description,
    project_type:           type,
    default_connection_id:  connectionId,
  });

  if (type === 'estrai' && connectionId) {
    const projectId = response.data.metadata.id;
    await http.put(`/projects/${projectId}/canvas`, { nodes: [], edges: [] });
  }

  return response.data;
};

/**
 * Restituisce tutti i progetti dell'utente.
 * @returns {Promise<Object>} { projects: [...] }
 */
export const listProjects = async () => {
  const response = await http.get('/projects');
  return response.data;
};

/**
 * Restituisce un progetto con il suo canvas.
 * @param {string} projectId
 * @returns {Promise<Object>} Progetto completo
 */
export const getProject = async (projectId) => {
  const response = await http.get(`/projects/${projectId}`);
  return response.data;
};

/**
 * Salva il canvas di un progetto (nodi + edges).
 * @param {string} projectId
 * @param {Object} canvas     - { nodes, edges }
 * @returns {Promise<Object>}
 */
export const updateCanvas = async (projectId, canvas) => {
  const response = await http.put(`/projects/${projectId}/canvas`, canvas);
  return response.data;
};

/**
 * Elimina un progetto.
 * @param {string} projectId
 * @returns {Promise<Object>}
 */
export const deleteProject = async (projectId) => {
  const response = await http.delete(`/projects/${projectId}`);
  return response.data;
};

/**
 * Esegue la query/pipeline di un progetto in modo asincrono (Celery).
 *
 * Accetta sia un connectionId (string, per Estrai SQL) che un oggetto options
 * (per Manipola/Arricchisci).
 *
 * @param {string}         projectId
 * @param {string|Object}  optionsOrConnectionId
 * @param {string}         [optionsOrConnectionId.intermediate_mode] - R-25 PR-B5:
 *   override per-call della INTERMEDIATE_PERSISTENCE_MODE globale
 *   ('metadata_only'|'preview_only'|'full_pg'|'full_artifact').
 *   Backend: opt-in superadmin (BE rifiuta 403 a non-super; 422 su valore
 *   invalido). Se omesso/falsy, NON viene serializzato come query param
 *   per preservare la compat con caller esistenti.
 * @returns {Promise<Object>} { task_id, status, ... }
 */
export const executeProjectQuery = async (projectId, optionsOrConnectionId = null) => {
  let payload = {};
  let intermediate_mode = null;

  if (optionsOrConnectionId && typeof optionsOrConnectionId === 'object') {
    const { force_recalculate = false, intermediate_mode: im = null } = optionsOrConnectionId;
    payload = { force_recalculate };
    intermediate_mode = im;
  } else if (optionsOrConnectionId && typeof optionsOrConnectionId === 'string') {
    payload = { connection_id: optionsOrConnectionId };
  }

  // Query param solo se valorizzato. Branch evita di passare un 3o arg
  // undefined a http.post (preserva contract-arity dei test esistenti).
  const url = `/projects/${projectId}/execute`;
  const response = intermediate_mode
    ? await http.post(url, payload, { params: { intermediate_mode } })
    : await http.post(url, payload);
  return response.data;
};

/**
 * Ottiene una preview dei dati del progetto (asincrona con Celery).
 * @param {string} projectId
 * @param {number} limit      - Numero massimo di righe (default 100)
 * @returns {Promise<Object>} { task_id, ... }
 */
export const previewProject = async (projectId, limit = 100) => {
  const response = await http.get(`/projects/${projectId}/preview`, {
    params: { limit },
  });
  return response.data;
};

/**
 * Restituisce l'URL di download dell'output di un progetto.
 * @param {string} projectId
 * @returns {string} URL completo
 */
export const getDownloadUrl = (projectId) =>
  `${API_ENDPOINT}/projects/${projectId}/download`;

/**
 * Scarica l'output di un progetto nel browser.
 * @param {string} projectId
 * @param {string} filename  - Nome file opzionale
 */
export const downloadProjectOutput = async (projectId, filename = null) => {
  const response = await http.get(`/projects/${projectId}/download`, {
    responseType: 'blob',
  });

  downloadBlob(response, filename, `output_${projectId}`);
  return { success: true };
};

/**
 * Scarica un file specifico associato a un progetto come Blob.
 *
 * Backend: GET /projects/:projectId/download?file=:filename. La query
 * string viene preservata letterale (senza encoding axios `params`) per
 * mantenere la URL semantics dei consumer esistenti.
 *
 * Ritorna l'AxiosResponse grezzo (non solo .data) cosi' il caller puo'
 * accedere a headers (es. Content-Disposition) se serve, senza
 * ulteriori giri.
 *
 * @param {string} projectId
 * @param {string} filename - basename del file lato server
 * @returns {Promise<import('axios').AxiosResponse<Blob>>}
 */
export const downloadProjectFile = async (projectId, filename) => {
  const response = await http.get(
    `/projects/${projectId}/download?file=${filename}`,
    { responseType: 'blob' },
  );
  return response;
};

/**
 * Restituisce gli schemi inferiti per tutti i nodi di un progetto.
 * @param {string} projectId
 * @returns {Promise<Object>} { [nodeId]: ColumnDef[] }
 */
export const getProjectSchemas = async (projectId) => {
  const response = await http.get(`/projects/${projectId}/schemas`);
  return response.data;
};

/**
 * Restituisce lo schema inferito per un singolo nodo.
 * @param {string} projectId
 * @param {string} nodeId
 * @returns {Promise<ColumnDef[]>}
 */
export const getNodeSchema = async (projectId, nodeId) => {
  const response = await http.get(`/projects/${projectId}/schemas/${nodeId}`);
  return response.data;
};

/**
 * Valida la connessione al database del progetto.
 * @param {string} projectId
 * @param {Object} payload
 * @returns {Promise<{valid: boolean, error?: string}>}
 */
export const validateProjectConnection = async (projectId, payload) => {
  const response = await http.post(`/projects/${projectId}/validate-connection`, payload);
  return response.data;
};

/**
 * Valida tutte le sorgenti dati di un progetto in un'unica chiamata.
 * @param {string} projectId
 * @returns {Promise<Object>}
 */
export const validateAllProjectSources = async (projectId) => {
  const response = await http.get(`/projects/${projectId}/validate-all`);
  return response.data;
};
