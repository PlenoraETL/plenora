/**
 * enrichment.js — API per il modulo Arricchisci
 *
 * Preparazione (stima con gli input) e avvio dell'arricchimento dati tramite API esterne.
 * Tutte le operazioni intensive girano sul worker (async) — per i risultati usare
 * getTaskStatus da api/tasks.js.
 */

import http from './http';

/**
 * Preparazione prima di avviare (Blocco A, regola 5): POST
 * /projects/{id}/preflight con gli input dell'esecuzione sul canvas SALVATO
 * (nessun `canvas` nel corpo). Per ogni nodo API: righe in ingresso
 * (esatte, stimate o non conoscibili, campionate), chiamate stimate, motivo.
 * `preparation_fingerprint` va passata all'avvio (executeEnrichmentAsync).
 * `test_run`: stima dell'«Esegui di prova» (POST /test-run/preflight, stessa
 * preparazione e impronta dell'avvio di prova; la risposta porta
 * `test_run.username`, l'utente di prova che il server userà).
 * @returns {Promise<{valid, manifest, preparation_fingerprint, nodes: Array, checks, test_run?}>}
 */
export const preflightEnrichment = async (projectId, { inputs = {}, force_recalculate = false, test_run = false } = {}) => {
  const url = test_run ? `/projects/${projectId}/test-run/preflight` : `/projects/${projectId}/preflight`;
  const response = await http.post(url, { inputs: inputs || {}, force_recalculate: Boolean(force_recalculate) });
  return response.data;
};

/**
 * Avvia l'arricchimento in modo asincrono (task worker).
 * Ritorna immediatamente con un task_id da usare per il polling.
 *
 * @param {string}  projectId              - ID del progetto
 * @param {Object}  options
 * @param {boolean} options.force_recalculate - Ignora la cache (default false)
 * @param {Object} [options.inputs]           - Valori runtime per input_key
 * @param {string}  options.format            - Formato output (default 'csv')
 * @param {string} [options.preparation_fingerprint] - impronta del preflight:
 *   il server rifiuta con 409 `preparation_changed` se la preparazione e'
 *   cambiata dalla stima confermata.
 * @param {string}  [options.intermediate_mode] - R-25 PR-B5: override per-call
 *   della INTERMEDIATE_PERSISTENCE_MODE globale (vedi executeProjectQuery).
 *   Opt-in superadmin lato BE (403 a non-super, 422 su valore invalido).
 *   Se omesso/falsy NON viene serializzato come query param.
 * @returns {Promise<Object>} { success, async: true, task_id, status, message }
 */
export const executeEnrichmentAsync = async (projectId, options = {}) => {
  const { force_recalculate = false, format = 'csv', intermediate_mode = null, inputs = {}, preparation_fingerprint = null } = options;
  const url = `/projects/${projectId}/enrich-async`;
  const body = { force_recalculate, format };
  if (inputs && Object.keys(inputs).length > 0) {
    body.inputs = inputs;
  }
  if (preparation_fingerprint) body.preparation_fingerprint = preparation_fingerprint;
  // Branch evita di passare un 3o arg undefined (preserva i test esistenti).
  const response = intermediate_mode
    ? await http.post(url, body, { params: { intermediate_mode } })
    : await http.post(url, body);
  return response.data;
};

