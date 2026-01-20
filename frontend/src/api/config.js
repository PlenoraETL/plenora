/**
 * Configurazione centralizzata per API
 * 
 * Tutte le costanti e configurazioni API dovrebbero essere importate da qui
 * per garantire consistenza in tutto il progetto.
 */

// Base URL dell'API - usa variabile ambiente se disponibile
export const API_BASE_URL = import.meta.env.VITE_API_URL || 'http://localhost:8000';

// URL completo per le API (con prefisso /api)
export const API_ENDPOINT = `${API_BASE_URL}/api`;

// Timeout default per le richieste (in ms)
export const API_TIMEOUT = 60000;

// Headers default
export const DEFAULT_HEADERS = {
  'Content-Type': 'application/json',
};

export default {
  API_BASE_URL,
  API_ENDPOINT,
  API_TIMEOUT,
  DEFAULT_HEADERS,
};
