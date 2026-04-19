import { nd } from './nodeHelpers';

/**
 * nodeFactories.js — Factory e deserializzatori per nodi ReactFlow
 *
 * Unica fonte di verità per ricostruire i nodi a partire dai dati
 * serializzati dal backend. Usato da tutti e tre gli editor
 * (EstraiEditor, ManipolaEditor, ArricchisciEditor).
 *
 * Struttura:
 *   - createEdgeFromData      → ricostruisce un edge
 *   - createTableNode         → nodo tabella DB (solo Estrai)
 *   - createFeatureLayerNode  → nodo layer ArcGIS (solo Estrai)
 *   - createResultNode        → nodo output (tutti e tre gli editor)
 *   - createCaricanNode       → nodo scrittura su DB/ArcGIS
 *   - createStaticValuesNode  → nodo valori statici
 *   - createCommentNode       → nodo annotazione
 *   - createDataSourceNode    → nodo datasource file/cartella
 *   - createProjectSourceNode → nodo sorgente progetto (Manipola + Arricchisci)
 *   - createFileSourceNode    → nodo sorgente file (Manipola + Arricchisci)
 *   - createTransformNode     → nodo trasformazione (solo Manipola)
 *   - createApiEnrichNode     → nodo arricchimento API (solo Arricchisci)
 *   - deserializeNodes        → dispatcher: mappa array nodi raw → ReactFlow nodes
 *
 * Convenzione callbacks:
 *   Le funzioni che accettano un parametro `callbacks` si aspettano un oggetto
 *   con le callback specifiche dell'editor. Tutte le callback sono opzionali —
 *   se assenti il nodo viene renderizzato in sola lettura.
 *
 * Uso:
 *   import { deserializeNodes, createEdgeFromData } from '../utils/nodeFactories';
 *
 *   const nodes = deserializeNodes(canvas.nodes, { connection, connections, callbacks });
 *   const edges = canvas.edges.map(e => createEdgeFromData(e, nodes));
 */

// =============================================================================
// HELPER INTERNO
// =============================================================================

// =============================================================================
// EDGE
// =============================================================================

/**
 * Ricostruisce un oggetto edge ReactFlow a partire dai dati serializzati.
 *
 * @param {Object} edge        - Dati edge dal backend (canvas.edges[i])
 * @param {Array}  loadedNodes - Nodi già caricati nel canvas
 * @returns {Object} Edge pronto per ReactFlow
 */
export function createEdgeFromData(edge, loadedNodes) {
  const targetNode = loadedNodes.find(n => n.id === edge.target);
  const isSpatial  = edge.is_spatial || false;

  let label, strokeColor;

  if (targetNode?.type === 'resultNode' || targetNode?.type === 'result') {
    label       = 'output';
    strokeColor = '#4CAF50';
  } else if (targetNode?.type === 'carica') {
    label       = 'Carica DB';
    strokeColor = '#7c3aed';
  } else if (targetNode?.type === 'caricaArcgis') {
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
    data: {
      join_type:          edge.join_type          || 'left',
      source_column:      edge.source_column      || '',
      target_column:      edge.target_column      || '',
      join_conditions:    edge.join_conditions    || null,
      on_filters:         edge.on_filters         || null,
      is_spatial:         edge.is_spatial         || false,
      spatial_predicate:  edge.spatial_predicate  || null,
      source_geom_column: edge.source_geom_column || null,
      target_geom_column: edge.target_geom_column || null,
      spatial_distance:   edge.spatial_distance   || null,
    },
  };
}

// =============================================================================
// NODI ESTRAI
// =============================================================================

/**
 * Crea un nodo tabella DB da dati backend.
 *
 * @param {Object} node       - Dati nodo dal backend
 * @param {Object} connection - Connessione DB attiva del progetto (può essere null)
 * @returns {Object} Nodo ReactFlow type='tableNode'
 */
export function createTableNode(node, connection = null) {
  return {
    id:       node.id,
    type:     'tableNode',
    position: node.position,
    data: {
      label:               nd(node, 'alias') || nd(node, 'table_name'),
      table_name:          nd(node, 'table_name'),
      alias:               nd(node, 'alias', null),
      columns:             nd(node, 'columns', []),
      filters:             nd(node, 'filters', []),
      calculated_columns:  nd(node, 'calculated_columns', []),
      db_type:             connection?.type || 'postgres',
    },
  };
}

/**
 * Crea un nodo Feature Layer ArcGIS da dati backend.
 *
 * onUpdateWhere e onAddRelatedLayer vengono ora dallo store Zustand.
 *
 * @param {Object} node - Dati nodo dal backend
 * @returns {Object} Nodo ReactFlow type='featureLayerNode'
 */
