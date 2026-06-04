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

export { API_BASE_URL, API_ENDPOINT, API_TIMEOUT, DEFAULT_HEADERS } from './config';

export { default as http } from './http';

export { default as api }  from './http';
export { default }         from './http';

export {
  createProject,
  cloneProject,
  listProjects,
  getProject,
  updateCanvas,
  deleteProject,
  executeProjectQuery,
  previewProject,
  getDownloadUrl,
  downloadProjectOutput,
} from './projects';

export {
  createConnection,
  listConnections,
  updateConnection,
  deleteConnection,
  testConnection,
  getConnectionTables,
} from './connections';

export {
  getTaskStatus,
  cancelTask,
} from './tasks';

export {
  validateEnrichment,
  estimateEnrichment,
  executeEnrichmentAsync,
} from './enrichment';

export {
  listApiConnections,
} from './apiConnections';

export {
  uploadFile,
  getFiles,
  getFile,
  deleteFile,
  getFileLayers,
  getFilePreview,
  previewFileTemp,
} from './files';

export {
  listFeatureConnections,
  getLayerSchema,
} from './features';

export * from './datasources';

export * from './schedules';

export {
  getCaricaSchemas,
  getCaricaTables,
  postCaricaAutoMapping,
} from './carica';

export {
  getLogs,
  getLogsStats,
} from './logs';

export {
  getTransformRegistry,
  getFunctionsCatalog,
  translateFunction,
} from './transforms';

export {
  getTemplates,
} from './templates';

export {
  getProjectSchemas,
  getNodeSchema,
  validateProjectConnection,
  validateAllProjectSources,
} from './projects';

export { createTask, downloadTaskOutput, cleanupTaskCompletely } from './tasks';

export {
  listEseguibili,
  getEseguibile,
  createEseguibile,
  deleteEseguibile,
  analyzeUpload as analyzeEseguibileUpload,
  executeEseguibile,
} from './eseguibili';

export {
  getShares,
  setShares,
  addShare,
  removeShare,
  VALID_RESOURCE_TYPES as SHARES_VALID_RESOURCE_TYPES,
} from './shares';
