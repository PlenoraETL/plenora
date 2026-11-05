/**
 * downloadBlob.js — IL punto che consegna un file al browser (Fase 5 T2,
 * audit R9.14, R9-N3, P23).
 *
 * Regola: l'URL del blob si revoca DOPO (REVOKE_DELAY_MS), mai subito dopo
 * click(): alcuni browser leggono il link in modo asincrono e il download
 * fallirebbe. Il test tests/shared/asyncArchitecture.contract.test.js vieta
 * createObjectURL/revokeObjectURL fuori da questo modulo.
 *
 * Gli errori della richiesta li mostra chi scarica (hooks/useDownload).
 */

export const REVOKE_DELAY_MS = 60_000;

/** Nome del file dal Content-Disposition, se c'e'. */
export function filenameFromDisposition(disposition) {
  const match = String(disposition || '').match(/filename="?([^";\n]+)"?/i);
  return match ? match[1].replace(/[/\\]/g, '_') : null;
}

const revokeLater = (url) => setTimeout(() => window.URL.revokeObjectURL(url), REVOKE_DELAY_MS);

/** Salva un Blob con un nome. */
export function saveBlob(blob, filename = 'download') {
  const url = window.URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = url;
  link.download = filename || 'download';
  document.body.appendChild(link);
  link.click();
  document.body.removeChild(link);
  revokeLater(url);
}

/** Apre un Blob in una nuova scheda (allegati inline). */
export function openBlob(blob) {
  const url = window.URL.createObjectURL(blob);
  window.open(url, '_blank', 'noopener,noreferrer');
  revokeLater(url);
}

/**
 * Scarica la risposta axios di una richiesta con responseType 'blob'.
 *
 * @param {AxiosResponse} response
 * @param {string|null}   filename     - Nome suggerito (sovrascritto dal Content-Disposition)
 * @param {string}        fallbackName - Nome se non ce n'e' nessuno
 */
export function downloadBlob(response, filename = null, fallbackName = 'download') {
  let downloadFilename = filenameFromDisposition(response.headers?.['content-disposition']) || filename;

  // Correggi estensione se il backend manda uno ZIP ma il filename dice altro
  const contentType = response.headers?.['content-type'] || 'application/octet-stream';
  if (contentType.includes('zip') && downloadFilename && !downloadFilename.endsWith('.zip')) {
    downloadFilename = downloadFilename.replace(/\.[^.]+$/, '') + '.zip';
  }

  saveBlob(new Blob([response.data], { type: contentType }), downloadFilename || fallbackName);
}
