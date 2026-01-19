/**
 * API Module - Export centralizzato
 * 
 * Importa da qui tutte le funzioni API:
 * 
 * import { getProject, getFiles, API_ENDPOINT } from '../api';
 */

// Config
export { API_BASE_URL, API_ENDPOINT, API_TIMEOUT, DEFAULT_HEADERS } from './config';

// Client principale (projects, connections, catalog, etc.)
export {
  // Catalog
  uploadCatalog,
  getCatalog,
  getTableColumns,
  // Connections
  createConnection,
  listConnections,
  getConnection,
  updateConnection,
  deleteConnection,
  testConnection,
  getConnectionTables,
  // Projects
  createProject,
  listProjects,
  getProject,
  updateCanvas,
  deleteProject,
  executeProjectQuery,
  previewProject,
  // API Connections
  listApiConnections,
  // Download
  getDownloadUrl,
  getProjectOutputInfo,
  downloadProjectOutput,
} from './client';

// Files API
export {
  uploadFile,
  getFiles,
  getFile,
  updateFile,
  deleteFile,
  getFileLayers,
  getFilePreview,
  getFileCount,
  previewFileTemp,
} from './files';

// Default export del client axios principale
export { default as api } from './client';
