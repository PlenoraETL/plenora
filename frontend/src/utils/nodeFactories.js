import { nd, nodeTopLevelData } from './nodeHelpers';
import { NODE_TYPES, RESOURCE_COPY_KEYS, TRANSIENT_DATA_KEYS, canonicalNodeType, joinEdgeFields, persistedNodeData } from './nodeTypeRegistry';

export function createEdgeFromData(edge, loadedNodes) {
  const targetNode = loadedNodes.find(n => n.id === edge.target);
  const isSpatial  = edge.is_spatial || false;

  let label, strokeColor;

  if (canonicalNodeType(targetNode?.type) === 'result') {
    label       = 'output';
    strokeColor = '#4CAF50';
  } else if (targetNode?.type === 'carica') {
    label       = 'Carica DB';
    strokeColor = '#7c3aed';
  } else if (canonicalNodeType(targetNode?.type) === 'carica_arcgis') {
    label       = 'Carica ArcGIS';
    strokeColor = '#2DD4BF';
  } else if (isSpatial) {
    label       = `🌐 ${edge.spatial_predicate?.replace('ST_', '') || 'spatial'}`;
    strokeColor = '#E91E63';
  } else {
    label       = `${edge.join_type || 'left'} join`;
    strokeColor = '#2196F3';
  }

  return {
    id:       edge.id,
    source:   edge.source,
    target:   edge.target,
    label,
    animated: true,
    style:    { stroke: strokeColor, strokeWidth: 2 },
    data: joinEdgeFields(edge),
  };
}

/** Rehydrate registered fields, then attach editor-only presentation data. */
export function deserializeNodes(rawNodes, options = {}) {
  const { connection = null, flags = {} } = options;
  return rawNodes.map(raw => {
    // Il serializer appiattisce node.data al primo livello del nodo salvato:
    // qui si fa l'inverso per TUTTI i campi (nodeTopLevelData), non solo per
    // quelli registrati. Prima un campo non registrato (es. output_columns
    // dell'Output) si perdeva a ogni riapertura.
    const node = { ...raw, data: { ...nodeTopLevelData(raw), ...(raw.data || {}) } };
    const type = canonicalNodeType(node.type);
    const entry = NODE_TYPES[type];
    if (!entry) return { ...node, data: { ...node.data, ...persistedNodeData(node) } };
    const data = persistedNodeData(node);
    const result = { ...node, type: entry.reactFlowType, data };
    switch (type) {
      case 'source':
        data.db_type = connection?.type || 'postgres';
        break;
      case 'feature_source':
        data.type = type;
        data.layer_name = nd(node, 'layer_name', nd(node, 'table_name', data.layer_name));
        data.columns = data.columns.map(col => ({
          ...col, type: col.type ?? 'str', alias: col.alias ?? null,
          visible: col.visible !== false,
        }));
        break;
      case 'result':
        data.isManipulate = flags.isManipulate || false;
        data.isArricchisci = flags.isArricchisci || false;
        if (data.isManipulate || data.isArricchisci) result.type = type;
        break;
      case 'comment':
        result.zIndex = -1;
        result.style = { width: data.width, height: data.height };
        // React Flow v12: dimensioni esplicite sul nodo (il NodeResizer aggiorna queste).
        if (data.width != null) result.width = data.width;
        if (data.height != null) result.height = data.height;
        break;
      case 'datasource_node':
        result.position = node.position || { x: 100, y: 100 };
        break;
      case 'multi_join':
        data.config = {
          join_keys: data.join_keys, join_key_ids: data.join_key_ids,
          join_pairs: data.join_pairs, left_keys: data.left_keys,
          right_keys: data.right_keys, join_type: data.multi_join_type,
          ...node.data?.config,
        };
        break;
      case 'api_enrich':
        // Preserve the public factory's forward-compatible UI extras, but only
        // registered fields may cross the persistence boundary.
        result.data = { ...nodeTopLevelData(node), ...node.data, ...data };
        // Colonne derivate: dalla fonte unica, mai nel nodo (regola 4); i
        // campi transitori (inputColumns, availableColumns...) nemmeno.
        delete result.data.columns;
        for (const key of TRANSIENT_DATA_KEYS) delete result.data[key];
        // Copie della connessione (R2.10, N11): il pannello le legge dalla risorsa.
        for (const key of RESOURCE_COPY_KEYS.api_enrich) delete result.data[key];
        break;
      default:
        break;
    }
    return result;
  });
}
