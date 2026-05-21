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

const DEFAULT_UNKNOWN = 'errore sconosciuto';

/**
 * @param {{error: (msg: string) => void}} toast - istanza toast (da `useToast`).
 * @param {unknown} err - eccezione catturata.
 * @param {string} prefix - prefisso user-facing (es. "Errore eliminazione").
 * @param {string} [unknownMessage] - fallback se err non ha message.
 */
export function toastApiError(toast, err, prefix, unknownMessage = DEFAULT_UNKNOWN) {
  const msg = (err && typeof err === 'object' && 'message' in err && err.message)
    ? String(err.message)
    : unknownMessage;
  toast.error(`${prefix}: ${msg}`);
}

export default toastApiError;
