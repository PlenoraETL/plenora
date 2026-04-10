/**
 * http.js — Istanza axios condivisa
 *
 * Questo è l'unico posto dove viene creato il client HTTP.
 * Tutti gli altri moduli API lo importano da qui.
 *
 * Interceptors configurati:
 *   - REQUEST: aggiunge Bearer token da sessionStorage
 *   - RESPONSE: normalizza gli errori e gestisce il logout su 401
 *
 * Per usare il client direttamente (casi eccezionali):
 *   import http from '../api/http';
 *   const res = await http.get('/my-endpoint');
 */

import axios from 'axios';
import { API_ENDPOINT, API_TIMEOUT } from './config';
import { authBus } from '../utils/authBus';

const http = axios.create({
  baseURL: API_ENDPOINT,
  timeout: API_TIMEOUT,
  // NON impostiamo Content-Type come default fisso: axios lo gestisce automaticamente.
  // - JSON body  → axios imposta application/json
  // - FormData   → axios imposta multipart/form-data; boundary=...
  // Impostarlo qui come default blocca il boundary su FormData (bug axios >= 1.0).
});

// ── REQUEST: inietta il Bearer token ─────────────────────────────────────────
http.interceptors.request.use((config) => {
  const token = sessionStorage.getItem('urby_token');
  if (token) {
    config.headers.Authorization = `Bearer ${token}`;
  }
  return config;
});

// ── RESPONSE: normalizza errori + gestisce sessione scaduta ──────────────────
http.interceptors.response.use(
  (response) => response,
  (error) => {
    const message =
      error.response?.data?.detail  ||
      error.response?.data?.message ||
      error.message                 ||
      'Errore di comunicazione con il server';

    // Silenzia i 404 sui task — sono normali durante il polling.
    // Pattern strict: matcha solo GET su /tasks/{uuid-like} (il polling).
    // Prima: `/tasks/` matchava qualsiasi URL con quel segmento, inclusi
    // typo in URL o DELETE su endpoint sbagliato. Con il pattern strict,
    // solo il polling legittimo viene silenziato.
    const isTaskPolling =
      error.response?.status === 404 &&
      error.config?.method === 'get' &&
      /\/tasks\/[a-f0-9-]{8,}/.test(error.config?.url || '');

    if (!isTaskPolling) {
      console.error('[API Error]', {
        url:     error.config?.url,
        method:  error.config?.method,
        status:  error.response?.status,
        message,
      });
    }

    // Sessione scaduta → delega al contesto React tramite authBus
    if (error.response?.status === 401) {
      sessionStorage.removeItem('urby_token');
      sessionStorage.removeItem('urby_user');
      authBus.onUnauthorized?.();
    }

    return Promise.reject(new Error(message));
  }
);

export default http;
