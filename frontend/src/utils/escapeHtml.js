/**
 * escapeHtml.js — l'unica funzione di escape per i sink HTML (audit P1, R9-N5).
 *
 * Regola (tests/shared/htmlSinks.contract.test.js): un sink che interpreta
 * HTML (innerHTML, insertAdjacentHTML, dangerouslySetInnerHTML, popup e
 * tooltip Leaflet, L.divIcon({ html }), setHTML di maplibre) riceve solo un
 * nodo DOM costruito con textContent oppure un valore passato da qui.
 * Preferire sempre i nodi DOM: questa funzione e' il ripiego per le API che
 * accettano solo stringhe.
 */
const ENTITIES = {
  '&': '&amp;',
  '<': '&lt;',
  '>': '&gt;',
  '"': '&quot;',
  "'": '&#39;',
  '`': '&#96;',
};

export function escapeHtml(value) {
  return String(value ?? '').replace(/[&<>"'`]/g, (ch) => ENTITIES[ch]);
}
