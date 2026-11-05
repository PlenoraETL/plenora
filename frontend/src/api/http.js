/**
 * http.js — Istanza axios condivisa
 *
 * Questo è l'unico posto dove viene creato il client HTTP.
 * Tutti gli altri moduli API lo importano da qui.
 *
 * Interceptors configurati:
 *   - REQUEST: aggiunge Bearer token da sessionStorage
 *   - RESPONSE: normalizza gli errori (utils/errorMessages) e chiude la
 *     sessione su 401 (handleUnauthorized)
 *
 * Per usare il client direttamente (casi eccezionali):
 *   import http from '../api/http';
 *   const res = await http.get('/my-endpoint');
 */

import axios from 'axios';
import { API_ENDPOINT, API_TIMEOUT, EXPECTED_API_VERSION } from './config';
import { authBus } from '../utils/authBus';
import { requestErrorMessage } from '../utils/errorMessages';
import { registerSessionReset } from '../utils/session';

const TOKEN_KEY = 'urby_token';

// Le chiamate di autenticazione rispondono 401 per motivi che non sono una
// sessione scaduta (credenziali errate al login, token gia' scaduto passato
// alla revoca): non chiudono la sessione (N9).
const AUTH_CALL_WITHOUT_SESSION = /\/auth\/(login|logout)(\?|$)/;

function sentBearerToken(config) {
  const headers = config?.headers;
  if (!headers) return null;
  const raw = typeof headers.get === 'function'
    ? headers.get('Authorization')
    : (headers.Authorization ?? headers.authorization);
  if (typeof raw !== 'string') return null;
  const m = /^Bearer\s+(.+)$/.exec(raw);
  return m ? m[1] : null;
}

/**
 * 401 → chiusura della sessione, una volta sola e solo per la sessione che
 * ha fatto la richiesta (R5.2).
 *
 * La sessione si chiude solo se il token inviato con la richiesta e' ancora
 * quello della sessione attuale. Per costruzione:
 *   - un 401 tardivo di una sessione precedente (token A, l'utente nel
 *     frattempo e' rientrato con B) non tocca la sessione nuova;
 *   - con N richieste parallele scadute la prima chiude la sessione, le
 *     altre trovano il token gia' in chiusura (`closingToken`) e non fanno
 *     nulla: `authBus.onUnauthorized` (logout + /login in AuthContext) parte
 *     una volta sola per sessione.
 * REGOLA (Fase 5, correzione dopo la conferma): qui NON si tocca lo storage
 * della sessione. Lo svuota UN solo punto, AuthContext.logout(), DOPO
 * authBus.emitBeforeLogout(): chi ha lavoro in sospeso (copia di sicurezza
 * del canvas) lo scrive mentre l'utente e' ancora noto.
 *
 * @returns {boolean} true se la sessione e' stata chiusa.
 */
let closingToken = null;
// Il token in chiusura non sopravvive alla sessione (utils/session).
registerSessionReset('http401', () => { closingToken = null; });
export function handleUnauthorized(config) {
  if (AUTH_CALL_WITHOUT_SESSION.test(config?.url || '')) return false;
  const sent = sentBearerToken(config);
  if (!sent || sent !== sessionStorage.getItem(TOKEN_KEY) || sent === closingToken) return false;
  closingToken = sent;
  authBus.onUnauthorized?.();
  return true;
}

const http = axios.create({
  baseURL: API_ENDPOINT,
  timeout: API_TIMEOUT,
  // NON impostiamo Content-Type come default fisso: axios lo gestisce automaticamente.
  // - JSON body  → axios imposta application/json
  // - FormData   → axios imposta multipart/form-data; boundary=...
  // Impostarlo qui come default blocca il boundary su FormData (bug axios >= 1.0).
});
http.interceptors.request.use((config) => {
  const token = sessionStorage.getItem(TOKEN_KEY);
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
  async (error) => {
    let data = error?.response?.data;
    // Axios keeps JSON error responses as Blob when the request asked for a
    // binary download. Decode the envelope before normalizing the message.
    if (typeof Blob !== 'undefined' && data instanceof Blob) {
      try {
        data = JSON.parse(await data.text());
        error.response.data = data;
      } catch {
        // A real binary or malformed response: il messaggio sara' quello
        // della famiglia HTTP.
      }
    }
    // Attach structured error code from new envelope (e.g. "VALIDATION_ERROR").
    // I consumer possono branchare su err.code invece di parsare stringhe.
    if (data?.error && typeof data.error === 'string') {
      try { error.code = data.error; } catch { /* non scrivibile, ignora */ }
    }

    // Una sola normalizzazione (utils/errorMessages): messaggio del backend,
    // altrimenti testo curato del codice o testo italiano per famiglia
    // (rete, timeout, 401, 403, 404, 409, 422, 5xx). Mai "Network Error",
    // "timeout of ...ms exceeded" o "Request failed with status code 500".
    const message = requestErrorMessage(error);

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

    // Sessione scaduta: una sola regola, qui (R5.2, N9).
    if (error?.response?.status === 401) handleUnauthorized(error.config);

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
