/**
 * projects.js — API per la gestione dei progetti URBY-ETL
 *
 * Copre: CRUD progetti, salvataggio canvas, esecuzione, preview, download.
 */

import http from './http';
import { fetchAllOffsetPages } from './pagination';
import { API_ENDPOINT } from './config';
import { downloadBlob } from '../utils/downloadBlob';
import { downloadProgress } from '../utils/transferProgress';
import { requestErrorMessage } from '../utils/errorMessages';
import { rememberProjects } from '../utils/knownProjects';

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
      response.data.canvasInitError = requestErrorMessage(canvasErr);
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

/**
 * Ripristina il canvas a una versione. `baseCanvasVersion` (token
 * `canvas_version` della versione da cui parte la scheda) rende il
 * ripristino condizionato: se il server e' andato oltre risponde 409
 * `canvas_version_conflict` (Blocco A, regola 8). Senza base: ripristino
 * senza controllo. L'unico chiamante e' lo store (scrittura condizionata).
 * @returns {Promise<{canvas, canvas_version, updated_at}>}
 */
export const restoreCanvasVersion = async (projectId, versionId, baseCanvasVersion = null) => {
  const url = `/projects/${projectId}/versions/${versionId}/restore`;
  const response = baseCanvasVersion
    ? await http.post(url, { base_canvas_version: baseCanvasVersion })
    : await http.post(url);
  return response.data;
};

/**
 * Restituisce tutti i progetti dell'utente.
 * @returns {Promise<Object>} { projects: [...] }
 */
export const listProjects = async () => {
  const data = await fetchAllOffsetPages('/projects', {
    extractItems: data => data.projects,
    mergeItems: (items, lastPage) => ({ ...lastPage, projects: items }),
  });
  // La palette Ctrl+K cerca tra i progetti gia' scaricati (UI-4, U23).
  rememberProjects(data?.projects);
  return data;
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
 * Salva il canvas di un progetto (nodi + edges). Con `base_canvas_version`
 * nel corpo il salvataggio e' condizionato alla versione (409
 * `canvas_version_conflict` se superata). L'unico chiamante e' lo store.
 * @param {string} projectId
 * @param {Object} canvas     - { nodes, edges, base_canvas_version? }
 * @returns {Promise<{status, project_id, canvas_version, updated_at}>}
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
 * «Esegui di prova» di un progetto con «Utente che esegue» (regola H del
 * backend, shared/execution/runtime_identity). Il server usa il canvas
 * SALVATO e il suo nome utente di prova: nessun canvas né utente nel corpo.
 * Il risultato resta nell'attività (si scarica una volta da chi l'ha
 * lanciata), non diventa l'output del progetto e non avvia Carica.
 * Arricchisci ha la sua prova (stessa stima: preflightEnrichment con
 * `test_run`).
 * @param {string} projectId
 * @param {{mode: string, inputs?: Object, force_recalculate?: boolean,
 *          preparation_fingerprint?: string|null, format?: string}} options
 * @returns {Promise<Object>} { task_id, async, test_run: { username } }
 */
export const executeTestRun = async (projectId, options = {}) => {
  const { mode, inputs = {}, preparation_fingerprint = null, format = null } = options;
  // La prova non ricalcola mai i progetti a monte (il server rifiuta `true`).
  const body = { inputs: inputs || {}, force_recalculate: false };
  if (mode === 'arricchisci') {
    if (preparation_fingerprint) body.preparation_fingerprint = preparation_fingerprint;
    if (format) body.format = format;
    const response = await http.post(`/projects/${projectId}/test-run/enrich`, body);
    return response.data;
  }
  const response = await http.post(`/projects/${projectId}/test-run`, body);
  return response.data;
};

/**
 * Ottiene una preview dei dati del progetto (asincrona, task worker).
 * @param {string} projectId
 * @param {number} limit      - Numero massimo di righe (default 100)
 * @returns {Promise<Object>} { task_id, ... }
 */
/**
 * Anteprima del progetto. Con `nodeId` (B1/B6) il backend esegue solo il
 * nodo e i suoi antenati e restituisce i dati DOPO quel passo.
 */
export const previewProject = async (projectId, limit = 100, nodeId = null) => {
  const params = { limit };
  if (nodeId) params.node_id = nodeId;
  const response = await http.get(`/projects/${projectId}/preview`, { params });
  return response.data;
};


/**
 * Scarica l'output di un progetto nel browser.
 * @param {string} projectId
 * @param {string} filename  - Nome file opzionale
 */
export const downloadProjectOutput = async (projectId, filename = null, { onProgress = null } = {}) => {
  const response = await http.get(`/projects/${projectId}/download`, {
    responseType: 'blob',
    ...downloadProgress(onProgress),
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
export const downloadProjectFile = async (projectId, filename, { onProgress = null } = {}) => {
  const response = await http.get(
    `/projects/${projectId}/download?file=${filename}`,
    { responseType: 'blob', ...downloadProgress(onProgress) },
  );
  return response;
};

/**
 * Schemi (colonne con id e stato per nodo) del canvas dell'editor, anche non
 * salvato, senza salvarlo. L'unico chiamante e' la fonte unica delle colonne
 * (store/useColumnStore, Blocco A regola 4).
 */
export const inferProjectSchemas = async (projectId, canvas, { signal } = {}) => {
  // `signal`: una richiesta superata da una firma nuova si annulla (UI-2, C12).
  const response = await http.post(`/projects/${projectId}/schemas`, { canvas }, { signal });
  return response.data;
};

/**
 * Query di Estrai che l'esecuzione lancerebbe sul canvas dell'editor (anche
 * non salvato), dallo stesso piano del backend (estrai/query_plan; Fase 5
 * T6, R3.13). Non salva niente.
 * @returns {Promise<{kind: 'sql'|'feature_service'|'none', sql, params:
 *   Array<{name, value, input_key, input_label}>, tables_used: string[],
 *   feature_service, error, notes: string[]}>}
 */
export const previewProjectQuery = async (projectId, canvas, { signal } = {}) => {
  const response = await http.post(`/projects/${projectId}/query-preview`, { canvas }, { signal });
  return response.data;
};

/**
 * Valori distinti di una colonna nell'INGRESSO di un nodo (Lookup, «Riempi
 * con i valori presenti»), letti dal server sul canvas dell'editor anche non
 * salvato, con la stessa preparazione dell'esecuzione. Testo dei valori nella
 * forma UNICA che usa il Lookup in esecuzione (backend
 * shared/execution/lookup_mapping.lookup_value_text: 5.0 -> "5", date
 * "2024-01-05"); il null resta distinto (`is_null: true`, `value: null`).
 * @returns {Promise<{values: Array<{value, is_null, count}>, column, column_id,
 *   truncated, sampled, sampled_rows, reason}>}
 */
export const distinctNodeValues = async (projectId, nodeId, { canvas, column = null, columnId = null, limit = 200 }) => {
  const response = await http.post(`/projects/${projectId}/nodes/${nodeId}/distinct`, {
    canvas, column, column_id: columnId, limit,
  });
  return response.data;
};
