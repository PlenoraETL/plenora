/**
 * canvasValidationContract.js — Contratto FE per la validazione canvas.
 *
 * Single-source-of-truth tipologico (typed-ish via JSDoc) per il risultato
 * delle 3 funzioni `validate*Canvas` e per il hook `useCanvasValidation`.
 *
 * Esporta:
 *   - typedef `Severity`, `ValidationIssue`, `CanvasValidationResult` (JSDoc)
 *   - enum stabili `SEVERITY` e `RULE_IDS`
 *   - helper `isCanvasEmpty(nodes)`
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
 * Boundary FE/BE futuro:
 *   - `RULE_IDS` sono stringhe stabili enum-like; il backend potra' emettere
 *     issue con gli stessi `id` per l'handshake FE/BE in Phase 3.
 *   - `ValidationIssue.source` distingue origine ('frontend' | 'backend').
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
 * Rule id stabili. Stringhe SCREAMING_SNAKE. NON cambiare i valori senza una
 * coordinata FE/BE: i test e l'eventuale BE handshake leggono questi id.
 */
export const RULE_IDS = Object.freeze({
  // Strutturali (S)
  CANVAS_EMPTY:           'CANVAS_EMPTY',
  RESULT_NODE_MISSING:    'RESULT_NODE_MISSING',
  RESULT_NODE_DUPLICATED: 'RESULT_NODE_DUPLICATED',
  CARICA_DUPLICATED:      'CARICA_DUPLICATED',
  CYCLE_DETECTED:         'CYCLE_DETECTED',
  SOURCE_ISOLATED:        'SOURCE_ISOLATED',
  TRANSFORM_ORPHAN:       'TRANSFORM_ORPHAN',

  // Result (R)
  RESULT_NOT_CONNECTED:   'RESULT_NOT_CONNECTED',
  RESULT_NO_SOURCE:       'RESULT_NO_SOURCE',
  RESULT_GEO_NO_GEOMETRY: 'RESULT_GEO_NO_GEOMETRY',

  // Edge / join (J)
  JOIN_NOT_CONFIGURED:    'JOIN_NOT_CONFIGURED',
  JOIN_SPATIAL_NO_GEOM:   'JOIN_SPATIAL_NO_GEOM',
  JOIN_COLUMN_NOT_FOUND:  'JOIN_COLUMN_NOT_FOUND',

  // Source / projection (P)
  SOURCE_NO_VISIBLE_COLUMNS: 'SOURCE_NO_VISIBLE_COLUMNS',
  COLUMN_NAME_CONFLICT:      'COLUMN_NAME_CONFLICT',

  // Carica DB (C)
  CARICA_NOT_CONNECTED:                   'CARICA_NOT_CONNECTED',
  CARICA_DB_CONNECTION_MISSING:           'CARICA_DB_CONNECTION_MISSING',
  CARICA_DB_SCHEMA_MISSING:               'CARICA_DB_SCHEMA_MISSING',
  CARICA_DB_TABLE_MISSING:                'CARICA_DB_TABLE_MISSING',
  CARICA_DB_MATCH_COLUMNS_MISSING:        'CARICA_DB_MATCH_COLUMNS_MISSING',
  CARICA_ARCGIS_NOT_CONNECTED:            'CARICA_ARCGIS_NOT_CONNECTED',
  CARICA_ARCGIS_CONNECTION_MISSING:       'CARICA_ARCGIS_CONNECTION_MISSING',
  CARICA_ARCGIS_LAYER_URL_MISSING:        'CARICA_ARCGIS_LAYER_URL_MISSING',
  CARICA_ARCGIS_NEW_SERVICE_NAME_MISSING: 'CARICA_ARCGIS_NEW_SERVICE_NAME_MISSING',
  CARICA_ARCGIS_MATCH_COLUMN_MISSING:     'CARICA_ARCGIS_MATCH_COLUMN_MISSING',

  // Transform (T) — Manipola
  TRANSFORM_OP_MISSING:            'TRANSFORM_OP_MISSING',
  TRANSFORM_PARAM_REQUIRED:        'TRANSFORM_PARAM_REQUIRED',
  TRANSFORM_COLUMN_NOT_FOUND:      'TRANSFORM_COLUMN_NOT_FOUND',
  TRANSFORM_GEOMETRY_TYPE_INVALID: 'TRANSFORM_GEOMETRY_TYPE_INVALID',

  // Arricchisci (A)
  ARRICCHISCI_NO_SOURCES:            'ARRICCHISCI_NO_SOURCES',
  ARRICCHISCI_API_NOT_SELECTED:      'ARRICCHISCI_API_NOT_SELECTED',
  ARRICCHISCI_API_CONNECTION_STALE:  'ARRICCHISCI_API_CONNECTION_STALE',
  ARRICCHISCI_PARAM_MAPPING_MISSING: 'ARRICCHISCI_PARAM_MAPPING_MISSING',
  ARRICCHISCI_NODE_ISOLATED:         'ARRICCHISCI_NODE_ISOLATED',
});

/**
 * Vero se il canvas e' vuoto (nodes null/undefined o lunghezza zero).
 *
 * @param {Array|null|undefined} nodes
 * @returns {boolean}
 */
export function isCanvasEmpty(nodes) {
  return !nodes || nodes.length === 0;
}

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
