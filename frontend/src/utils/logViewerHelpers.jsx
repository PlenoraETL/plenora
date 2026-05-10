/**
 * logViewerHelpers.js — Helper puri di formattazione per la log view.
 *
 * Funzioni pure estratte per essere testabili in isolamento senza
 * montare il componente.
 *
 * Importato da: pages/LogViewer.jsx, components/logviewer/RunsTab.jsx,
 * components/logviewer/RunDetail.jsx.
 */
/**
 * Formatta una durata in millisecondi in stringa leggibile.
 * @param {number|undefined} ms
 * @returns {string}
 */
export function fmtDuration(ms) {
  if (!ms) return '—';
  if (ms < 1000) return `${ms}ms`;
  if (ms < 60000) return `${(ms / 1000).toFixed(1)}s`;
  return `${Math.floor(ms / 60000)}m ${Math.round((ms % 60000) / 1000)}s`;
}

/**
 * Formatta un numero di righe con separatore migliaia (locale it-IT).
 * @param {number|undefined} n
 * @returns {string}
 */
export function fmtRows(n) {
  return n ? n.toLocaleString('it-IT') : '—';
}

/**
 * Formatta un timestamp ISO in formato leggibile MM-DD HH:mm:ss.
 * @param {string|undefined} ts
 * @returns {string}
 */
export function formatTime(ts) {
  if (!ts) return '—';
  const parts = ts.split('T');
  if (parts.length !== 2) return ts;
  return `${parts[0].slice(5)} ${parts[1].slice(0, 8)}`;
}

/**
 * Tronca un project ID lungo a 18 caratteri + ellipsis.
 * @param {string|undefined} id
 * @returns {string|undefined}
 */
export function truncateProjectId(id) {
  if (!id || id.length <= 20) return id;
  return id.slice(0, 18) + '…';
}

