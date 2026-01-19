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

import axios from 'axios';
import { API_ENDPOINT, API_TIMEOUT } from './config';

// Client axios dedicato per files API
const filesApi = axios.create({
  baseURL: `${API_ENDPOINT}/files`,
  timeout: API_TIMEOUT,
});

// Interceptor per error handling consistente
filesApi.interceptors.response.use(
  (response) => response,
  (error) => {
    const message = 
      error.response?.data?.detail || 
      error.response?.data?.message || 
      error.message || 
      'Errore di comunicazione con il server';
    
    console.error('[Files API Error]', {
      url: error.config?.url,
      method: error.config?.method,
      status: error.response?.status,
      message
    });
    
    return Promise.reject(new Error(message));
  }
);

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

  const response = await filesApi.post('/upload', formData, {
    headers: { 'Content-Type': 'multipart/form-data' }
  });

  return response.data;
};

/**
 * Ottieni lista file
 * @returns {Promise<{files: Array}>}
 */
export const getFiles = async () => {
  const response = await filesApi.get('/');
  return response.data;
};

/**
 * Ottieni dettagli file
 * @param {string} fileId - ID del file
 * @returns {Promise<Object>} FileSource
 */
export const getFile = async (fileId) => {
  const response = await filesApi.get(`/${fileId}`);
  return response.data;
};

/**
 * Aggiorna file
 * @param {string} fileId - ID del file
 * @param {Object} data - Dati da aggiornare {name, description, layer, sheet}
 * @returns {Promise<Object>} FileSource aggiornato
 */
export const updateFile = async (fileId, data) => {
  const response = await filesApi.put(`/${fileId}`, data);
  return response.data;
};

/**
 * Elimina file
 * @param {string} fileId - ID del file
 * @returns {Promise<Object>}
 */
export const deleteFile = async (fileId) => {
  const response = await filesApi.delete(`/${fileId}`);
  return response.data;
};

/**
 * Ottieni layers/sheets disponibili in un file
 * @param {string} fileId - ID del file
 * @returns {Promise<{layers: Array, sheets: Array}>}
 */
export const getFileLayers = async (fileId) => {
  const response = await filesApi.get(`/${fileId}/layers`);
  return response.data;
};

/**
 * Ottieni preview dati
 * @param {string} fileId - ID del file
 * @param {number} limit - Numero righe (default 100)
 * @returns {Promise<{columns: Array, rows: Array, total_count: number}>}
 */
export const getFilePreview = async (fileId, limit = 100) => {
  const response = await filesApi.get(`/${fileId}/preview`, {
    params: { limit }
  });
  return response.data;
};

/**
 * Ottieni conteggio righe
 * @param {string} fileId - ID del file
 * @returns {Promise<{row_count: number}>}
 */
export const getFileCount = async (fileId) => {
  const response = await filesApi.get(`/${fileId}/count`);
  return response.data;
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

  const response = await filesApi.post('/preview-temp', formData, {
    headers: { 'Content-Type': 'multipart/form-data' }
  });

  return response.data;
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
