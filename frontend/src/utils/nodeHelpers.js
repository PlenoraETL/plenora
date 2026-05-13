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


// Chiavi che il backend NON deve "rovesciare" in `node.data`: sono
// proprieta` di trasporto / runtime di ReactFlow oppure il `data` stesso
// (che ricorrerebbe). Pattern simmetrico al `_NODE_KNOWN_VISUAL_EXTRAS`
// del Pydantic Node lato backend.
const _NODE_TOP_LEVEL_KEYS_NOT_IN_DATA = new Set([
  'id', 'type', 'position', 'data', 'style', 'width', 'height',
  // ReactFlow runtime decorations
  'selected', 'dragging', 'measured', 'positionAbsolute', 'zIndex',
  'sourcePosition', 'targetPosition',
]);

/**
 * Ribalta i campi top-level di un node raw dentro un oggetto piatto
 * adatto a essere usato come `node.data`. Skippa le chiavi
 * ReactFlow-runtime e le chiavi con valore null/undefined (cosi` un
 * default piu` permissivo del factory non viene sovrascritto).
 *
 * Sostituisce la copia manuale `field: nd(node, 'field', default)`
 * ripetuta per N campi nei factory: aggiungere un nuovo campo
 * top-level al Pydantic Node lato BE non richiede piu` di
 * aggiornare anche il factory FE per vederlo nel form.
 *
 * @param {Object} node - Nodo raw dal backend
 * @returns {Object} Hash dei campi top-level del node pronti a essere
 *                   spreadati dentro `data`.
 */
export function nodeTopLevelData(node) {
  const out = {};
  if (!node || typeof node !== 'object') return out;
  for (const key of Object.keys(node)) {
    if (_NODE_TOP_LEVEL_KEYS_NOT_IN_DATA.has(key)) continue;
    const val = node[key];
    if (val === undefined || val === null) continue;
    out[key] = val;
  }
  return out;
}
