/**
 * downloadBlob.js — Utility condivisa per il download di file via axios
 *
 * Estratta da projects.js, tasks.js e schedules.js dove era triplicata.
 *
 * Importato da:
 *   - api/projects.js
 *   - api/tasks.js
 *   - api/schedules.js
 */

/**
 * Scarica un file Blob nel browser creando un link temporaneo.
 *
 * @param {AxiosResponse} response     - Response axios con responseType: 'blob'
 * @param {string|null}   filename     - Nome file suggerito (sovrascrivibile da Content-Disposition)
 * @param {string}        fallbackName - Nome usato se né filename né header sono presenti
 */
export function downloadBlob(response, filename = null, fallbackName = 'download') {
  let downloadFilename = filename;

  // Leggi il nome dal Content-Disposition se disponibile
  const contentDisposition = response.headers['content-disposition'];
  if (contentDisposition) {
    const match = contentDisposition.match(/filename="?([^";\n]+)"?/);
    if (match) downloadFilename = match[1].replace(/[/\\]/g, '_');
  }

  // Correggi estensione se il backend manda uno ZIP ma il filename dice altro
  const contentType = response.headers['content-type'] || 'application/octet-stream';
  if (contentType.includes('zip') && downloadFilename && !downloadFilename.endsWith('.zip')) {
    downloadFilename = downloadFilename.replace(/\.[^.]+$/, '') + '.zip';
  }

  const blob = new Blob([response.data], { type: contentType });
  const url  = window.URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href     = url;
  link.download = downloadFilename || fallbackName;
  document.body.appendChild(link);
  link.click();
  document.body.removeChild(link);
  window.URL.revokeObjectURL(url);
}
