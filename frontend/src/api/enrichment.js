/**
 * enrichment.js — API per il modulo Arricchisci
 *
 * Gestisce validazione, stima e avvio dell'arricchimento dati tramite API esterne.
 * Tutte le operazioni intensive usano Celery (async) — per i risultati usare
 * getTaskStatus da api/tasks.js.
 */

import http from './http';

/**
 * Valida la configurazione di un arricchimento prima dell'esecuzione.
 * Verifica che le colonne mappate esistano e che i dati siano compatibili.
 *
 * @param {string}  projectId      - ID del progetto
 * @param {string}  apiId          - ID della connessione API esterna
 * @param {Object}  paramMapping   - Mapping { api_param: df_column }
 * @param {string}  fileSourceId   - ID file source (opzionale, se sorgente è file)
 * @param {Array}   staticColumns  - Colonne da nodi StaticValues (opzionale)
 * @returns {Promise<Object>} { valid, total_rows, valid_rows, invalid_rows,
 *                              missing_columns, warnings }
 */
export const validateEnrichment = async (
  projectId,
  apiId,
  paramMapping,
  fileSourceId  = null,
  staticColumns = null
) => {
  const response = await http.post('/enrichment/validate', {
    project_id:    projectId,
    api_id:        apiId,
    param_mapping: paramMapping,
    file_source_id: fileSourceId,
    static_columns: staticColumns,
  });
  return response.data;
};

/**
 * Stima il tempo di esecuzione e il numero di chiamate API necessarie.
 *
 * @param {string}  projectId    - ID del progetto
 * @param {string}  apiId        - ID della connessione API esterna
 * @param {Object}  paramMapping - Mapping parametri
 * @param {string}  fileSourceId - ID file source (opzionale)
 * @returns {Promise<Object>} { total_rows, unique_combinations, estimated_api_calls,
 *                              estimated_time_seconds, estimated_time_formatted,
 *                              deduplication_savings_percent }
 */
export const estimateEnrichment = async (projectId, apiId, paramMapping, fileSourceId = null) => {
  const response = await http.post('/enrichment/estimate', {
    project_id:     projectId,
    api_id:         apiId,
    param_mapping:  paramMapping,
    file_source_id: fileSourceId,
  });
  return response.data;
};

/**
 * Avvia l'arricchimento in modo asincrono (Celery).
 * Ritorna immediatamente con un task_id da usare per il polling.
 *
 * @param {string}  projectId              - ID del progetto
 * @param {Object}  options
 * @param {boolean} options.force_recalculate - Ignora la cache (default false)
 * @param {string}  options.format            - Formato output (default 'csv')
 * @param {string}  [options.intermediate_mode] - R-25 PR-B5: override per-call
 *   della INTERMEDIATE_PERSISTENCE_MODE globale (vedi executeProjectQuery).
 *   Opt-in superadmin lato BE (403 a non-super, 422 su valore invalido).
 *   Se omesso/falsy NON viene serializzato come query param.
 * @returns {Promise<Object>} { success, async: true, task_id, status, message }
 */
export const executeEnrichmentAsync = async (projectId, options = {}) => {
  const { force_recalculate = false, format = 'csv', intermediate_mode = null } = options;
  const url = `/projects/${projectId}/enrich-async`;
  const body = { force_recalculate, format };
  // Branch evita di passare un 3o arg undefined (preserva i test esistenti).
  const response = intermediate_mode
    ? await http.post(url, body, { params: { intermediate_mode } })
    : await http.post(url, body);
  return response.data;
};

