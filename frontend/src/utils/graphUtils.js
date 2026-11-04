import { dataFlowEdges } from './runtimeBindingEdges';

/**
 * graphUtils.js — Utility pure condivise per la gestione dei grafi canvas
 * (detectCycle). Erano usate dai validatori canvas per-famiglia
 * (rimossi con la migrazione a validazione backend, Epic CanvasVal §12);
 * restano qui come utility di grafo generiche e testate (graphUtils.contract).
 */

/**
 * Rileva cicli nel grafo tramite DFS con colorazione (bianco/grigio/nero).
 * @param {Array} nodes
 * @param {Array} edges
 * @returns {boolean} true se esiste almeno un ciclo
 */
export function detectCycle(nodes, edges) {
  const adjacency = new Map();
  nodes.forEach(n => adjacency.set(n.id, []));
  dataFlowEdges(edges).forEach(e => {
    if (adjacency.has(e.source)) adjacency.get(e.source).push(e.target);
  });
  const colors = new Map();
  nodes.forEach(n => colors.set(n.id, 0));

  function dfs(nodeId) {
    colors.set(nodeId, 1);
    for (const neighbor of (adjacency.get(nodeId) || [])) {
      if (colors.get(neighbor) === 1) return true;
      if (colors.get(neighbor) === 0 && dfs(neighbor)) return true;
    }
    colors.set(nodeId, 2);
    return false;
  }

  for (const node of nodes) {
    if (colors.get(node.id) === 0 && dfs(node.id)) return true;
  }
  return false;
}

