/**
 * backendValidationResult.js — adatta la risposta di POST /projects/{id}/validate
 * (Epic Canvas Validation §12) al `CanvasValidationResult` consumato dal FE
 * (ValidationModal, badge, highlight).
 *
 * Il backend è la SINGLE SOURCE OF TRUTH della validazione (i validate*Canvas.js
 * per-famiglia sono stati rimossi). Issue backend: `{node_id, severity, code,
 * message, suggestion}`. Issue FE: `{id, severity, message, nodeId, source}`.
 */

import { buildResult, createIssue } from './canvasValidationContract';

/**
 * Mappa un singolo issue backend nel ValidationIssue FE.
 * @param {{node_id?: string, severity: string, code: string, message: string}} bi
 */
function backendIssueToFE(bi) {
  return createIssue({
    id: bi.code,
    severity: bi.severity,
    message: bi.message,
    nodeId: bi.node_id || undefined,
    source: 'backend',
  });
}

/**
 * Converte `{valid, has_errors, issues:[...]}` in `CanvasValidationResult`.
 * `ok`/`bySeverity` sono ricomputati da `buildResult` (coerenti con has_errors).
 * @param {{issues?: Array}} data
 * @returns {import('./canvasValidationContract').CanvasValidationResult}
 */
export function backendReportToResult(data) {
  const issues = Array.isArray(data?.issues) ? data.issues.map(backendIssueToFE) : [];
  return buildResult(issues);
}

/**
 * Risultato fallback quando la richiesta di validazione fallisce (rete/server):
 * un solo issue error, così il modal mostra il problema invece di un finto "ok".
 * @param {string} [message]
 * @returns {import('./canvasValidationContract').CanvasValidationResult}
 */
export function validationRequestFailedResult(message) {
  return buildResult([createIssue({
    id: 'VALIDATION_REQUEST_FAILED',
    severity: 'error',
    message: message || 'Validazione non riuscita: errore di comunicazione col server.',
    source: 'backend',
  })]);
}