export function createFeatureLayerNode(node) {

  const columns = nd(node, 'columns', []).map(col => ({
    name:    col.name,
    type:    col.type || 'str',
    alias:   col.alias ?? null,
    visible: col.visible !== false,
  }));

  return {
    id:       node.id,
    type:     'featureLayerNode',
    position: node.position,
    data: {
      type:             'feature_source',
      layer_name:       nd(node, 'layer_name') || nd(node, 'table_name') || 'Layer',
      layer_url:        nd(node, 'layer_url', ''),
      connection_id:    nd(node, 'connection_id', ''),
      connection_name:  nd(node, 'connection_name', ''),
      service_name:     nd(node, 'service_name', ''),
      geometry_type:    nd(node, 'geometry_type', ''),
      feature_count:    nd(node, 'feature_count', null),
      where:            nd(node, 'where', ''),
      relationships:    nd(node, 'relationships', []),
      columns,
    },
  };
}

// =============================================================================
// NODI CONDIVISI (tutti e tre gli editor)
// =============================================================================

/**
 * Crea il nodo Output/Result da dati backend.
 * Tutti gli handler vivono nello store Zustand — non servono callback.
 *
 * @param {Object} node    - Dati nodo dal backend
 * @param {Object} options - { isManipulate, isArricchisci }
 * @returns {Object} Nodo ReactFlow type='resultNode'
 */
export function createResultNode(node, options = {}) {
  const { isManipulate = false, isArricchisci = false } = options;
  const nodeType = (isManipulate || isArricchisci) ? 'result' : 'resultNode';

  return {
    id:       node.id,
    type:     nodeType,
    position: node.position,
    data: {
      fileName:         nd(node, 'fileName', 'output'),
      format:           nd(node, 'format', 'csv'),
      distinct:         nd(node, 'distinct', false),
      limit:            nd(node, 'limit', null),
      geomColumn:       nd(node, 'geomColumn', null),
      columns:          nd(node, 'columns', []),
      group_by_enabled:  nd(node, 'group_by_enabled', false),
      group_by_columns:  nd(node, 'group_by_columns', []),
      aggregations:      nd(node, 'aggregations', []),
      order_by_enabled:  nd(node, 'order_by_enabled', false),
      order_by_columns:  nd(node, 'order_by_columns', []),
      having_enabled:    nd(node, 'having_enabled', false),
      having_conditions: nd(node, 'having_conditions', []),
      availableColumns: [],
      isManipulate,
      isArricchisci,
      // Modalita' "Pubblicazione IMDF" — campi dormienti finche' non
      // viene cablato il publisher node (saranno usati per leggere/salvare
      // la config ArcGIS associata al result in modalita' IMDF).
      imdf_arcgis_connection_id: nd(node, 'imdf_arcgis_connection_id', null),
      imdf_arcgis_folder:        nd(node, 'imdf_arcgis_folder',        null),
      imdf_fs_name_template:     nd(node, 'imdf_fs_name_template',     null),
      imdf_apply_renderers:      nd(node, 'imdf_apply_renderers',      false),
    },
  };
}

/**
 * Crea il nodo CARICA DB (scrittura su Database) da dati backend.
 *
 * @param {Object} node - Dati nodo dal backend
 * @returns {Object} Nodo ReactFlow type='carica'
 */
export function createCaricanNode(node) {

  return {
    id:       node.id,
    type:     'carica',
    position: node.position,
    data: {
      connection_id:           nd(node, 'connection_id', ''),
      schema_name:             nd(node, 'schema_name', ''),
      table_name:              nd(node, 'table_name', ''),
      write_mode:              nd(node, 'write_mode', 'create'),
      column_mappings:         nd(node, 'column_mappings', []),
      match_columns:           nd(node, 'match_columns', []),
      batch_size:              nd(node, 'batch_size', 1000),
      transaction_mode:        nd(node, 'transaction_mode', 'all_or_nothing'),
      create_spatial_index:    nd(node, 'create_spatial_index') !== false,
      conflict_policy:         nd(node, 'conflict_policy', 'abort'),
      availableColumns: [],
    },
  };
}

/**
 * Crea il nodo CARICA ArcGIS (scrittura su Feature Service) da dati backend.
 *
 * @param {Object} node - Dati nodo dal backend
 * @returns {Object} Nodo ReactFlow type='caricaArcgis'
 */
export function createCaricanArcGISNode(node) {

  return {
    id:       node.id,
    type:     'caricaArcgis',
    position: node.position,
    data: {
      arcgis_connection_id:    nd(node, 'arcgis_connection_id', ''),
      arcgis_service_url:      nd(node, 'arcgis_service_url', ''),
      arcgis_layer_url:        nd(node, 'arcgis_layer_url', ''),
      arcgis_operation:        nd(node, 'arcgis_operation', 'add'),
      arcgis_folder_id:        nd(node, 'arcgis_folder_id', ''),
      arcgis_match_column:     nd(node, 'arcgis_match_column', ''),
      arcgis_new_service_name: nd(node, 'arcgis_new_service_name', ''),
      arcgis_new_layer_name:   nd(node, 'arcgis_new_layer_name', 'Layer 0'),
      column_mappings:         nd(node, 'column_mappings', []),
      availableColumns: [],
    },
  };
}

