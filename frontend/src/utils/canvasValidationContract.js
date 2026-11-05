/**
 * canvasValidationContract.js — Contratto FE per la validazione canvas.
 *
 * Single-source-of-truth tipologico (typed-ish via JSDoc) per il
 * `CanvasValidationResult` consumato dalla UI (ValidationModal, badge, highlight).
 * Post-migrazione (Epic CanvasVal §12) la validazione è on-demand via backend:
 * `utils/backendValidationResult.js` mappa la risposta di POST /validate in questo
 * shape usando `createIssue`/`buildResult`. Il `code` di ogni issue è quello
 * del backend (core/projects/canvas_validation.py): nessun elenco copiato qui
 * (Fase 5 T7: `RULE_IDS` e `isCanvasEmpty` non avevano chiamanti).
 *
 * Esporta:
 *   - typedef `Severity`, `ValidationIssue`, `CanvasValidationResult` (JSDoc)
 *   - enum stabile `SEVERITY`
 *   - factory `createIssue({...})`
 *   - builder `buildResult(issues)`
 *
 * Compat:
 *   - `CanvasValidationResult.isValid` e' alias di `.ok` per call site legacy
 *     (KEEP_COMPAT). Gli array `issues` e i campi degli `ValidationIssue` sono
 *     additivi rispetto al vecchio shape `{severity, message, nodeId?, nodeName?}`,
 *     quindi i contract test esistenti (regex su message + severity + nodeId)
 *     restano verdi.
 *
 * Boundary FE/BE:
 *   - il `code` dell'issue è quello emesso dal backend, invariato.
 *   - `ValidationIssue.source` distingue origine ('frontend' | 'backend'); dopo
 *     la migrazione gli issue arrivano col `source: 'backend'`.
 */

/**
 * @typedef {'error'|'warning'|'info'} Severity
 */

/**
 * @typedef {Object} ValidationIssue
 * @property {string} id — Stable rule id (es. `'CANVAS_EMPTY'`).
 * @property {Severity} severity
 * @property {string} message — Testo user-facing.
 * @property {string} [nodeId]
 * @property {string} [nodeName]
 * @property {string} [edgeId]
 * @property {string} [field]
 * @property {'frontend'|'backend'} source
 */

/**
 * @typedef {Object} CanvasValidationResult
 * @property {boolean} ok — true se nessun issue di severity 'error'.
 * @property {boolean} isValid — alias di `ok` (KEEP_COMPAT).
 * @property {ValidationIssue[]} issues — flat list di tutti gli issue.
 * @property {{error: ValidationIssue[], warning: ValidationIssue[], info: ValidationIssue[]}} bySeverity
 */

export const SEVERITY = Object.freeze({
  ERROR: 'error',
  WARNING: 'warning',
  INFO: 'info',
});



/**
 * Factory di un `ValidationIssue` con campi opzionali normalizzati e
 * `source: 'frontend'` di default.
 *
 * @param {Object} init
 * @param {string} init.id
 * @param {Severity} init.severity
 * @param {string} init.message
 * @param {string} [init.nodeId]
 * @param {string} [init.nodeName]
 * @param {string} [init.edgeId]
 * @param {string} [init.field]
 * @param {'frontend'|'backend'} [init.source]
 * @returns {ValidationIssue}
 */
export function createIssue({ id, severity, message, nodeId, nodeName, edgeId, field, source }) {
  /** @type {ValidationIssue} */
  const issue = { id, severity, message, source: source || 'frontend' };
  if (nodeId !== undefined)   issue.nodeId   = nodeId;
  if (nodeName !== undefined) issue.nodeName = nodeName;
  if (edgeId !== undefined)   issue.edgeId   = edgeId;
  if (field !== undefined)    issue.field    = field;
  return issue;
}

/**
 * Costruisce il `CanvasValidationResult` dato un array di `issues`.
 * `ok` e' true se nessun issue ha severity 'error'. `isValid` e' alias di `ok`
 * (KEEP_COMPAT). `bySeverity` raggruppa gli issue per consumer (es. modal).
 *
 * @param {ValidationIssue[]} issues
 * @returns {CanvasValidationResult}
 */
export function buildResult(issues) {
  const bySeverity = {
    error:   issues.filter(i => i.severity === SEVERITY.ERROR),
    warning: issues.filter(i => i.severity === SEVERITY.WARNING),
    info:    issues.filter(i => i.severity === SEVERITY.INFO),
  };
  const ok = bySeverity.error.length === 0;
  return {
    ok,
    isValid: ok,
    issues,
    bySeverity,
  };
}
