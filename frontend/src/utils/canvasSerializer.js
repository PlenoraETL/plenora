import { canonicalNodeType, persistedNodeData, persistenceValue } from './nodeTypeRegistry';
import {
  dataFlowEdges,
  isParameterBindingEdge,
  serializedBindingsForInput,
} from './runtimeBindingEdges';


function serializeNode(node, edges, allNodes) {
  const type = canonicalNodeType(node.type);
  const data = persistedNodeData(node);
  if (type === 'result') data.columns = _computeResultColumns(node, allNodes, edges);
  if (type === 'project_input') {
    data.bindings = serializedBindingsForInput({ ...node, data }, edges);
  }
  if (type === 'comment') {
    // React Flow v12: il NodeResizer scrive node.width/height (non piu' lo style).
    data.width = node.width ?? node.style?.width ?? data.width;
    data.height = node.height ?? node.style?.height ?? data.height;
  }
  if (type === 'multi_join' && node.data?.config) {
    const cfg = node.data.config;
    const pairs = (cfg.join_pairs ?? data.join_pairs).filter(p => p && p.left && p.right)
      .map(p => ({ left: p.left, right: p.right }));
    data.join_keys = [...new Set([...(cfg.join_keys ?? data.join_keys),
      ...pairs.filter(p => p.left === p.right).map(p => p.left)])];
    data.join_key_ids = cfg.join_key_ids ?? data.join_key_ids;
    data.join_pairs = pairs;
    data.left_keys = pairs.length ? pairs.map(p => p.left) : (cfg.left_keys ?? data.left_keys);
    data.right_keys = pairs.length ? pairs.map(p => p.right) : (cfg.right_keys ?? data.right_keys);
    data.multi_join_type = cfg.join_type ?? data.multi_join_type;
  }
  return persistenceValue({ id: node.id, type, position: node.position, ...data });
}

export function serializeEdge(edge) {
  if (isParameterBindingEdge(edge)) return null;
  if (!edge.data || !edge.data.join_type) return edge;
  return {
    id: edge.id, source: edge.source, target: edge.target,
    join_type:          edge.data.join_type          || 'left',
    source_column:      edge.data.source_column      || '',
    target_column:      edge.data.target_column      || '',
    join_conditions:    edge.data.join_conditions    || null,
    on_filters:         edge.data.on_filters         || null,
    is_spatial:         edge.data.is_spatial         || false,
    spatial_predicate:  edge.data.spatial_predicate  || null,
    source_geom_column: edge.data.source_geom_column || null,
    target_geom_column: edge.data.target_geom_column || null,
    spatial_distance:   edge.data.spatial_distance   ?? null,
  };
}

export function serializeCanvas(nodes, edges, _options = {}) {
  return {
    nodes: nodes.map(node => serializeNode(node, edges, nodes)),
    edges: edges.map(serializeEdge).filter(Boolean),
  };
}

function _computeResultColumns(resultNode, allNodes, edges) {
  // Fonte di verità: colonne del nodo upstream collegato al result.
  // Il vecchio comportamento privilegiava `resultNode.data.columns` anche quando stale,
  // persistendo colonne obsolete dopo modifiche al grafo (UI mostrava N colonne propagate
  // live via useMemo ma lo state del nodo restava fermo a uno stato precedente).
  //
  // C9 (backend): il server ricomputa autorevolmente result_node.columns
  // al PUT /canvas via SchemaInferenceService (vedi
  // backend/app/shared/canvas_columns.py). Quindi il valore calcolato qui
  // è best-effort per la UX live pre-save; quello che arriva al backend
  // può essere anche vuoto o stale — viene comunque sovrascritto lato
  // server con la BFS backward che copre tutti i node type
  // (source/transform/api_enrich/merge/join). Al reload la UI legge il
  // valore corretto dal canvas persistito.
  const saved = resultNode.data?.columns || [];
  const incomingEdge = dataFlowEdges(edges).find(e => e.target === resultNode.id);
  if (!incomingEdge) return saved;
  const sourceNode = allNodes.find(n => n.id === incomingEdge.source);
  const upstream = sourceNode?.data?.columns || [];
  if (upstream.length === 0) return saved;
  // Merge: sincronizza nomi/tipi con l'upstream (rimuove colonne stale, aggiunge nuove)
  // ma preserva customizzazioni utente (alias, visible) del data.columns salvato.
  const savedByName = new Map(saved.map(c => [c.name, c]));
  return upstream.map(col => {
    const s = savedByName.get(col.name);
    return s ? { ...col, alias: s.alias ?? null, visible: s.visible ?? true } : col;
  });
}
