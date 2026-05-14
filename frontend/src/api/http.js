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
import { API_ENDPOINT, API_TIMEOUT, EXPECTED_API_VERSION } from './config';
import { authBus } from '../utils/authBus';

const http = axios.create({
  baseURL: API_ENDPOINT,
  timeout: API_TIMEOUT,
  // NON impostiamo Content-Type come default fisso: axios lo gestisce automaticamente.
  // - JSON body  → axios imposta application/json
  // - FormData   → axios imposta multipart/form-data; boundary=...
  // Impostarlo qui come default blocca il boundary su FormData (bug axios >= 1.0).
});
http.interceptors.request.use((config) => {
  const token = sessionStorage.getItem('urby_token');
  if (token) {
    config.headers.Authorization = `Bearer ${token}`;
  }
  return config;
});

// API version drift detector. Su rolling deploy con bundle FE su una version
// e backend su un'altra, ogni response porta `X-API-Version`; logghiamo un
// warning unico al primo mismatch osservato per evitare spam in console.
let _versionMismatchLogged = false;
http.interceptors.response.use(
  (response) => {
    const observed = response.headers?.['x-api-version'];
    if (observed && observed !== EXPECTED_API_VERSION && !_versionMismatchLogged) {
      _versionMismatchLogged = true;
      console.warn(
        `[API Version Mismatch] bundle compilato per ${EXPECTED_API_VERSION}, server risponde ${observed}. ` +
        'Possibile rolling deploy in corso: ricarica la pagina se vedi comportamenti anomali.'
      );
    }
    return response;
  },
  (error) => {
    // Precedenza: new envelope `message` → legacy FastAPI `detail` → JS Error.
    // Il BE dual-shape mantiene `detail` come mirror durante la transizione.
    const data = error.response?.data;
    const message =
      data?.message                 ||
      data?.detail                  ||
      error.message                 ||
      'Errore di comunicazione con il server';

    // Attach structured error code from new envelope (e.g. "VALIDATION_ERROR").
    // I consumer possono branchare su err.code invece di parsare stringhe.
    if (data?.error && typeof data.error === 'string') {
      try { error.code = data.error; } catch { /* non scrivibile, ignora */ }
    }

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

    // Sessione scaduta → delega al contesto React tramite authBus.
    // Eccezione: la call a /auth/logout (revoca server-side) NON deve
    // triggerare onUnauthorized — altrimenti un token scaduto passato
    // al logout innescherebbe un ciclo logout→401→logout. Il logout
    // lato client procede comunque a ripulire sessionStorage: il
    // risultato della blacklist e' best-effort, non bloccante.
    const requestUrl = error.config?.url || '';
    const isLogoutCall = /\/auth\/logout(\?|$)/.test(requestUrl);
    if (error.response?.status === 401 && !isLogoutCall) {
      sessionStorage.removeItem('urby_token');
      sessionStorage.removeItem('urby_user');
      authBus.onUnauthorized?.();
    }

    // Preserva l'AxiosError originale con .response/.status/.config/.code:
    // molti consumer branchano su err.response?.status (es. 409 dipendenze
    // in ProjectList, 400 validation in form). Sovrascriviamo solo il
    // .message per avere il testo user-friendly senza perdere lo shape
    // originale.
    if (error && typeof error === 'object') {
      try { error.message = message; } catch { /* message non scrivibile, amen */ }
      return Promise.reject(error);
    }
    return Promise.reject(new Error(message));
  }
);

export default http;
