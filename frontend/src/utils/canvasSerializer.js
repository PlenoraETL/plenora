import { DERIVED_COLUMNS_TYPES, canonicalNodeType, persistedNodeData, persistenceValue } from './nodeTypeRegistry';
import {
  isParameterBindingEdge,
  serializedBindingsForInput,
} from './runtimeBindingEdges';

// Campi delle colonne previste allegati al salvataggio (forma di columnSource).
const SAVED_COLUMN_KEYS = ['id', 'name', 'type', 'source_name', 'source_node', 'is_calculated', 'optional'];
const savedColumn = (c) => Object.fromEntries(SAVED_COLUMN_KEYS
  .filter(key => c[key] !== null && c[key] !== undefined).map(key => [key, c[key]]));

function serializeNode(node, edges, derivedColumns) {
  const type = canonicalNodeType(node.type);
  const data = persistedNodeData(node);
  // Colonne DERIVATE (Blocco A, regola 4): non sono nel nodo. Al salvataggio
  // si allegano dalla mappa del server (store/useColumnStore), perche' il
  // backend le legge ancora: mappa dei tipi al salvataggio dei file
  // (canvas_schema_runtime.build_canvas_type_map, estrai _build_canvas_type_map)
  // e controllo della geometria (canvas_validation). Le colonne dell'Output le
  // ricalcola comunque il server al PUT (canvas_columns.compute_result_columns).
  // Senza mappa (inferenza, impronta del contenuto) non si inviano.
  if (DERIVED_COLUMNS_TYPES.has(type) && derivedColumns?.[node.id]) {
    data.columns = derivedColumns[node.id].map(savedColumn);
  }
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

/**
 * `options.derivedColumns` (solo per il salvataggio): mappa nodo -> colonne
 * previste dal server, allegate ai nodi con colonne derivate.
 */
export function serializeCanvas(nodes, edges, options = {}) {
  return {
    nodes: nodes.map(node => serializeNode(node, edges, options.derivedColumns)),
    edges: edges.map(serializeEdge).filter(Boolean),
  };
}
