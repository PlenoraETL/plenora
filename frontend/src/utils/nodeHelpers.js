/**
 * nodeHelpers.js — Helper condivisi per la lettura dei nodi canvas
 *
 * Helper condivisi da canvasSerializer.js e nodeFactories.js.
 *
 * Importato da:
 *   - utils/canvasSerializer.js
 *   - utils/nodeFactories.js
 */

/**
 * Legge un campo prima da node.data, poi direttamente dal nodo, con fallback.
 * Necessario perché il backend può salvare campi sia dentro che fuori da data.
 *
 * @param {Object} node  - Nodo raw
 * @param {string} key   - Nome del campo
 * @param {*}      def   - Valore di default
 * @returns {*}
 */
export const nd = (node, key, def = undefined) => {
  const fromData = node.data?.[key];
  if (fromData !== undefined && fromData !== null) return fromData;
  const fromNode = node[key];
  if (fromNode !== undefined && fromNode !== null) return fromNode;
  return def;
};
