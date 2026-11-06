/**
 * transferProgress — avanzamento dei caricamenti e degli scaricamenti di
 * file (UI-3, U17).
 *
 * Regola unica: OGNI richiesta del modulo api che invia un file (FormData)
 * o ne riceve uno (responseType 'blob') accetta un `onProgress` facoltativo
 * e lo passa ad axios con queste due funzioni. Il test
 * tests/shared/transferProgress.contract.test.js lo controlla su tutti i
 * file di src/api: un caricamento nuovo senza avanzamento non passa.
 *
 * `onProgress({ loaded, total, percent })`: `percent` e' null quando il
 * server non dice la dimensione (niente Content-Length): la barra mostra
 * allora i byte trasferiti senza percentuale.
 */

const bytes = (v) => {
  const n = Number(v);
  return Number.isFinite(n) && n > 0 ? n : 0;
};

const toProgress = (onProgress) => (event) => {
  const loaded = bytes(event?.loaded);
  const total = bytes(event?.total);
  onProgress({ loaded, total: total || null, percent: total ? Math.min(100, Math.round((loaded * 100) / total)) : null });
};

/** Configurazione axios per l'avanzamento di un caricamento. */
export const uploadProgress = (onProgress) => (onProgress ? { onUploadProgress: toProgress(onProgress) } : {});

/** Configurazione axios per l'avanzamento di uno scaricamento. */
export const downloadProgress = (onProgress) => (onProgress ? { onDownloadProgress: toProgress(onProgress) } : {});
