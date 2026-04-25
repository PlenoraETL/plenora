/**
 * uploadGuards.js — guard client-side per upload file.
 *
 * Background (B3 Phase 4):
 *  Il backend Phase 3 (T-04) ha cappato gli upload via
 *  `app.shared.upload_guards.enforce_upload_size_limit` che restituisce 413
 *  con messaggio "File troppo grande (NMB). Limite massimo: NMB.". Per UX
 *  meno fragile (niente round-trip per file ovviamente troppo grandi,
 *  niente messaggi axios generici tipo "Network Error" su 413 con body
 *  troncato), facciamo una pre-check lato client e normalizziamo gli
 *  errori 413 propagati dal backend.
 *
 * Contratto (mirato al batch B3, no UI):
 *   - assertFileSize(file): rifiuta sincronamente con UploadTooLargeError
 *     se file.size > MAX_UPLOAD_SIZE_BYTES.
 *   - extract413Message(error): se error e' un AxiosError 413, ritorna il
 *     detail del backend; altrimenti ritorna null.
 *   - throwIfUploadTooLarge(error): rilancia UploadTooLargeError con il
 *     messaggio backend se 413, altrimenti ripropaga l'errore originale.
 *
 * Soglia: deve restare allineata con `settings.MAX_FILE_SIZE` (default
 * 100MB). Tenere allineati i due cap e' responsabilita' di chi cambia il
 * limite — non c'e' un endpoint per ottenerlo runtime, quindi qui resta
 * costante esposta. Override in test via spy/mock import diretto.
 */

/** Cap massimo upload in byte. Allineato a `settings.MAX_FILE_SIZE`
 * backend (`app/config.py:139`, default 100MB). */
export const MAX_UPLOAD_SIZE_BYTES = 100 * 1024 * 1024;

/** Cap specifico per gli allegati ticket. Allineato a
 * `TICKET_ATTACHMENT_MAX_BYTES` in `backend/app/core/tickets/router.py`
 * (default 10MB): senza un cap dedicato, `uploadAttachment` userebbe il
 * limite generale di 100MB e accetterebbe lato client file che il
 * backend rifiuta. Modifica accoppiata: se il backend cambia il cap
 * tickets, aggiornare anche questo valore. */
export const TICKET_ATTACHMENT_MAX_SIZE_BYTES = 10 * 1024 * 1024;

/** Errore tipato per file troppo grandi: lato chiamante puo' branchare
 * con `err instanceof UploadTooLargeError` per differenziare la UX
 * (toast specifico, hint "comprimi il file", ecc.) senza dover fare
 * string-match sul messaggio.
 */
export class UploadTooLargeError extends Error {
  constructor(message, { fileSize, maxSize, source } = {}) {
    super(message);
    this.name = 'UploadTooLargeError';
    this.fileSize = fileSize;
    this.maxSize = maxSize;
    // 'client' = bloccato pre-flight; 'server' = arrivato dal 413 backend.
    this.source = source || 'client';
  }
}

const _formatMB = (bytes) => Math.round((bytes || 0) / (1024 * 1024));

/**
 * Pre-check sincrona: rifiuta upload oltre il cap senza chiamare il backend.
 * @param {File|Blob|null|undefined} file
 * @param {{ maxBytes?: number }} [opts]
 * @throws {UploadTooLargeError} se file.size > maxBytes.
 */
export function assertFileSize(file, opts = {}) {
  const maxBytes = opts.maxBytes ?? MAX_UPLOAD_SIZE_BYTES;
  // file.size puo' essere undefined su input non-File (es. mock); in tal
  // caso saltiamo il check per non rompere flow legittimi (es. ri-upload
  // di Blob ricostruiti). Il backend resta la fonte di verita'.
  if (!file || typeof file.size !== 'number') return;
  if (file.size > maxBytes) {
    throw new UploadTooLargeError(
      `File troppo grande (${_formatMB(file.size)}MB). ` +
        `Limite massimo: ${_formatMB(maxBytes)}MB.`,
      { fileSize: file.size, maxSize: maxBytes, source: 'client' },
    );
  }
}

/**
 * Estrae il messaggio user-friendly da una risposta 413 del backend.
 * Restituisce null se l'errore non e' un 413.
 *
 * Schema atteso (FastAPI HTTPException):
 *   { detail: "File troppo grande (NMB). Limite massimo: NMB." }
 */
export function extract413Message(error) {
  if (!error || error.response?.status !== 413) return null;
  const data = error.response.data;
  if (typeof data === 'string') return data;
  if (data && typeof data.detail === 'string') return data.detail;
  if (data && typeof data.message === 'string') return data.message;
  return 'File troppo grande: rifiutato dal server.';
}

/**
 * Helper da usare in catch(): se l'errore e' 413, lancia
 * `UploadTooLargeError` con il messaggio backend; altrimenti rilancia
 * l'errore originale invariato.
 *
 * Esempio:
 *   try { await http.post('/files/upload', fd); }
 *   catch (e) { throwIfUploadTooLarge(e); throw e; }
 *
 * Vantaggio: i caller a valle (UI, store) possono usare
 * `instanceof UploadTooLargeError` come unico test, indipendentemente
 * dal fatto che il rifiuto sia avvenuto pre-flight o server-side.
 */
export function throwIfUploadTooLarge(error) {
  const detail = extract413Message(error);
  if (detail !== null) {
    throw new UploadTooLargeError(detail, {
      fileSize: undefined,
      maxSize: MAX_UPLOAD_SIZE_BYTES,
      source: 'server',
    });
  }
}
