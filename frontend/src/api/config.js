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
export const API_TIMEOUT = 600000;

// API contract version contro cui questo bundle e' stato compilato. Deve
// allinearsi con backend/app/config.py:API_VERSION. Su mismatch (rolling
// deploy con un BE su version diversa) il response interceptor di http.js
// logga un warning unico; bump esplicito qui + sul BE al cambio breaking
// del contratto FE↔BE.
export const EXPECTED_API_VERSION = "1.0";

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
