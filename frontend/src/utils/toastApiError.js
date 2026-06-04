/**
 * toastApiError — helper unificato per mostrare errori API via toast.
 *
 * Pre-fix il pattern `toast.error('Errore X: ' + (e?.message || 'errore
 * sconosciuto'))` era duplicato in ~10 punti tra hook e pagine, con
 * piccole varianti (`e?.message`, `err?.message`, `error?.message`,
 * concat con `+`, template literal). Manutenzione del wording o del
 * formato richiedeva edit sincronizzati.
 *
 * Uso:
 * ```js
 * try { await action(); }
 * catch (e) { toastApiError(toast, e, 'Errore eliminazione'); }
 * ```
 *
 * Estrae il messaggio dall'eccezione (axios o nativo) con fallback
 * "errore sconosciuto" — pattern Go-friendly: funzione pura, no state,
 * tre parametri espliciti.
 */

import { humanizeError } from './errorMessages';

/**
 * @param {{error: (msg: string) => void}} toast - istanza toast (da `useToast`).
 * @param {unknown} err - eccezione catturata.
 * @param {string} prefix - prefisso user-facing (es. "Errore eliminazione").
 *
 * Da Epic A: l'errore passa per `humanizeError`, che traduce i codici
 * strutturati del backend (es. `connection_in_use`) in messaggi azionabili e
 * non espone mai stack/SDK/"Request failed...". Il `prefix` resta come contesto.
 */
export function toastApiError(toast, err, prefix) {
  const { message } = humanizeError(err);
  toast.error(prefix ? `${prefix}: ${message}` : message);
}

export default toastApiError;