/**
 * Crea un nodo IMDF Publisher da dati backend.
 * Il nodo pubblica il bundle IMDF prodotto dall'upstream `api_enrich`
 * (Processor File con output_kind='imdf') su una connessione ArcGIS,
 * estraendo il GDB e creando/rimpiazzando un Feature Service.
 *
 * @param {Object} node - Dati nodo dal backend
 * @returns {Object} Nodo ReactFlow type='imdf_publisher'
 */
export function createImdfPublisherNode(node) {
  return {
    id:       node.id,
    type:     'imdf_publisher',
    position: node.position,
    data: {
      imdf_arcgis_connection_id: nd(node, 'imdf_arcgis_connection_id', ''),
      imdf_arcgis_folder:        nd(node, 'imdf_arcgis_folder',        ''),
      imdf_fs_name_template:     nd(node, 'imdf_fs_name_template',     ''),
      imdf_apply_renderers:      nd(node, 'imdf_apply_renderers',      false),
      imdf_publish_mode:         nd(node, 'imdf_publish_mode',         'replace'),
    },
  };
}

/**
 * Crea un nodo Valori Statici da dati backend.
 *
 * @param {Object} node - Dati nodo dal backend
 * @returns {Object} Nodo ReactFlow type='staticValues'
 */
export function createStaticValuesNode(node) {
  return {
    id:       node.id,
    type:     'staticValues',
    position: node.position,
    data: {
      label:  nd(node, 'label', 'Valori Statici'),
      fields: nd(node, 'fields', []),
    },
  };
}

/**
 * Crea un nodo Annotazione/Commento da dati backend.
 *
 * @param {Object} node - Dati nodo dal backend
 * @returns {Object} Nodo ReactFlow type='comment'
 */
export function createCommentNode(node) {
  return {
    id:       node.id,
    type:     'comment',
    position: node.position,
    zIndex:   -1,
    style: {
      width:  nd(node, 'width', 250),
      height: nd(node, 'height', 150),
    },
    data: {
      level:      nd(node, 'level', ''),
      label:      nd(node, 'label', ''),
      title:      nd(node, 'title', ''),
      color:      nd(node, 'color', '#fbbf24'),
      arrowStyle: nd(node, 'arrowStyle', null),
    },
  };
}

/**
 * Crea un nodo Data Source (file o cartella, sorgente o destinazione) da dati backend.
 *
 * @param {Object} node - Dati nodo dal backend
 * @returns {Object} Nodo ReactFlow type='datasourceNode'
 */
export function createDataSourceNode(node) {
  return {
    id:       node.id,
    type:     'datasourceNode',
    position: node.position || { x: 100, y: 100 },
    data: {
      mode:            nd(node, 'mode', 'source'),
      datasource_id:   nd(node, 'datasource_id', ''),
      datasource_name: nd(node, 'datasource_name', ''),
      file_mode:       nd(node, 'file_mode', 'fixed'),
      file_path:       nd(node, 'file_path', ''),
      folder_path:     nd(node, 'folder_path', ''),
      dynamic_rule:    nd(node, 'dynamic_rule', 'latest'),
      pattern:         nd(node, 'pattern', ''),
      format:          nd(node, 'format', ''),
      columns:         nd(node, 'columns', []),
      parse_options:   nd(node, 'parse_options', {}),
      dest_folder:     nd(node, 'dest_folder', ''),
      dest_filename:   nd(node, 'dest_filename', ''),
      dest_format:     nd(node, 'dest_format', 'csv'),
    },
  };
}

// =============================================================================
// NODI MANIPOLA + ARRICCHISCI
// =============================================================================

/**
 * Crea un nodo Project Source (sorgente da altro progetto URBY) da dati backend.
 *
 * @param {Object} node - Dati nodo dal backend
 * @returns {Object} Nodo ReactFlow type='projectSource'
 */
export function createProjectSourceNode(node) {
  return {
    ...node,
    data: {
      ...node.data,
      source_project_id: nd(node, 'source_project_id', ''),
      columns:           nd(node, 'columns', []),
    },
  };
}

/**
 * Crea un nodo File Source (sorgente da file precedentemente caricato) da dati backend.
 *
 * @param {Object} node - Dati nodo dal backend
 * @returns {Object} Nodo ReactFlow type='fileSource'
 */
export function createFileSourceNode(node) {
  return {
    ...node,
    data: {
      ...node.data,
      file_source_id: nd(node, 'file_source_id', ''),
      columns:        nd(node, 'columns', []),
    },
  };
}

