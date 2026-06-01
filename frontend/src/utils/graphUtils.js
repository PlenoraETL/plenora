/**
 * graphUtils.js — Utility pure condivise per la gestione dei grafi canvas
 * (detectCycle, getNodeName). Erano usate dai validatori canvas per-famiglia
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
  edges.forEach(e => {
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

/**
 * Restituisce il nome leggibile di un nodo per i messaggi di validazione.
 * Versione unificata — copre tutti i tipi nodo di Estrai e Manipola.
 *
 * @param {Object} node
 * @param {Object|null} registry - Registry trasformazioni (opzionale, per nodi transform)
 * @returns {string}
 */
export function getNodeName(node, registry = null) {
  if (!node) return '?';
  switch (node.type) {
    // Estrai
    case 'tableNode':        return node.data?.alias || node.data?.table_name || 'Tabella';
    case 'datasourceNode':
    case 'datasource_node':  return node.data?.alias || node.data?.name || 'DataSource';
    case 'featureLayerNode': return node.data?.alias || node.data?.name || 'Feature Layer';
    case 'resultNode':       return 'Output';
    // Manipola + condivisi
    case 'project_source':   return node.data?.alias || node.data?.table_name || 'Sorgente progetto';
    case 'file_source':      return node.data?.fileName || node.data?.alias || 'File sorgente';
    case 'staticValues':     return node.data?.alias || 'Valori statici';
    case 'result':           return 'Output';
    case 'carica':
      if (node.data?.dest_type === 'arcgis') return 'Carica ArcGIS';
      if (node.data?.dest_type === 'rest') return 'Carica REST';
      return 'Carica DB';
    case 'transform': {
      const opId = node.data?.transform_op;
      if (opId && registry && registry[opId]) return registry[opId].name || opId;
      return opId || 'Trasformazione';
    }
    case 'multi_join':       return 'Multi-Join';
    default:                 return node.type || node.id;
  }
}
