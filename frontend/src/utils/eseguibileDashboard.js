/**
 * eseguibileDashboard — le regole della dashboard Grafana di un Eseguibile
 * nel frontend (11/11/2026). UNA funzione per domanda:
 *
 *  - `dashboardFeatureOn(item)`: la funzione e' attiva sul server
 *    (`dashboard_feature_enabled` di lista e dettaglio); spenta = nessuna
 *    casella ne' voce;
 *  - `dashboardTarget(...)`: la finestra dei risultati offre «Apri
 *    dashboard»? Una sola regola per tutti i percorsi (finestra con il
 *    manifest e finestra legacy, da ogni pagina): la applica
 *    components/eseguibili/useEseguibileRun e i percorsi la ricevono;
 *  - `safeGrafanaPath(path)`: un indirizzo del server si apre (iframe o
 *    scheda) solo se e' un percorso relativo sotto /grafana/ dello stesso
 *    origine;
 *  - tempi del rinnovo di lasciapassare e sessione (hooks/useDashboardPass).
 */
import { can } from './permissions';

/** Stati del risultato (useEseguibileRun.resultState) in cui la dashboard si apre. */
export const OPENABLE_RESULT_STATES = Object.freeze(['on_server', 'local']);

/** La funzione e' attiva sul server per questo Eseguibile (lista o dettaglio). */
export const dashboardFeatureOn = (item) => item?.dashboard_feature_enabled === true;

/**
 * La funzione e' attiva? Dalla lista degli Eseguibili: true/false se almeno
 * uno lo dice, null se la lista e' vuota (non si sa: la casella si mostra e
 * il server risponde `grafana_disabled` con il suo messaggio).
 */
export function dashboardFeatureFromList(items) {
  const list = Array.isArray(items) ? items : [];
  if (list.length === 0) return null;
  return list.some(dashboardFeatureOn);
}

/** Avvisi della dashboard da mostrare (solo a chi puo' modificare). */
export const dashboardWarningsFor = (user, eseguibile) => (
  can.manageEseguibileDashboard(user, eseguibile) && Array.isArray(eseguibile?.dashboard_warnings)
    ? eseguibile.dashboard_warnings
    : []
);

/**
 * «Apri dashboard» nella finestra dei risultati: il bersaglio o null.
 * Condizioni (tutte): chi esegue (can.openEseguibileDashboard),
 * l'Eseguibile ha la dashboard (`has_dashboard`, gia' falso a funzione
 * spenta), l'output non e' un file del connettore (`output_is_file_based`:
 * nessuna copia per la dashboard), l'esecuzione e' riuscita e il risultato
 * e' ancora qui (`on_server` o `local`: in `gone` no).
 */
export function dashboardTarget({ user, eseguibile, taskId, resultState }) {
  if (!eseguibile?.id || !taskId) return null;
  if (!can.openEseguibileDashboard(user)) return null;
  if (eseguibile.has_dashboard !== true || eseguibile.output_is_file_based === true) return null;
  if (!OPENABLE_RESULT_STATES.includes(resultState)) return null;
  return {
    eseguibileId: eseguibile.id,
    taskId,
    // L'Eseguibile per «Modifica in Grafana» (permesso con il flag del server).
    item: { id: eseguibile.id, name: eseguibile.name || '', can_edit: eseguibile.can_edit },
    warnings: dashboardWarningsFor(user, eseguibile),
  };
}

/** Testo degli avvisi della dashboard (colonne che non ci sono piu'), o null. */
export function dashboardWarningsText(warnings) {
  const list = Array.isArray(warnings) ? warnings : [];
  if (list.length === 0) return null;
  const parts = list.map(w => (w?.column ? `«${w.column}»: ${w.motivo}` : w?.motivo)).filter(Boolean);
  return parts.length ? `Dashboard da controllare: ${parts.join('; ')}` : null;
}

/**
 * Avvisi della risposta di crea, «Modifica in Grafana» e pubblicazione (le
 * vede solo chi puo' modificare: sono sue azioni) come toast di avviso.
 */
export function toastDashboardWarnings(toast, warnings) {
  const text = dashboardWarningsText(warnings);
  if (text) toast?.warning?.(text, 8000);
  return Boolean(text);
}

/** Percorso relativo sotto /grafana/ (stesso origine) o null. */
export function safeGrafanaPath(path) {
  if (typeof path !== 'string') return null;
  if (!/^\/grafana\/[^/\\]/.test(path)) return null;
  if (/[\s\\]/.test(path)) return null;
  return path;
}

// Rinnovo di lasciapassare e sessione (cookie di 30 min sul server).
export const RENEW_EVERY_MS = 20 * 60_000;
export const RENEW_MARGIN_MS = 60_000;
const MIN_DELAY_MS = 5_000;

/**
 * Quando rinnovare dopo un lasciapassare `{expires_at, data_expires_at}`
 * (epoch in secondi): `{ at, expire }`. `expire: true` = a quell'ora i dati
 * non ci sono piu' (il lasciapassare non va oltre `data_expires_at`): non
 * si rinnova, la dashboard dice «dati non piu' disponibili».
 */
export function nextRenewal(pass, now = Date.now()) {
  const expiresMs = Number(pass?.expires_at) * 1000;
  const dataMs = Number(pass?.data_expires_at) * 1000;
  if (Number.isFinite(dataMs) && dataMs > 0 && (!Number.isFinite(expiresMs) || expiresMs >= dataMs)) {
    return { at: Math.max(now, dataMs), expire: true };
  }
  const byExpiry = Number.isFinite(expiresMs) ? expiresMs - RENEW_MARGIN_MS : now + RENEW_EVERY_MS;
  return { at: Math.max(now + MIN_DELAY_MS, Math.min(now + RENEW_EVERY_MS, byExpiry)), expire: false };
}
