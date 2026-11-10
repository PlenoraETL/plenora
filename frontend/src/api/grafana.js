/**
 * grafana.js — sessione per /grafana (11/11/2026).
 *
 * `POST /grafana/session` (Bearer) imposta il cookie dedicato HttpOnly con
 * Path=/grafana (durata GRAFANA_SESSION_TTL_SECONDS, 30 min): va chiamato
 * PRIMA di aprire l'iframe della dashboard o «Modifica in Grafana», e
 * richiamato per rinnovarlo (hooks/useDashboardPass). `DELETE` lo toglie
 * (logout, context/AuthContext).
 */
import http, { sendOnPageExit } from './http';

export const openGrafanaSession = async () => {
  await http.post('/grafana/session');
};

/**
 * Logout: toglie il cookie di /grafana. Best effort e subito (fetch
 * keepalive, come le pulizie all'uscita: la pagina cambia mentre parte;
 * nessun errore da mostrare). true = richiesta partita.
 */
export const closeGrafanaSession = () => sendOnPageExit('DELETE', '/grafana/session');
