/**
 * compactNodeTypes — converte la mappa `nodeTypes` di un editor nella
 * versione della nuova interfaccia: ogni tipo diventa un CompactNode,
 * tranne le note (`comment`), che restano libere sul canvas.
 *
 * La mappa originale resta la fonte dei form completi, mostrati nel
 * pannello laterale (NodeInspector).
 */
import CompactNode from './CompactNode';

const KEEP_FULL = new Set(['comment']);

const cache = new WeakMap();

export function compactNodeTypes(fullNodeTypes) {
  if (!fullNodeTypes) return fullNodeTypes;
  if (cache.has(fullNodeTypes)) return cache.get(fullNodeTypes);
  const out = {};
  for (const [type, Full] of Object.entries(fullNodeTypes)) {
    out[type] = KEEP_FULL.has(type) ? Full : CompactNode;
  }
  cache.set(fullNodeTypes, out);
  return out;
}
