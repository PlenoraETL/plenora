/**
 * API Client per File Source
 * 
 * Gestisce le chiamate al backend per:
 * - Upload file
 * - Lista file
 * - Dettagli file
 * - Preview dati
 * - Eliminazione
 * 
 * Usa axios per consistenza con client.js
 */

/**
 * Usa http (istanza axios condivisa) invece di un client dedicato.
 * Auth, error handling e logout 401 sono già gestiti dagli interceptor di http.js.
 */
import http from './http';
import { fetchAllCursorPages } from './pagination';
import { assertFileSize, throwIfUploadTooLarge } from './uploadGuards';
import { uploadProgress } from '../utils/transferProgress';

/**
 * Upload un nuovo file
 * @param {File} file - File da caricare
 * @param {string} name - Nome descrittivo
 * @param {string} description - Descrizione opzionale
 * @param {string} layer - Layer da usare (per GDB/GPKG)
 * @param {string} sheet - Sheet da usare (per XLSX)
 * @param {Array<string>} selectedColumns - Colonne da importare (null = tutte)
 * @param {number} srid - SRID per colonne geometriche (null = auto)
 * @returns {Promise<Object>} FileSource creato
 */
export const uploadFile = async (file, name, description = '', layer = '', sheet = '', selectedColumns = null, srid = null, { onProgress = null } = {}) => {
  // Evita il roundtrip per file ovviamente fuori soglia; il backend
  // resta il guard autorevole e risponde 413.
  assertFileSize(file);

  const formData = new FormData();
  formData.append('file', file);
  formData.append('name', name);
  formData.append('description', description);
  if (layer) formData.append('layer', layer);
  if (sheet) formData.append('sheet', sheet);
  if (selectedColumns && selectedColumns.length > 0) {
    formData.append('selected_columns', JSON.stringify(selectedColumns));
  }
  if (srid) {
    formData.append('srid', String(srid));
  }

  try {
    const response = await http.post('/files/upload', formData, uploadProgress(onProgress));
    return response.data;
  } catch (err) {
    // Normalizza 413: i caller possono branchare su
    // err instanceof UploadTooLargeError sia per rifiuto client-side
    // che server-side. Tutti gli altri errori passano invariati.
    throwIfUploadTooLarge(err);
    throw err;
  }
};

/**
 * Ottieni lista file.
 *
 * Pre-fix (P0 contracts-schemas): URL `/files/` con trailing slash;
 * BE espone `/files` (senza slash). FastAPI default `redirect_slashes=True`
 * produce 307 redirect transparent ma fragile: in setup con proxy
 * che fa rewrite o `redirect_slashes=False`, il client riceve 404 o
 * 307 non gestito. Post-fix: `/files` allineato al router BE.
 *
 * UI-1 (C5, C26): tutte le pagine (il server ne da' al massimo 500 per
 * richiesta: prima la lista si fermava a 200 senza avviso) e ogni file
 * SENZA le colonne: nella lista ci sono `column_count` e i primi nomi
 * (`column_names_preview`); le colonne intere con getFile(id).
 *
 * @returns {Promise<{files: Array}>}
 */
// Pagine del cursore della dimensione massima che il server accetta
// (GET /files `limit` le=500): con un cursore ogni pagina dipende dalla
// precedente, quindi le pagine sono in sequenza per costruzione (conferma
// finale F7, scelta dichiarata); pagine piu' grandi = meno giri.
export const FILES_PAGE_SIZE = 500;

export const getFiles = async () => fetchAllCursorPages('/files', {
  pageSize: FILES_PAGE_SIZE,
  extractItems: data => data.files,
  mergeItems: (items, lastPage) => ({ ...lastPage, files: items }),
});

/**
 * Ottieni dettagli file
 * @param {string} fileId - ID del file
 * @returns {Promise<Object>} FileSource
 */
export const getFile = async (fileId) => {
  const response = await http.get(`/files/${fileId}`);
  return response.data;
};

/** Aggiorna metadati e selezione layer/sheet di un FileSource. */
export const updateFile = async (fileId, data) => {
  const response = await http.put(`/files/${fileId}`, data);
  return response.data;
};

/**
 * Upload file multi-layer (GPKG, GDB): crea N FileSource separati, uno per layer.
 * @param {File} file - File da uploadare
 * @param {string} name - Nome base (ogni FileSource si chiamerà name_layername)
 * @param {string} description - Descrizione opzionale
 * @param {Array<{layer: string, selected_columns: string[]|null}>} layersConfig - Configurazione per ogni layer
 * @param {number|null} srid - SRID opzionale
 * @returns {Promise<Array>} Lista di FileSource creati
 */
export const uploadMultiLayer = async (file, name, description = '', layersConfig = [], srid = null, { onProgress = null } = {}) => {
  assertFileSize(file);

  const formData = new FormData();
  formData.append('file', file);
  formData.append('name', name);
  formData.append('description', description);
  formData.append('layers_config', JSON.stringify(layersConfig));
  if (srid) formData.append('srid', String(srid));

  try {
    const response = await http.post('/files/upload-multi-layer', formData, uploadProgress(onProgress));
    return response.data;
  } catch (err) {
    throwIfUploadTooLarge(err);
    throw err;
  }
};

/**
 * Elimina file
 * @param {string} fileId - ID del file
 * @returns {Promise<Object>}
 */
export const deleteFile = async (fileId) => {
  const response = await http.delete(`/files/${fileId}`);
  return response.data;
};

/**
 * Ottieni layers/sheets disponibili in un file
 * @param {string} fileId - ID del file
 * @returns {Promise<{layers: Array, sheets: Array}>}
 */
export const getFileLayers = async (fileId) => {
  const response = await http.get(`/files/${fileId}/layers`);
  return response.data;
};

/**
 * Ottieni preview dati
 * @param {string} fileId - ID del file
 * @param {number} limit - Numero righe (default 100)
 * @returns {Promise<{columns: Array, rows: Array, total_count: number}>}
 */
export const getFilePreview = async (fileId, limit = 100) => {
  const response = await http.get(`/files/${fileId}/preview`, {
    params: { limit }
  });
  return response.data;
};

/**
 * Preview temporanea di un file (senza salvarlo)
 * Usato per mostrare le colonne disponibili prima dell'upload
 * @param {File} file - File da analizzare
 * @returns {Promise<{columns: Array, row_count: number}>}
 */
export const previewFileTemp = async (file, layer = null, sheet = null, { onProgress = null } = {}) => {
  assertFileSize(file);

  const formData = new FormData();
  formData.append('file', file);
  if (layer) formData.append('layer', layer);
  if (sheet) formData.append('sheet', sheet);

  try {
    const response = await http.post('/files/preview-temp', formData, uploadProgress(onProgress));
    return response.data;
  } catch (err) {
    throwIfUploadTooLarge(err);
    throw err;
  }
};

export default {
  uploadFile,
  getFiles,
  getFile,
  updateFile,
  deleteFile,
  getFileLayers,
  getFilePreview,
  previewFileTemp,
};