// =============================================================================
// NODI MANIPOLA
// =============================================================================

/**
 * Crea un nodo Transform (operazione di trasformazione dati) da dati backend.
 *
 * @param {Object} node - Dati nodo dal backend
 * @returns {Object} Nodo ReactFlow type='transform'
 */
export function createTransformNode(node) {
  return {
    ...node,
    data: {
      ...node.data,
      transform_op: nd(node, 'transform_op', 'join'),
      config:       nd(node, 'config', {}),
      collapsed:    nd(node, 'collapsed', false),
      columns:      nd(node, 'columns', []),
    },
  };
}

// =============================================================================
// NODI ARRICCHISCI
// =============================================================================

/**
 * Crea un nodo API Enrichment (chiamata a connettore esterno) da dati backend.
 *
 * @param {Object} node - Dati nodo dal backend
 * @returns {Object} Nodo ReactFlow type='api_enrich'
 */
export function createApiEnrichNode(node) {
  return {
    ...node,
    data: {
      ...node.data,
      api_id:          nd(node, 'api_id', ''),
      api_name:        nd(node, 'api_name', ''),
      param_mapping:   nd(node, 'param_mapping', {}),
      parameters:      nd(node, 'parameters', []),
      output_mapping:  nd(node, 'output_mapping', []),
      enrich_type:     nd(node, 'enrich_type', 'manual'),
      connection_type: nd(node, 'connection_type', null),
      exec_params:     nd(node, 'exec_params', {}),
      endpoint:        nd(node, 'endpoint', null),
      columns:         nd(node, 'columns', []),
      batch:           nd(node, 'batch', {}),
      continue_on_error: nd(node, 'continue_on_error', true),
      skip_null_params:  nd(node, 'skip_null_params', true),
      max_errors:        nd(node, 'max_errors', 0),
      api_type:          nd(node, 'api_type', null),
      static_parameters: nd(node, 'static_parameters', {}),
      // Processore File (rest_file): config multipart in/binary out
      request:           nd(node, 'request',  null),
      response:          nd(node, 'response', null),
      // File aggiuntivi caricati inline (per file_fields extra_input).
      // Formato: {field_name: {file_source_id, original_filename, size}}
      extra_files:       nd(node, 'extra_files', null),
      // Tag che dice al canvas "lo zip prodotto da questo nodo ha
      // questa struttura nota" (es. 'imdf'). Il nodo result downstream
      // legge questo tag per specializzarsi.
      output_kind:       nd(node, 'output_kind', null),
    },
  };
}

// =============================================================================
// DISPATCHER — deserializza un intero array di nodi raw
// =============================================================================

/**
 * Converte un array di nodi raw dal backend in nodi ReactFlow pronti al render.
 *
 * Punto di ingresso principale usato dagli editor. Dopo la Fase 3 del refactoring,
 * tutte le callback e i runtime data (connections, projectId) vivono nello store
 * Zustand — i factory hanno bisogno solo dei dati statici del nodo.
 *
 * @param {Array}  rawNodes               - canvas.nodes dal backend
 * @param {Object} options
 * @param {Object} options.connection      - Connessione DB attiva (per tableNode)
 * @param {Object} options.flags          - Flag di contesto: { isManipulate, isArricchisci }
 *
 * @returns {Array} Nodi ReactFlow pronti per setNodes
 */
export function deserializeNodes(rawNodes, options = {}) {
  const {
    connection = null,
    flags      = {},
  } = options;

  const { isManipulate = false, isArricchisci = false } = flags;

  return rawNodes.map(node => {
    switch (node.type) {

      // ── Estrai ──────────────────────────────────────────────────────────────
      case 'source':
        return createTableNode(node, connection);

      case 'feature_source':
        return createFeatureLayerNode(node);

      // ── Condivisi (tutti gli editor) ────────────────────────────────────────
      case 'result':
        return createResultNode(node, { isManipulate, isArricchisci });

      case 'carica':
        return createCaricanNode(node);

      case 'carica_arcgis':
        return createCaricanArcGISNode(node);

      case 'imdf_publisher':
        return createImdfPublisherNode(node);

      case 'staticValues':
        return createStaticValuesNode(node);

      case 'comment':
        return createCommentNode(node);

      case 'datasource_node':
      case 'datasourceNode':
        return createDataSourceNode(node);

      // ── Manipola + Arricchisci ───────────────────────────────────────────────
      case 'project_source':
        return createProjectSourceNode(node);

      case 'file_source':
        return createFileSourceNode(node);

      // ── Manipola ─────────────────────────────────────────────────────────────
      case 'transform':
        return createTransformNode(node);

      // ── Arricchisci ──────────────────────────────────────────────────────────
      case 'api_enrich':
        return createApiEnrichNode(node);

      default:
        return node;
    }
  });
}
