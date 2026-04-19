/**
 * api/index.js — Export centralizzato del layer API
 *
 * Importa SEMPRE da qui, non direttamente dai singoli moduli.
 * Questo garantisce che eventuali refactoring interni non rompano
 * i consumer — basta aggiornare questo file.
 *
 * Struttura moduli:
 *   http.js           → istanza axios + interceptors
 *   config.js         → costanti (URL, timeout, headers)
 *   projects.js       → CRUD progetti, canvas, esecuzione, preview, download
 *   connections.js    → CRUD connessioni DB, test, schema tabelle
 *   tasks.js          → polling task Celery, cancel, download
 *   enrichment.js     → validate, estimate, execute arricchimento
 *   apiConnections.js → CRUD connettori esterni (API, eseguibili, datasource)
 *   files.js          → upload/gestione file caricati
 *   features.js       → Feature Service ArcGIS
 *   datasources.js    → Data Sources (SharePoint, FTP, S3, ...)
 *   schedules.js      → schedulazioni task
 */

// ── Configurazione ────────────────────────────────────────────────────────────
export { API_BASE_URL, API_ENDPOINT, API_TIMEOUT, DEFAULT_HEADERS } from './config';

// ── Client HTTP (solo per casi eccezionali — preferire le funzioni specifiche) ─
export { default as http } from './http';

// ── Backward compatibility: 'api' come alias di http ─────────────────────────
export { default as api }  from './http';
export { default }         from './http';

// ── Progetti ──────────────────────────────────────────────────────────────────
export {
  createProject,
  listProjects,
  getProject,
  updateCanvas,
  deleteProject,
  executeProjectQuery,
  previewProject,
  getDownloadUrl,
  getProjectOutputInfo,
  downloadProjectOutput,
} from './projects';

// ── Connessioni DB ────────────────────────────────────────────────────────────
export {
  createConnection,
  listConnections,
  updateConnection,
  deleteConnection,
  testConnection,
  getConnectionTables,
} from './connections';

// ── Task Celery ───────────────────────────────────────────────────────────────
export {
  getTaskStatus,
  cancelTask,
} from './tasks';

// ── Arricchisci ───────────────────────────────────────────────────────────────
export {
  validateEnrichment,
  estimateEnrichment,
  executeEnrichmentAsync,
} from './enrichment';

// ── Connettori esterni ────────────────────────────────────────────────────────
export {
  listApiConnections,
} from './apiConnections';

// ── File caricati ─────────────────────────────────────────────────────────────
export {
  uploadFile,
  getFiles,
  getFile,
  deleteFile,
  getFileLayers,
  getFilePreview,
  previewFileTemp,
} from './files';

// ── Feature Service ArcGIS ────────────────────────────────────────────────────
export {
  listFeatureConnections,
  getLayerSchema,
} from './features';

// ── Data Sources ──────────────────────────────────────────────────────────────
export * from './datasources';

// ── Schedules ─────────────────────────────────────────────────────────────────
export * from './schedules';

// ── CARICA (scrittura su DB) ───────────────────────────────────────────────────
export {
  getCaricaSchemas,
  getCaricaTables,
  getCaricaColumns,
  postCaricaAutoMapping,
} from './carica';

// ── Log di sistema ────────────────────────────────────────────────────────────
export {
  getLogs,
  getLogsStats,
  clearLogs,
} from './logs';

// ── Trasformazioni e funzioni SQL ─────────────────────────────────────────────
export {
  getTransformRegistry,
  getFunctionsCatalog,
  translateFunction,
} from './transforms';

// ── Template API ────────────────────────────────────────────────
export {
  getTemplates,
} from './templates';

// ── Schema inference e validazione connessioni ────────────────────────────────
export {
  getProjectSchemas,
  getNodeSchema,
  validateProjectConnection,
  validateAllProjectSources,
} from './projects';

// ── Task creation ─────────────────────────────────────────────────────────────
export { createTask, downloadTaskOutput, cleanupTaskCompletely } from './tasks';

// ── Eseguibili (catene on-demand) ─────────────────────────────────────────────
export {
  listEseguibili,
  getEseguibile,
  createEseguibile,
  deleteEseguibile,
  analyzeUpload as analyzeEseguibileUpload,
  executeEseguibile,
} from './eseguibili';

// ── Shares (cross-group sharing / allowed_groups) ─────────────────────────────
export {
  getShares,
  setShares,
  addShare,
  removeShare,
  VALID_RESOURCE_TYPES as SHARES_VALID_RESOURCE_TYPES,
} from './shares';
