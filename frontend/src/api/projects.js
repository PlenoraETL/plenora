/**
 * projects.js — API per la gestione dei progetti URBY-ETL
 *
 * Copre: CRUD progetti, salvataggio canvas, esecuzione, preview, download.
 */

import http from './http';
import { fetchAllOffsetPages } from './pagination';
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
  // Pre-fix (P0 contracts-flows): la sequenza POST + PUT canvas era
  // dual-call non idempotente. Se POST riusciva (progetto in DB) ma
  // PUT canvas falliva per timeout/network, la funzione sollevava
  // un'eccezione — l'utente pensava che la creazione fosse fallita
  // e riprovava, generando un duplicato nel DB (POST /projects non
  // ha guard di idempotency).
  //
  // Post-fix: la PUT canvas e' best-effort, errore propagato come
  // flag `canvasInitFailed` nella response invece di sollevare. Il
  // caller vede che il progetto esiste e puo' guidare l'utente
  // all'editor per completare manualmente. NB: il canvas vuoto e'
  // l'inizializzazione di default — il BE crea gia' un canvas vuoto
  // su POST /projects, quindi la PUT e' veramente best-effort di
  // assicurazione, non un dato critico.
  const response = await http.post('/projects', {
    name,
    description,
    project_type:           type,
    default_connection_id:  connectionId,
  });

  if (type === 'estrai' && connectionId) {
    const projectId = response.data.metadata.id;
    try {
      await http.put(`/projects/${projectId}/canvas`, { nodes: [], edges: [] });
    } catch (canvasErr) {
      console.warn(
        '[createProject] canvas init best-effort fallito per projectId=' +
        projectId + ': ' + (canvasErr?.message || canvasErr) +
        ' — il progetto E\' stato creato; il canvas verra' +
        ' inizializzato al primo accesso editor.'
      );
      response.data.canvasInitFailed = true;
      response.data.canvasInitError = canvasErr?.message || String(canvasErr);
    }
  }

  return response.data;
};

/**
 * Clona un progetto esistente (canvas + impostazioni) in uno nuovo (#8).
 * @param {string} projectId - id del progetto sorgente
 * @param {string|null} newName - nome del clone (default BE "Copia di …")
 * @returns {Promise<{metadata: Object, canvas: Object|null}>}
 */
export const cloneProject = async (projectId, newName = null) => {
  const response = await http.post(`/projects/${projectId}/clone`, {
    name: newName || null,
  });
  return response.data;
};

/**
 * Aggiorna i metadati di un progetto (rename / descrizione) — #8 rename.
 * @param {string} projectId
 * @param {{name?: string, description?: string}} fields
 * @returns {Promise<{metadata: Object}>}
 */
export const updateProject = async (projectId, fields) => {
  const response = await http.patch(`/projects/${projectId}`, fields);
  return response.data;
};

/* ── Versioning canvas (#12) ─────────────────────────────────────────────── */

/** Salva uno snapshot del canvas corrente. @returns {Promise<{id, label}>} */
export const createCanvasVersion = async (projectId, label = null) => {
  const response = await http.post(`/projects/${projectId}/versions`, { label: label || null });
  return response.data;
};

/** Elenco versioni salvate. @returns {Promise<{versions: Array}>} */
export const listCanvasVersions = async (projectId) => {
  const response = await http.get(`/projects/${projectId}/versions`);
  return response.data;
};

/** Ripristina il canvas a una versione. @returns {Promise<{canvas}>} */
export const restoreCanvasVersion = async (projectId, versionId) => {
  const response = await http.post(`/projects/${projectId}/versions/${versionId}/restore`);
  return response.data;
};

/**
 * Restituisce tutti i progetti dell'utente.
 * @returns {Promise<Object>} { projects: [...] }
 */
export const listProjects = async () => {
  return fetchAllOffsetPages('/projects', {
    extractItems: data => data.projects,
    mergeItems: (items, lastPage) => ({ ...lastPage, projects: items }),
  });
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
 * Valida l'intero canvas del progetto (Epic Canvas Validation §12).
 * Orizzontale: il backend valida la catena cross-progetto (strutturale +
 * per-nodo + DB-dependent) sul canvas SALVATO. Persiste `canvas_validated`.
 * @param {string} projectId
 * @returns {Promise<{valid: boolean, has_errors: boolean, issues: Array}>}
 */
export const validateProjectCanvas = async (projectId) => {
  const response = await http.post(`/projects/${projectId}/validate`);
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
 * Esegue la query/pipeline di un progetto in modo asincrono (task worker).
 *
 * @param {string} projectId
 * @param {Object} [options]
 * @param {boolean} [options.force_recalculate=false]
 * @param {string}  [options.intermediate_mode] - R-25 PR-B5: override
 *   per-call della INTERMEDIATE_PERSISTENCE_MODE globale ('metadata_only'
 *   | 'preview_only' | 'full_pg' | 'full_artifact'). Backend: opt-in
 *   superadmin (BE rifiuta 403 a non-super; 422 su valore invalido).
 *   Se omesso/falsy, NON viene serializzato come query param.
 * @returns {Promise<Object>} { task_id, status, async, ... }
 *
 * NOTA: il branch legacy che accettava un `connection_id` come secondo
 * argomento stringa (Estrai v1) e' stato rimosso. Il backend
 * `ExecuteRequest` non ha un campo `connection_id`: la connessione viene
 * letta dal canvas server-side. Caller storici che passavano la stringa
 * ottenevano un'esecuzione che ignorava silenziosamente il valore.
 */
export const executeProjectQuery = async (projectId, options = null) => {
  let payload = {};
  let intermediate_mode = null;

  if (options && typeof options === 'object') {
      const {
        force_recalculate = false,
        intermediate_mode: im = null,
        inputs = {},
      } = options;
      payload = { force_recalculate };
      if (inputs && Object.keys(inputs).length > 0) {
        payload.inputs = inputs;
      }
      intermediate_mode = im;
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
 * Ottiene una preview dei dati del progetto (asincrona, task worker).
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
