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
import { assertFileSize, throwIfUploadTooLarge } from './uploadGuards';

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
export const uploadFile = async (file, name, description = '', layer = '', sheet = '', selectedColumns = null, srid = null) => {
  // B3 Phase 4: pre-check size lato client; il backend cappa a
  // settings.MAX_FILE_SIZE (default 100MB) e risponde 413, ma evitiamo
  // il roundtrip per file ovviamente fuori soglia.
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
    const response = await http.post('/files/upload', formData);
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
 * Ottieni lista file
 * @returns {Promise<{files: Array}>}
 */
export const getFiles = async () => {
  const response = await http.get('/files/');
  return response.data;
};

/**
 * Ottieni dettagli file
 * @param {string} fileId - ID del file
 * @returns {Promise<Object>} FileSource
 */
export const getFile = async (fileId) => {
  const response = await http.get(`/files/${fileId}`);
  return response.data;
};

/**
 * Aggiorna file
 * @param {string} fileId - ID del file
 * @param {Object} data - Dati da aggiornare {name, description, layer, sheet}
 * @returns {Promise<Object>} FileSource aggiornato
 */
/**
 * Upload file multi-layer (GPKG, GDB): crea N FileSource separati, uno per layer.
 * @param {File} file - File da uploadare
 * @param {string} name - Nome base (ogni FileSource si chiamerà name_layername)
 * @param {string} description - Descrizione opzionale
 * @param {Array<{layer: string, selected_columns: string[]|null}>} layersConfig - Configurazione per ogni layer
 * @param {number|null} srid - SRID opzionale
 * @returns {Promise<Array>} Lista di FileSource creati
 */
export const uploadMultiLayer = async (file, name, description = '', layersConfig = [], srid = null) => {
  // B3 Phase 4: pre-check size lato client (vedi uploadFile sopra).
  assertFileSize(file);

  const formData = new FormData();
  formData.append('file', file);
  formData.append('name', name);
  formData.append('description', description);
  formData.append('layers_config', JSON.stringify(layersConfig));
  if (srid) formData.append('srid', String(srid));

  try {
    const response = await http.post('/files/upload-multi-layer', formData);
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
export const previewFileTemp = async (file, layer = null, sheet = null) => {
  // B3 Phase 4: anche la preview-temp passa per il cap backend
  // (`_enforce_upload_size_limit`). Pre-check client per coerenza UX.
  assertFileSize(file);

  const formData = new FormData();
  formData.append('file', file);
  if (layer) formData.append('layer', layer);
  if (sheet) formData.append('sheet', sheet);

  try {
    const response = await http.post('/files/preview-temp', formData);
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
  deleteFile,
  getFileLayers,
  getFilePreview,
  previewFileTemp,
};
