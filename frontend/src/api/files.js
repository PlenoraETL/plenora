/**
 * API Client per File Source
 * 
 * Gestisce le chiamate al backend per:
 * - Upload file
 * - Lista file
 * - Dettagli file
 * - Preview dati
 * - Eliminazione
 */

const API_BASE = import.meta.env.VITE_API_URL || 'http://localhost:8000';

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

  const response = await fetch(`${API_BASE}/api/files/upload`, {
    method: 'POST',
    body: formData,
  });

  if (!response.ok) {
    const error = await response.json();
    throw new Error(error.detail || 'Errore upload file');
  }

  return response.json();
};

/**
 * Ottieni lista file
 * @returns {Promise<{files: Array}>}
 */
export const getFiles = async () => {
  const response = await fetch(`${API_BASE}/api/files`);
  
  if (!response.ok) {
    throw new Error('Errore caricamento file');
  }

  return response.json();
};

/**
 * Ottieni dettagli file
 * @param {string} fileId - ID del file
 * @returns {Promise<Object>} FileSource
 */
export const getFile = async (fileId) => {
  const response = await fetch(`${API_BASE}/api/files/${fileId}`);
  
  if (!response.ok) {
    const error = await response.json();
    throw new Error(error.detail || 'File non trovato');
  }

  return response.json();
};

/**
 * Aggiorna file
 * @param {string} fileId - ID del file
 * @param {Object} data - Dati da aggiornare {name, description, layer, sheet}
 * @returns {Promise<Object>} FileSource aggiornato
 */
export const updateFile = async (fileId, data) => {
  const response = await fetch(`${API_BASE}/api/files/${fileId}`, {
    method: 'PUT',
    headers: {
      'Content-Type': 'application/json',
    },
    body: JSON.stringify(data),
  });

  if (!response.ok) {
    const error = await response.json();
    throw new Error(error.detail || 'Errore aggiornamento');
  }

  return response.json();
};

/**
 * Elimina file
 * @param {string} fileId - ID del file
 * @returns {Promise<void>}
 */
export const deleteFile = async (fileId) => {
  const response = await fetch(`${API_BASE}/api/files/${fileId}`, {
    method: 'DELETE',
  });

  if (!response.ok) {
    const error = await response.json();
    throw new Error(error.detail || 'Errore eliminazione');
  }

  return response.json();
};

/**
 * Ottieni layers/sheets disponibili in un file
 * @param {string} fileId - ID del file
 * @returns {Promise<{layers: Array, sheets: Array}>}
 */
export const getFileLayers = async (fileId) => {
  const response = await fetch(`${API_BASE}/api/files/${fileId}/layers`);
  
  if (!response.ok) {
    const error = await response.json();
    throw new Error(error.detail || 'Errore caricamento layers');
  }

  return response.json();
};

/**
 * Ottieni preview dati
 * @param {string} fileId - ID del file
 * @param {number} limit - Numero righe (default 100)
 * @returns {Promise<{columns: Array, rows: Array, total_count: number}>}
 */
export const getFilePreview = async (fileId, limit = 100) => {
  const response = await fetch(`${API_BASE}/api/files/${fileId}/preview?limit=${limit}`);
  
  if (!response.ok) {
    const error = await response.json();
    throw new Error(error.detail || 'Errore preview');
  }

  return response.json();
};

/**
 * Ottieni conteggio righe
 * @param {string} fileId - ID del file
 * @returns {Promise<{row_count: number}>}
 */
export const getFileCount = async (fileId) => {
  const response = await fetch(`${API_BASE}/api/files/${fileId}/count`);
  
  if (!response.ok) {
    const error = await response.json();
    throw new Error(error.detail || 'Errore conteggio');
  }

  return response.json();
};

/**
 * Preview temporanea di un file (senza salvarlo)
 * Usato per mostrare le colonne disponibili prima dell'upload
 * @param {File} file - File da analizzare
 * @returns {Promise<{columns: Array, row_count: number}>}
 */
export const previewFileTemp = async (file) => {
  const formData = new FormData();
  formData.append('file', file);

  const response = await fetch(`${API_BASE}/api/files/preview-temp`, {
    method: 'POST',
    body: formData,
  });

  if (!response.ok) {
    const error = await response.json();
    throw new Error(error.detail || 'Errore lettura file');
  }

  return response.json();
};

export default {
  uploadFile,
  getFiles,
  getFile,
  updateFile,
  deleteFile,
  getFileLayers,
  getFilePreview,
  getFileCount,
  previewFileTemp,
};